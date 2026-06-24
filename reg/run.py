from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any

from curl_cffi import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reg.runtime import DEFAULT_REGISTER_CONFIG as RUNTIME_DEFAULT_REGISTER_CONFIG  # noqa: E402
from reg.runtime import OUTPUT_DIR as RUNTIME_OUTPUT_DIR  # noqa: E402
from reg.runtime import REGISTER_CONFIG_FILE as RUNTIME_REGISTER_CONFIG_FILE  # noqa: E402
from reg.runtime import ensure_runtime_environment  # noqa: E402

ensure_runtime_environment()

from services.register import openai_register  # noqa: E402


DEFAULT_OUTPUT_DIR = RUNTIME_OUTPUT_DIR
DEFAULT_REGISTER_CONFIG = RUNTIME_DEFAULT_REGISTER_CONFIG
DEFAULT_REGISTER_CONFIG_FILE = RUNTIME_REGISTER_CONFIG_FILE


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(text: str) -> None:
    print(f"{_now_text()} {text}", flush=True)


def _emit_logger(logger, text: str) -> None:
    try:
        logger(text)
    except Exception:
        log(text)


def load_register_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"注册配置不是 JSON 对象: {path}")
    return raw


def normalize_register_config(
    raw: dict[str, Any],
    *,
    count: int | None,
    threads: int | None,
    proxy: str = "",
    enable_warp_registration: bool = False,
    enable_flaresolverr: bool,
    flaresolverr_url: str,
) -> dict[str, Any]:
    mail = raw.get("mail") if isinstance(raw.get("mail"), dict) else {}
    flaresolverr = raw.get("flaresolverr") if isinstance(raw.get("flaresolverr"), dict) else {}
    resolved_proxy = str(proxy or raw.get("proxy") or "").strip()
    resolved_flaresolverr_url = str(flaresolverr_url or flaresolverr.get("url") or "").strip()
    return {
        "mail": {
            "request_timeout": float(mail.get("request_timeout") or 30),
            "wait_timeout": float(mail.get("wait_timeout") or 30),
            "wait_interval": float(mail.get("wait_interval") or 2),
            "providers": list(mail.get("providers") or []),
        },
        "proxy": resolved_proxy,
        "enable_warp_registration": bool(enable_warp_registration or raw.get("enable_warp_registration") or False),
        "flaresolverr": {
            "enabled": bool(enable_flaresolverr and resolved_flaresolverr_url),
            "url": resolved_flaresolverr_url,
            "max_timeout_ms": max(1000, int(flaresolverr.get("max_timeout_ms") or 60000)),
            "preload": bool(flaresolverr.get("preload", True)),
        },
        "total": max(1, int(count or raw.get("total") or 1)),
        "threads": max(1, int(threads or raw.get("threads") or 1)),
    }


def apply_register_config(config_data: dict[str, Any]) -> None:
    openai_register.config.update(
        {
            "mail": config_data["mail"],
            "proxy": config_data["proxy"],
            "enable_warp_registration": config_data["enable_warp_registration"],
            "flaresolverr": config_data["flaresolverr"],
            "total": config_data["total"],
            "threads": config_data["threads"],
        }
    )
    openai_register.set_flaresolverr_runtime_override(config_data["flaresolverr"])


def register_once(index: int, logger=log) -> dict[str, Any]:
    start = time.time()
    proxy, proxy_label = openai_register.resolve_register_proxy(openai_register.config)
    registrar = openai_register.PlatformRegistrar(proxy)
    try:
        openai_register.step(index, f"本地任务启动（{proxy_label}）")
        result = registrar.register(index)
        duration = round(time.time() - start, 1)
        _emit_logger(logger, f"[任务{index}] 注册成功 {result.get('email', '')}，耗时 {duration}s")
        return {"ok": True, "index": index, "duration": duration, "account": result}
    except Exception as exc:
        duration = round(time.time() - start, 1)
        _emit_logger(logger, f"[任务{index}] 注册失败，耗时 {duration}s，原因: {exc}")
        return {"ok": False, "index": index, "duration": duration, "error": str(exc)}
    finally:
        registrar.close()


def run_register_batch(
    config_data: dict[str, Any],
    *,
    logger=log,
    progress_callback=None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    total = int(config_data["total"])
    threads = int(config_data["threads"])
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    submitted = 0
    futures = set()
    stats = {
        "total": total,
        "submitted": 0,
        "done": 0,
        "success": 0,
        "fail": 0,
        "running": 0,
    }

    def emit_progress() -> None:
        if progress_callback is None:
            return
        progress_callback(dict(stats))

    previous_log_sink = openai_register.register_log_sink
    openai_register.register_log_sink = lambda text, _color="": _emit_logger(logger, text)
    try:
        _emit_logger(logger, f"开始本地注册，总数={total}，线程数={threads}")
        emit_progress()
        with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="reg-local") as executor:
            while submitted < total or futures:
                while submitted < total and len(futures) < threads:
                    submitted += 1
                    futures.add(executor.submit(register_once, submitted, logger))
                    stats["submitted"] = submitted
                    stats["running"] = len(futures)
                    emit_progress()
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                stats["running"] = len(futures)
                for future in done:
                    result = future.result()
                    stats["done"] += 1
                    if result.get("ok"):
                        account = result.get("account")
                        if isinstance(account, dict):
                            successes.append(account)
                        stats["success"] += 1
                    else:
                        failures.append(result)
                        stats["fail"] += 1
                    stats["running"] = len(futures)
                    emit_progress()
        _emit_logger(logger, f"本地注册结束，成功 {len(successes)}，失败 {len(failures)}")
        emit_progress()
    finally:
        openai_register.register_log_sink = previous_log_sink
    return successes, failures


def save_results(accounts: list[dict[str, Any]], failures: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    accounts_path = output_dir / f"accounts-{stamp}.json"
    failures_path = output_dir / f"failures-{stamp}.json"
    accounts_path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failures_path.write_text(json.dumps(failures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return accounts_path, failures_path


def import_accounts(
    *,
    server: str,
    auth_key: str,
    accounts: list[dict[str, Any]],
    proxy: str = "",
    timeout: float = 180.0,
) -> dict[str, Any]:
    if not accounts:
        return {"added": 0, "skipped": 0, "refreshed": 0, "errors": [], "items": []}
    server = server.rstrip("/")
    session_kwargs: dict[str, Any] = {"impersonate": "chrome", "verify": False}
    if str(proxy or "").strip():
        session_kwargs["proxy"] = str(proxy).strip()
    session = requests.Session(**session_kwargs)
    try:
        response = session.post(
            f"{server}/api/accounts",
            headers={
                "Authorization": f"Bearer {auth_key}",
                "Content-Type": "application/json",
            },
            json={"tokens": [], "accounts": accounts},
            timeout=timeout,
        )
        if response.status_code != 200:
            detail = response.text[:1000]
            raise RuntimeError(f"导入失败，HTTP {response.status_code}: {detail}")
        try:
            data = response.json()
        except Exception as exc:
            detail = response.text[:1000]
            content_type = str(response.headers.get("content-type") or "")
            raise RuntimeError(
                f"导入接口返回的不是合法 JSON，content-type={content_type}, body={detail}"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError("导入接口返回的不是 JSON 对象")
        return data
    finally:
        session.close()


def fetch_cloud_account_summary(
    *,
    server: str,
    auth_key: str,
    proxy: str = "",
    timeout: float = 30.0,
) -> dict[str, Any]:
    server = server.rstrip("/")
    session_kwargs: dict[str, Any] = {"impersonate": "chrome", "verify": False}
    if str(proxy or "").strip():
        session_kwargs["proxy"] = str(proxy).strip()
    session = requests.Session(**session_kwargs)
    try:
        response = session.get(
            f"{server}/api/accounts/summary",
            headers={"Authorization": f"Bearer {auth_key}"},
            timeout=timeout,
        )
        if response.status_code != 200:
            detail = response.text[:1000]
            raise RuntimeError(f"读取云端账号统计失败，HTTP {response.status_code}: {detail}")
        try:
            data = response.json()
        except Exception as exc:
            detail = response.text[:1000]
            content_type = str(response.headers.get("content-type") or "")
            raise RuntimeError(
                f"云端账号统计接口返回的不是合法 JSON，content-type={content_type}, body={detail}"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError("云端账号统计接口返回的不是 JSON 对象")
        return data
    finally:
        session.close()


def test_openai_proxy(proxy: str = "", timeout: float = 20.0) -> dict[str, Any]:
    effective_proxy = str(proxy or "").strip()
    session = openai_register.create_session(effective_proxy)
    targets = [
        ("chatgpt", "https://chatgpt.com/"),
        ("auth", "https://auth.openai.com/"),
    ]
    results: list[dict[str, Any]] = []

    try:
        for label, url in targets:
            try:
                response = session.get(url, timeout=timeout, allow_redirects=True)
                status_code = int(getattr(response, "status_code", 0) or 0)
                final_url = str(getattr(response, "url", "") or "")
                results.append(
                    {
                        "target": label,
                        "url": url,
                        "status_code": status_code,
                        "final_url": final_url,
                        "reachable": status_code > 0 and status_code < 500,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "target": label,
                        "url": url,
                        "status_code": 0,
                        "final_url": "",
                        "reachable": False,
                        "error": str(exc),
                    }
                )
        reachable_results = [item for item in results if item.get("reachable")]
        if reachable_results:
            best = reachable_results[0]
            message = f"{best['target']} 可访问，HTTP {best['status_code']}"
            return {
                "ok": True,
                "proxy": effective_proxy,
                "message": message,
                "results": results,
            }
        first_error = next((str(item.get("error") or "") for item in results if item.get("error")), "")
        return {
            "ok": False,
            "proxy": effective_proxy,
            "message": first_error or "代理无法访问 ChatGPT 相关站点",
            "results": results,
        }
    finally:
        session.close()


def run_local_register_job(
    *,
    config_path: Path,
    output_dir: Path,
    count: int | None = None,
    threads: int | None = None,
    proxy: str = "",
    enable_warp_registration: bool = False,
    enable_flaresolverr: bool = False,
    flaresolverr_url: str = "",
    accounts_file: Path | None = None,
    import_only: bool = False,
    upload_to_cloud: bool = False,
    server: str = "",
    auth_key: str = "",
    logger=log,
    progress_callback=None,
) -> dict[str, Any]:
    accounts: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    if accounts_file is not None:
        if not accounts_file.exists():
            raise FileNotFoundError(f"账号文件不存在: {accounts_file}")
        loaded = json.loads(accounts_file.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError(f"账号文件格式错误，需要 JSON 数组: {accounts_file}")
        accounts = [item for item in loaded if isinstance(item, dict)]
        logger(f"已加载账号文件 {accounts_file}，共 {len(accounts)} 条")

    if not import_only:
        if not config_path.exists():
            raise FileNotFoundError(f"注册配置文件不存在: {config_path}")
        raw = load_register_config(config_path)
        config_data = normalize_register_config(
            raw,
            count=count,
            threads=threads,
            proxy=proxy,
            enable_warp_registration=enable_warp_registration,
            enable_flaresolverr=enable_flaresolverr,
            flaresolverr_url=str(flaresolverr_url or "").strip(),
        )
        apply_register_config(config_data)
        logger(
            "本地注册配置："
            f"代理={'直连' if not config_data['proxy'] else config_data['proxy']}，"
            f"WARP={'开启' if config_data['enable_warp_registration'] else '关闭'}，"
            f"FlareSolverr={'开启' if config_data['flaresolverr']['enabled'] else '关闭'}，"
            f"线程={config_data['threads']}，数量={config_data['total']}"
        )
        accounts, failures = run_register_batch(
            config_data,
            logger=logger,
            progress_callback=progress_callback,
        )
        logger(f"本地注册结束：成功 {len(accounts)}，失败 {len(failures)}")
    elif not accounts:
        raise ValueError("使用 import_only 时必须提供 accounts_file")

    import_result: dict[str, Any] | None = None
    if upload_to_cloud:
        if not server.strip() or not auth_key.strip():
            raise ValueError("开启上传到云端时必须提供 server 和 auth_key")
        if not accounts:
            logger("本轮没有成功账号，跳过云端导入")
            return {
                "accounts": accounts,
                "failures": failures,
                "accounts_path": "",
                "failures_path": "",
                "import_result": None,
            }
        logger(f"开始导入云端: {server}")
        import_result = import_accounts(
            server=server.strip(),
            auth_key=auth_key.strip(),
            accounts=accounts,
            proxy=proxy,
        )
        logger(
            "云端导入完成，"
            f"added={int(import_result.get('added') or 0)}, "
            f"skipped={int(import_result.get('skipped') or 0)}, "
            f"refreshed={int(import_result.get('refreshed') or 0)}"
        )
        errors = import_result.get("errors") if isinstance(import_result.get("errors"), list) else []
        if errors:
            logger(f"云端导入存在错误 {len(errors)} 条，首条: {json.dumps(errors[0], ensure_ascii=False)}")

    return {
        "accounts": accounts,
        "failures": failures,
        "accounts_path": "",
        "failures_path": "",
        "import_result": import_result,
    }


def check_cloud_and_refill(
    *,
    server: str,
    auth_key: str,
    min_active_accounts: int,
    config_path: Path,
    output_dir: Path,
    count: int | None = None,
    threads: int | None = None,
    proxy: str = "",
    enable_warp_registration: bool = False,
    enable_flaresolverr: bool = False,
    flaresolverr_url: str = "",
    upload_to_cloud: bool = True,
    logger=log,
    progress_callback=None,
    should_stop=None,
) -> dict[str, Any]:
    threshold = max(0, int(min_active_accounts))
    rounds: list[dict[str, Any]] = []

    while True:
        if callable(should_stop) and should_stop():
            logger("收到停止信号，结束当前补号流程")
            return {
                "stopped": True,
                "rounds": rounds,
            }

        summary_payload = fetch_cloud_account_summary(server=server, auth_key=auth_key, proxy=proxy)
        summary = summary_payload.get("summary") if isinstance(summary_payload.get("summary"), dict) else {}
        active = int(summary_payload.get("valid_account_count") or summary.get("active") or 0)
        logger(f"云端当前有效账号数: {active}，阈值下限: {threshold}")
        if active >= threshold:
            logger("云端有效账号数量已达到阈值，停止继续补号")
            return {
                "skipped": not rounds,
                "summary": summary_payload,
                "rounds": rounds,
                "valid_account_count": active,
            }

        round_no = len(rounds) + 1
        logger(f"云端有效账号数量不足，开始第 {round_no} 轮本地注册补号")
        result = run_local_register_job(
            config_path=config_path,
            output_dir=output_dir,
            count=count,
            threads=threads,
            proxy=proxy,
            enable_warp_registration=enable_warp_registration,
            enable_flaresolverr=enable_flaresolverr,
            flaresolverr_url=flaresolverr_url,
            upload_to_cloud=upload_to_cloud,
            server=server,
            auth_key=auth_key,
            logger=logger,
            progress_callback=progress_callback,
        )
        result["summary_before"] = summary_payload
        result["round"] = round_no
        rounds.append(result)

        import_result = result.get("import_result") if isinstance(result.get("import_result"), dict) else {}
        added = int(import_result.get("added") or 0)
        refreshed = int(import_result.get("refreshed") or 0)
        success_count = len(result.get("accounts") or [])
        if success_count <= 0 and added <= 0 and refreshed <= 0:
            logger("本轮补号未产生新的有效增量，停止继续重试，等待下次人工或监控触发")
            return {
                "stopped": True,
                "summary": summary_payload,
                "rounds": rounds,
                "valid_account_count": active,
            }

        latest_summary = fetch_cloud_account_summary(server=server, auth_key=auth_key, proxy=proxy)
        latest_summary_data = latest_summary.get("summary") if isinstance(latest_summary.get("summary"), dict) else {}
        latest_active = int(latest_summary.get("valid_account_count") or latest_summary_data.get("active") or 0)
        logger(f"第 {round_no} 轮补号后，云端有效账号数: {latest_active}")

        if latest_active >= threshold:
            logger("云端有效账号数量已达到阈值，当前补号流程结束")
            return {
                "summary": latest_summary,
                "rounds": rounds,
                "valid_account_count": latest_active,
            }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地独立注册并可选导入云端")
    parser.add_argument("--config", default=str(DEFAULT_REGISTER_CONFIG_FILE), help="注册配置文件路径，默认 reg/register.json")
    parser.add_argument("--count", type=int, default=None, help="覆盖注册总数")
    parser.add_argument("--threads", type=int, default=None, help="覆盖注册线程数")
    parser.add_argument("--proxy", default="", help="自定义代理地址，例如 http://127.0.0.1:7890")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录，默认 reg/output")
    parser.add_argument("--enable-flaresolverr", action="store_true", help="启用 FlareSolverr")
    parser.add_argument("--flaresolverr-url", default="", help="FlareSolverr 地址")
    parser.add_argument("--accounts-file", default="", help="已有账号文件路径")
    parser.add_argument("--import-only", action="store_true", help="只导入账号文件，不执行本地注册")
    parser.add_argument("--server", default="", help="云端服务地址，例如 http://your-server:8000")
    parser.add_argument("--auth-key", default="", help="云端管理员 auth-key")
    parser.add_argument("--min-active-accounts", type=int, default=None, help="云端有效账号低于此值时才执行本地注册并导入")
    parser.add_argument("--skip-import", action="store_true", help="只本地注册，不导入云端")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    config_path = Path(args.config).resolve()
    accounts_file = Path(args.accounts_file).resolve() if args.accounts_file.strip() else None
    upload_to_cloud = not args.skip_import and bool(args.server.strip()) and bool(args.auth_key.strip())

    if args.min_active_accounts is not None:
        if not args.server.strip() or not args.auth_key.strip():
            raise SystemExit("使用 --min-active-accounts 时必须提供 --server 和 --auth-key")
        check_cloud_and_refill(
            server=args.server.strip(),
            auth_key=args.auth_key.strip(),
            min_active_accounts=int(args.min_active_accounts),
            config_path=config_path,
            output_dir=output_dir,
            count=args.count,
            threads=args.threads,
            proxy=str(args.proxy or "").strip(),
            enable_flaresolverr=bool(args.enable_flaresolverr),
            flaresolverr_url=str(args.flaresolverr_url or "").strip(),
            upload_to_cloud=upload_to_cloud,
            logger=log,
        )
        return 0

    if not args.skip_import and not upload_to_cloud and not args.import_only:
        log("未提供 --server 或 --auth-key，跳过云端导入")

    run_local_register_job(
        config_path=config_path,
        output_dir=output_dir,
        count=args.count,
        threads=args.threads,
        proxy=str(args.proxy or "").strip(),
        enable_flaresolverr=bool(args.enable_flaresolverr),
        flaresolverr_url=str(args.flaresolverr_url or "").strip(),
        accounts_file=accounts_file,
        import_only=bool(args.import_only),
        upload_to_cloud=upload_to_cloud,
        server=args.server.strip(),
        auth_key=args.auth_key.strip(),
        logger=log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
