from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from reg.run import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REGISTER_CONFIG,
    check_cloud_and_refill,
    fetch_cloud_account_summary,
    test_openai_proxy,
    run_local_register_job,
)
from reg.runtime import ENV_FILE, OUTPUT_DIR, REGISTER_CONFIG_FILE, SETTINGS_FILE, ensure_runtime_environment


DEFAULT_WEB_SETTINGS: dict[str, Any] = {
    "output_dir": str(DEFAULT_OUTPUT_DIR),
    "count": 20,
    "threads": 3,
    "proxy": "",
    "server": "",
    "auth_key": "",
    "min_active_accounts": 60,
    "monitor_interval_seconds": 300,
    "upload_to_cloud": True,
    "enable_flaresolverr": False,
    "flaresolverr_url": "",
}


def _timestamp_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _safe_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _infer_level(text: str) -> str:
    lowered = str(text or "").lower()
    if any(keyword in lowered for keyword in ("失败", "错误", "error", "exception", "traceback")):
        return "error"
    if any(keyword in lowered for keyword in ("成功", "完成", "已保存", "已开启", "导入完成")):
        return "success"
    if any(keyword in lowered for keyword in ("跳过", "提示", "warning", "暂停", "拦截", "超时")):
        return "warning"
    if any(keyword in lowered for keyword in ("检查", "监控", "启动", "导入", "开始")):
        return "debug"
    return "info"


class RegWebManager:
    def __init__(self) -> None:
        ensure_runtime_environment()
        self._state_lock = threading.Lock()
        self._busy = False
        self._monitoring = False
        self._current_task = ""
        self._current_hint = "可以开始本地注册、导入云端或开启自动补号监控"
        self._monitor_stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._next_check_at = 0.0
        self._progress = self._empty_progress()
        self._logs: list[dict[str, Any]] = []
        self._log_cursor = 0
        self._max_logs = 4000

    @staticmethod
    def _empty_progress() -> dict[str, int]:
        return {
            "total": 0,
            "submitted": 0,
            "done": 0,
            "success": 0,
            "fail": 0,
            "running": 0,
        }

    def _load_settings(self) -> dict[str, Any]:
        payload = dict(DEFAULT_WEB_SETTINGS)
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            payload.update(raw)
        if not str(payload.get("proxy") or "").strip():
            payload["proxy"] = self._load_register_proxy()
        payload["output_dir"] = str(payload.get("output_dir") or DEFAULT_OUTPUT_DIR)
        payload["count"] = _safe_int(payload.get("count"), 20, 1)
        payload["threads"] = _safe_int(payload.get("threads"), 3, 1)
        payload["min_active_accounts"] = _safe_int(payload.get("min_active_accounts"), 60, 1)
        payload["monitor_interval_seconds"] = _safe_int(payload.get("monitor_interval_seconds"), 300, 5)
        payload["upload_to_cloud"] = _safe_bool(payload.get("upload_to_cloud"), True)
        payload["enable_flaresolverr"] = _safe_bool(payload.get("enable_flaresolverr"), False)
        payload["server"] = str(payload.get("server") or "").strip()
        payload["auth_key"] = str(payload.get("auth_key") or "").strip()
        payload["flaresolverr_url"] = str(payload.get("flaresolverr_url") or "").strip()
        payload["proxy"] = str(payload.get("proxy") or "").strip()
        return payload

    def _load_register_proxy(self) -> str:
        try:
            raw = json.loads(REGISTER_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return ""
        if isinstance(raw, dict):
            return str(raw.get("proxy") or "").strip()
        return ""

    def _save_settings_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(DEFAULT_WEB_SETTINGS)
        normalized.update(payload)
        normalized["output_dir"] = str(normalized.get("output_dir") or DEFAULT_OUTPUT_DIR).strip() or str(DEFAULT_OUTPUT_DIR)
        normalized["count"] = _safe_int(normalized.get("count"), 20, 1)
        normalized["threads"] = _safe_int(normalized.get("threads"), 3, 1)
        normalized["proxy"] = str(normalized.get("proxy") or "").strip()
        normalized["server"] = str(normalized.get("server") or "").strip()
        normalized["auth_key"] = str(normalized.get("auth_key") or "").strip()
        normalized["min_active_accounts"] = _safe_int(normalized.get("min_active_accounts"), 60, 1)
        normalized["monitor_interval_seconds"] = _safe_int(normalized.get("monitor_interval_seconds"), 300, 5)
        normalized["upload_to_cloud"] = _safe_bool(normalized.get("upload_to_cloud"), True)
        normalized["enable_flaresolverr"] = _safe_bool(normalized.get("enable_flaresolverr"), False)
        normalized["flaresolverr_url"] = str(normalized.get("flaresolverr_url") or "").strip()
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return normalized

    @staticmethod
    def _load_text(path: Path, fallback: str = "") -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return fallback

    @staticmethod
    def _parse_register_config(text: str) -> dict[str, Any]:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("register.json 必须是 JSON 对象")
        return data

    def _resolve_output_dir(self, settings: dict[str, Any]) -> Path:
        target = Path(str(settings.get("output_dir") or DEFAULT_OUTPUT_DIR)).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        (target / "imports").mkdir(parents=True, exist_ok=True)
        return target

    def settings_payload(self) -> dict[str, Any]:
        settings = self._load_settings()
        return {
            "settings": _json_safe(settings),
            "register_config_text": self._load_text(REGISTER_CONFIG_FILE, json.dumps(DEFAULT_REGISTER_CONFIG, ensure_ascii=False, indent=2) + "\n"),
            "env_text": self._load_text(ENV_FILE, ""),
            "paths": {
                "settings_file": str(SETTINGS_FILE),
                "register_config_file": str(REGISTER_CONFIG_FILE),
                "env_file": str(ENV_FILE),
                "output_dir": str(self._resolve_output_dir(settings)),
                "runtime_root": str(REGISTER_CONFIG_FILE.parent),
            },
        }

    def save_all(self, *, settings: dict[str, Any], register_config_text: str, env_text: str) -> dict[str, Any]:
        normalized_settings = self._save_settings_payload(settings)
        parsed_register = self._parse_register_config(register_config_text)
        merged_proxy = str(normalized_settings.get("proxy") or parsed_register.get("proxy") or "").strip()
        normalized_settings["proxy"] = merged_proxy
        parsed_register["proxy"] = merged_proxy
        REGISTER_CONFIG_FILE.write_text(
            json.dumps(parsed_register, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        ENV_FILE.write_text(str(env_text or ""), encoding="utf-8")
        self._append_log("配置已保存")
        return {
            "settings": _json_safe(normalized_settings),
            "register_config_text": self._load_text(REGISTER_CONFIG_FILE, "{}\n"),
            "env_text": self._load_text(ENV_FILE, ""),
        }

    def _append_log(self, text: str, level: str | None = None) -> None:
        entry = {
            "id": 0,
            "timestamp": _timestamp_text(),
            "level": level or _infer_level(text),
            "message": str(text or ""),
        }
        with self._state_lock:
            self._log_cursor += 1
            entry["id"] = self._log_cursor
            self._logs.append(entry)
            if len(self._logs) > self._max_logs:
                self._logs = self._logs[-self._max_logs :]

    def _update_progress(self, payload: dict[str, Any]) -> None:
        with self._state_lock:
            self._progress = {
                "total": _safe_int(payload.get("total"), 0, 0),
                "submitted": _safe_int(payload.get("submitted"), 0, 0),
                "done": _safe_int(payload.get("done"), 0, 0),
                "success": _safe_int(payload.get("success"), 0, 0),
                "fail": _safe_int(payload.get("fail"), 0, 0),
                "running": _safe_int(payload.get("running"), 0, 0),
            }

    def get_logs(self, cursor: int = 0) -> dict[str, Any]:
        with self._state_lock:
            items = [entry for entry in self._logs if int(entry["id"]) > max(0, int(cursor))]
            next_cursor = self._logs[-1]["id"] if self._logs else 0
        return {"items": items, "cursor": next_cursor}

    def export_logs_text(self) -> tuple[str, str]:
        with self._state_lock:
            lines = [f"{item['timestamp']} [{item['level']}] {item['message']}" for item in self._logs]
        name = f"reg-web-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        content = "\n".join(lines) + ("\n" if lines else "")
        return name, content

    def runtime_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            busy = self._busy
            monitoring = self._monitoring
            current_task = self._current_task
            current_hint = self._current_hint
            next_check_at = self._next_check_at
            progress = dict(self._progress)
        countdown_text = "未启动"
        if monitoring and not busy:
            if next_check_at > time.time():
                countdown_text = f"{max(0, int(next_check_at - time.time()))}s 后再次检查"
            else:
                countdown_text = "即将开始首次检查"
        elif busy:
            countdown_text = "当前任务运行中"
        status = "idle"
        if monitoring:
            status = "monitoring"
        elif busy:
            status = "running"
        return {
            "busy": busy,
            "monitoring": monitoring,
            "status": status,
            "current_task": current_task,
            "hint": current_hint,
            "monitor_countdown_text": countdown_text,
            "next_check_at": datetime.fromtimestamp(next_check_at).isoformat() if next_check_at > 0 else "",
            "progress": progress,
        }

    def cloud_summary(self) -> dict[str, Any]:
        settings = self._load_settings()
        server = str(settings.get("server") or "").strip()
        auth_key = str(settings.get("auth_key") or "").strip()
        proxy = str(settings.get("proxy") or "").strip()
        if not server or not auth_key:
            raise ValueError("请先填写云端地址和管理员密钥")
        payload = fetch_cloud_account_summary(server=server, auth_key=auth_key, proxy=proxy)
        return payload

    def proxy_test(self, proxy: str = "") -> dict[str, Any]:
        settings = self._load_settings()
        proxy = str(proxy or settings.get("proxy") or "").strip()
        result = test_openai_proxy(proxy=proxy)
        message = str(result.get("message") or "")
        if result.get("ok"):
            self._append_log(f"代理测试成功: {message}", level="success")
        else:
            self._append_log(f"代理测试失败: {message}", level="error")
        return result

    def _build_common_kwargs(self) -> dict[str, Any]:
        settings = self._load_settings()
        return {
            "config_path": REGISTER_CONFIG_FILE.resolve(),
            "output_dir": self._resolve_output_dir(settings),
            "count": _safe_int(settings.get("count"), 20, 1),
            "threads": _safe_int(settings.get("threads"), 3, 1),
            "proxy": str(settings.get("proxy") or "").strip(),
            "enable_flaresolverr": _safe_bool(settings.get("enable_flaresolverr"), False),
            "flaresolverr_url": str(settings.get("flaresolverr_url") or "").strip(),
            "upload_to_cloud": _safe_bool(settings.get("upload_to_cloud"), True),
            "server": str(settings.get("server") or "").strip(),
            "auth_key": str(settings.get("auth_key") or "").strip(),
            "logger": self._append_log,
            "progress_callback": self._update_progress,
        }

    def _begin_busy(self, task_name: str, *, allow_when_monitoring: bool = False) -> None:
        with self._state_lock:
            if self._busy:
                raise RuntimeError("当前已有任务在运行，请稍后再试")
            if self._monitoring and not allow_when_monitoring:
                raise RuntimeError("当前正在循环监控，请先停止监控后再手动执行")
            self._busy = True
            self._current_task = task_name
            self._current_hint = "任务执行中，日志会持续滚动输出"
            self._progress = self._empty_progress()

    def _finish_busy(self) -> None:
        with self._state_lock:
            self._busy = False
            self._current_task = ""
            self._progress = self._empty_progress()
            self._current_hint = "正在循环检查云端账号数量，低于阈值时会连续补号直到达标" if self._monitoring else "可以开始本地注册、导入云端或开启自动补号监控"

    def _run_threaded(self, *, task_name: str, target, allow_when_monitoring: bool = False) -> None:
        self._begin_busy(task_name, allow_when_monitoring=allow_when_monitoring)

        def runner() -> None:
            try:
                target()
            except Exception as exc:
                self._append_log(f"{task_name} 失败: {exc}", level="error")
            finally:
                self._finish_busy()

        thread = threading.Thread(target=runner, daemon=True, name=f"reg-web-{task_name}")
        self._worker_thread = thread
        thread.start()

    def start_register(self) -> None:
        def task() -> None:
            self._append_log("开始执行本地注册", level="debug")
            run_local_register_job(**self._build_common_kwargs())

        self._run_threaded(task_name="本地注册", target=task)

    def start_refill(self) -> None:
        settings = self._load_settings()
        if not str(settings.get("server") or "").strip() or not str(settings.get("auth_key") or "").strip():
            raise ValueError("检查云端并补号需要填写云端地址和管理员密钥")

        def task() -> None:
            kwargs = self._build_common_kwargs()
            min_active_accounts = _safe_int(self._load_settings().get("min_active_accounts"), 60, 1)
            self._append_log("开始执行云端检查/补号", level="debug")
            check_cloud_and_refill(
                server=kwargs["server"],
                auth_key=kwargs["auth_key"],
                min_active_accounts=min_active_accounts,
                config_path=kwargs["config_path"],
                output_dir=kwargs["output_dir"],
                count=kwargs["count"],
                threads=kwargs["threads"],
                proxy=kwargs["proxy"],
                enable_flaresolverr=kwargs["enable_flaresolverr"],
                flaresolverr_url=kwargs["flaresolverr_url"],
                upload_to_cloud=kwargs["upload_to_cloud"],
                logger=self._append_log,
                progress_callback=self._update_progress,
                should_stop=self._monitor_stop_event.is_set,
            )

        self._run_threaded(task_name="检查补号", target=task)

    def start_import(self, accounts_file: Path) -> None:
        if not accounts_file.exists():
            raise FileNotFoundError(f"账号文件不存在: {accounts_file}")
        settings = self._load_settings()
        if not str(settings.get("server") or "").strip() or not str(settings.get("auth_key") or "").strip():
            raise ValueError("导入账号需要填写云端地址和管理员密钥")

        def task() -> None:
            kwargs = self._build_common_kwargs()
            self._append_log(f"开始导入账号文件: {accounts_file.name}", level="debug")
            try:
                run_local_register_job(
                    config_path=kwargs["config_path"],
                    output_dir=kwargs["output_dir"],
                    count=kwargs["count"],
                    threads=kwargs["threads"],
                    proxy=kwargs["proxy"],
                    enable_flaresolverr=kwargs["enable_flaresolverr"],
                    flaresolverr_url=kwargs["flaresolverr_url"],
                    accounts_file=accounts_file,
                    import_only=True,
                    upload_to_cloud=True,
                    server=kwargs["server"],
                    auth_key=kwargs["auth_key"],
                    logger=self._append_log,
                    progress_callback=self._update_progress,
                )
            finally:
                try:
                    accounts_file.unlink(missing_ok=True)
                except Exception:
                    pass

        self._run_threaded(task_name="导入账号", target=task)

    def start_monitor(self) -> None:
        with self._state_lock:
            if self._monitoring:
                raise RuntimeError("循环监控已经在运行中")
            if self._busy:
                raise RuntimeError("当前已有任务在运行，请稍后再开启监控")
            self._monitoring = True
            self._current_hint = "正在循环检查云端账号数量，低于阈值时会连续补号直到达标"
            self._next_check_at = 0.0
        self._monitor_stop_event.clear()
        settings = self._load_settings()
        if not str(settings.get("server") or "").strip() or not str(settings.get("auth_key") or "").strip():
            with self._state_lock:
                self._monitoring = False
                self._current_hint = "可以开始本地注册、导入云端或开启自动补号监控"
            raise ValueError("开启循环监控需要填写云端地址和管理员密钥")
        interval_seconds = _safe_int(settings.get("monitor_interval_seconds"), 300, 5)
        self._append_log(f"已开启循环监控，巡检间隔 {interval_seconds}s；一旦低于阈值，将连续补号直到达标")

        def monitor_loop() -> None:
            try:
                while not self._monitor_stop_event.is_set():
                    try:
                        self._begin_busy("监控补号", allow_when_monitoring=True)
                    except RuntimeError:
                        self._append_log("检测到已有任务在执行，本轮监控跳过", level="warning")
                    else:
                        try:
                            self._append_log("开始执行监控检查", level="debug")
                            kwargs = self._build_common_kwargs()
                            min_active_accounts = _safe_int(self._load_settings().get("min_active_accounts"), 60, 1)
                            check_cloud_and_refill(
                                server=kwargs["server"],
                                auth_key=kwargs["auth_key"],
                                min_active_accounts=min_active_accounts,
                                config_path=kwargs["config_path"],
                                output_dir=kwargs["output_dir"],
                                count=kwargs["count"],
                                threads=kwargs["threads"],
                                proxy=kwargs["proxy"],
                                enable_flaresolverr=kwargs["enable_flaresolverr"],
                                flaresolverr_url=kwargs["flaresolverr_url"],
                                upload_to_cloud=kwargs["upload_to_cloud"],
                                logger=self._append_log,
                                progress_callback=self._update_progress,
                                should_stop=self._monitor_stop_event.is_set,
                            )
                        except Exception as exc:
                            self._append_log(f"监控补号执行失败: {exc}", level="error")
                        finally:
                            self._finish_busy()
                    interval_seconds_local = _safe_int(self._load_settings().get("monitor_interval_seconds"), 300, 5)
                    with self._state_lock:
                        self._next_check_at = time.time() + interval_seconds_local
                    if self._monitor_stop_event.wait(interval_seconds_local):
                        break
            finally:
                with self._state_lock:
                    self._monitoring = False
                    self._next_check_at = 0.0
                    if not self._busy:
                        self._current_hint = "可以开始本地注册、导入云端或开启自动补号监控"
                self._append_log("循环监控已停止")

        thread = threading.Thread(target=monitor_loop, daemon=True, name="reg-web-monitor")
        self._monitor_thread = thread
        thread.start()

    def stop_monitor(self) -> None:
        with self._state_lock:
            if not self._monitoring:
                raise RuntimeError("当前没有运行中的循环监控")
        self._monitor_stop_event.set()
        self._append_log("已请求停止循环监控，当前轮次结束后会退出", level="warning")

    def shutdown(self) -> None:
        self._monitor_stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1)
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1)
