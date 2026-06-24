from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _app_root() -> Path:
    override = os.getenv("CHATGPT2API_REG_APP_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_ROOT = _app_root()
ENV_FILE = APP_ROOT / ".env"
DEFAULT_LOCAL_AUTH_KEY = "local-reg-tool"

DEFAULT_REGISTER_CONFIG: dict[str, Any] = {
    "mail": {
        "request_timeout": 30,
        "wait_timeout": 30,
        "wait_interval": 2,
        "providers": [
            {
                "enable": True,
                "type": "yyds_mail",
                "api_base": "https://maliapi.215.im/v1",
                "admin_email": "",
                "admin_password": "",
                "domain": [],
                "subdomain": "",
                "email_prefix": "",
                "api_key": "",
                "wildcard": False,
            }
        ],
    },
    "proxy": "",
    "flaresolverr": {
        "preload": True,
        "url": "http://flaresolverr:8191",
        "max_timeout_ms": 60000,
        "enabled": False,
    },
    "total": 20,
    "threads": 3,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "auth-key": DEFAULT_LOCAL_AUTH_KEY,
    "timezone": "Asia/Shanghai",
}


DEFAULT_ENV_TEXT = """# Local register tool environment
# Optional: override paths below if needed.
# CHATGPT2API_REGISTER_CONFIG_FILE=
# CHATGPT2API_OUTPUT_DIR=

CHATGPT2API_AUTH_KEY=local-reg-tool
CHATGPT2API_TIMEZONE=Asia/Shanghai
STORAGE_BACKEND=json
"""


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        env_key = key.strip()
        if not env_key:
            continue
        os.environ.setdefault(env_key, value.strip().strip('"').strip("'"))


_load_env_file(ENV_FILE)

DATA_DIR = Path(os.getenv("CHATGPT2API_DATA_DIR") or (APP_ROOT / "data")).expanduser()
OUTPUT_DIR = Path(os.getenv("CHATGPT2API_OUTPUT_DIR") or (APP_ROOT / "output")).expanduser()
SETTINGS_FILE = APP_ROOT / "settings.json"
REGISTER_CONFIG_FILE = Path(os.getenv("CHATGPT2API_REGISTER_CONFIG_FILE") or (APP_ROOT / "register.json")).expanduser()
CONFIG_FILE = Path(os.getenv("CHATGPT2API_CONFIG_FILE") or (APP_ROOT / "config.json")).expanduser()


def _write_json_if_missing(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text_if_missing(path: Path, text: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_runtime_environment() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_text_if_missing(ENV_FILE, DEFAULT_ENV_TEXT)
    _write_json_if_missing(REGISTER_CONFIG_FILE, DEFAULT_REGISTER_CONFIG)
    _write_json_if_missing(CONFIG_FILE, DEFAULT_CONFIG)

    os.environ.setdefault("CHATGPT2API_BASE_DIR", str(APP_ROOT))
    os.environ.setdefault("CHATGPT2API_DATA_DIR", str(DATA_DIR))
    os.environ.setdefault("CHATGPT2API_CONFIG_FILE", str(CONFIG_FILE))
    os.environ.setdefault("CHATGPT2API_REGISTER_CONFIG_FILE", str(REGISTER_CONFIG_FILE))
    os.environ.setdefault("CHATGPT2API_AUTH_KEY", DEFAULT_LOCAL_AUTH_KEY)
    os.environ.setdefault("STORAGE_BACKEND", "json")
