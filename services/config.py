from __future__ import annotations

import json
import os
import time

from reg.runtime import APP_ROOT, CONFIG_FILE as RUNTIME_CONFIG_FILE, DATA_DIR, DEFAULT_LOCAL_AUTH_KEY

BASE_DIR = APP_ROOT
CONFIG_FILE = RUNTIME_CONFIG_FILE
VERSION_FILE = BASE_DIR / "VERSION"


def _load_config() -> dict:
    try:
        raw = json.loads(RUNTIME_CONFIG_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


class ConfigStore:
    def __init__(self) -> None:
        self.base_dir = APP_ROOT
        self.data_dir = DATA_DIR
        self.config_file = RUNTIME_CONFIG_FILE
        self.data = _load_config()

    @property
    def timezone(self) -> str:
        return str(os.getenv("CHATGPT2API_TIMEZONE") or self.data.get("timezone") or "Asia/Shanghai").strip()

    @property
    def auth_key(self) -> str:
        return str(os.getenv("CHATGPT2API_AUTH_KEY") or self.data.get("auth-key") or DEFAULT_LOCAL_AUTH_KEY).strip()

    @property
    def refresh_account_interval_minute(self) -> int:
        try:
            return max(1, int(self.data.get("refresh_account_interval_minute") or 5))
        except (TypeError, ValueError):
            return 5

    @property
    def refresh_account_concurrency(self) -> int:
        try:
            return max(1, min(64, int(self.data.get("refresh_account_concurrency") or 8)))
        except (TypeError, ValueError):
            return 8

    @property
    def auto_relogin_after_refresh(self) -> bool:
        value = self.data.get("auto_relogin_after_refresh", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def get(self) -> dict[str, object]:
        return dict(self.data)

    def update(self, payload: dict[str, object]) -> dict[str, object]:
        next_data = dict(self.data)
        next_data.update(payload or {})
        self.data = next_data
        self.config_file.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return dict(self.data)

    def get_proxy_settings(self) -> str:
        return str(self.data.get("proxy") or "").strip()

    def get_proxy_runtime_settings(self) -> dict[str, object]:
        runtime = self.data.get("proxy_runtime")
        return dict(runtime) if isinstance(runtime, dict) else {}

    def get_storage_backend(self):
        from services.storage.factory import create_storage_backend

        return create_storage_backend(self.data_dir)


config = ConfigStore()


def _tz_offset_hours(name: str) -> int:
    normalized = str(name or "").strip()
    if normalized in {"Asia/Shanghai", "CST-8"}:
        return 8
    if normalized == "UTC":
        return 0
    return 8


def local_time_text(timestamp: float | None = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    ts = time.time() if timestamp is None else float(timestamp)
    return time.strftime(fmt, time.gmtime(ts + _tz_offset_hours(config.timezone) * 3600))
