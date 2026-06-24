from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import secrets
import string
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from curl_cffi import requests

from services.account_service import account_service
from services.config import local_time_text
from services.proxy_service import proxy_settings
from services.register import mail_provider

base_dir = Path(__file__).resolve().parent
config = {
    "mail": {
        "request_timeout": 30,
        "wait_timeout": 30,
        "wait_interval": 2,
        "providers": [],
    },
    "proxy": "",
    "enable_warp_registration": False,
    "flaresolverr": {
        "enabled": True,
        "url": "",
        "max_timeout_ms": 60000,
        "preload": True,
    },
    "total": 10,
    "threads": 3,
}
register_config_file = base_dir.parents[1] / "data" / "register.json"
register_config_file = Path(os.getenv("CHATGPT2API_REGISTER_CONFIG_FILE") or register_config_file).expanduser()
try:
    saved_config = json.loads(register_config_file.read_text(encoding="utf-8"))
    config.update({key: saved_config[key] for key in ("mail", "proxy", "enable_warp_registration", "flaresolverr", "total", "threads") if key in saved_config})
except Exception:
    pass

auth_base = "https://auth.openai.com"
platform_base = "https://platform.openai.com"
platform_oauth_client_id = "app_2SKx67EdpoN0G6j64rFvigXD"
platform_oauth_redirect_uri = f"{platform_base}/auth/callback"
platform_oauth_audience = "https://api.openai.com/v1"
platform_auth0_client = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
sec_ch_ua = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
sec_ch_ua_full_version_list = '"Chromium";v="145.0.0.0", "Not:A-Brand";v="99.0.0.0", "Google Chrome";v="145.0.0.0"'
default_timeout = 30
print_lock = threading.Lock()
stats_lock = threading.Lock()
stats = {"done": 0, "success": 0, "fail": 0, "start_time": 0.0}
register_log_sink = None
_flaresolverr_autodetect_lock = threading.Lock()
_flaresolverr_autodetect_cache: dict[str, Any] | None = None
_flaresolverr_runtime_override: dict[str, Any] | None = None

common_headers = {
    "accept": "application/json",
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "content-type": "application/json",
    "dnt": "1",
    "origin": auth_base,
    "priority": "u=1, i",
    "sec-gpc": "1",
    "sec-ch-ua": sec_ch_ua,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": sec_ch_ua_full_version_list,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": user_agent,
}

navigate_headers = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "connection": "keep-alive",
    "dnt": "1",
    "sec-gpc": "1",
    "sec-ch-ua": sec_ch_ua,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": sec_ch_ua_full_version_list,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": user_agent,
}


def log(text: str, color: str = "") -> None:
    colors = {"red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m"}
    if register_log_sink:
        try:
            register_log_sink(text, color)
        except Exception:
            pass
    with print_lock:
        prefix = colors.get(color, "")
        suffix = "\033[0m" if prefix else ""
        print(f"{prefix}{local_time_text(fmt='%H:%M:%S')} {text}{suffix}")


def step(index: int, text: str, color: str = "") -> None:
    log(f"[任务{index}] {text}", color)


def _redact_proxy(proxy: str) -> str:
    candidate = str(proxy or "").strip()
    if not candidate:
        return "直连"
    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        return candidate
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunparse((parsed.scheme, f"[REDACTED]@{host}", parsed.path or "", "", parsed.query or "", ""))
    return candidate


def _proxy_log_label(proxy: str) -> str:
    candidate = str(proxy or "").strip()
    if not candidate:
        return "直连"
    return f"代理={_redact_proxy(candidate)}"


def _warp_enabled(settings: dict[str, Any] | None = None) -> bool:
    source = settings if isinstance(settings, dict) else config
    return bool(source.get("enable_warp_registration"))


def resolve_register_proxy(settings: dict[str, Any] | None = None) -> tuple[str, str]:
    source = settings if isinstance(settings, dict) else config
    if _warp_enabled(source):
        warp_proxy = "socks5://warp:1080"
        return warp_proxy, "WARP=socks5://warp:1080"
    runtime_profile = proxy_settings.get_profile(upstream=True)
    runtime_proxy = str(getattr(runtime_profile, "proxy_url", "") or "").strip()
    runtime_source = str(getattr(runtime_profile, "proxy_source", "") or "").strip()
    if runtime_proxy:
        source_label = "全局代理" if runtime_source == "global" else "运行时代理"
        return runtime_proxy, f"{source_label}={_redact_proxy(runtime_proxy)}"
    proxy = str(source.get("proxy") or "").strip()
    return proxy, _proxy_log_label(proxy)


def _make_trace_headers() -> dict[str, str]:
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    return {
        "traceparent": f"00-{uuid.uuid4().hex}-{format(int(parent_id), '016x')}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


from utils.pkce import generate_pkce as _generate_pkce  # noqa: F401


def _random_password(length: int = 16) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    value = list(
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + secrets.choice("!@#$%")
        + "".join(secrets.choice(chars) for _ in range(max(0, length - 4)))
    )
    random.shuffle(value)
    return "".join(value)


def _random_name() -> tuple[str, str]:
    return random.choice(["James", "Robert", "John", "Michael", "David", "Mary", "Emma", "Olivia"]), random.choice(
        ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
    )


def _random_birthdate() -> str:
    return f"{random.randint(1996, 2006):04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"


def _response_json(resp) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _response_debug_detail(resp, limit: int = 800) -> str:
    if resp is None:
        return ""
    data = _response_json(resp)
    parts = [
        f"url={str(getattr(resp, 'url', '') or '')[:300]}",
        f"content_type={str(getattr(resp, 'headers', {}).get('content-type') or '')}",
    ]
    for key in ("cf-ray", "x-request-id", "openai-processing-ms"):
        value = str(getattr(resp, "headers", {}).get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    if data:
        parts.append(f"json={json.dumps(data, ensure_ascii=False)[:limit]}")
    else:
        parts.append(f"body={str(getattr(resp, 'text', '') or '')[:limit]}")
    return ", ".join(parts)


def _is_cloudflare_challenge(resp) -> bool:
    if resp is None:
        return False
    text = str(getattr(resp, "text", "") or "").lower()
    headers = getattr(resp, "headers", {}) or {}
    server = str(headers.get("server") or "").lower()
    return (
        "cloudflare" in server
        or "challenges.cloudflare.com" in text
        or "<title>just a moment" in text
    )


def _truthy(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _normalize_flaresolverr_config(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    api_url = str(source.get("url") or "").strip().rstrip("/")
    try:
        max_timeout_ms = max(1000, int(source.get("max_timeout_ms") or 60000))
    except (TypeError, ValueError):
        max_timeout_ms = 60000
    return {
        "enabled": _truthy(source.get("enabled"), False) and bool(api_url),
        "url": api_url,
        "max_timeout_ms": max_timeout_ms,
        "preload": _truthy(source.get("preload"), True),
    }


def set_flaresolverr_runtime_override(value: dict[str, Any] | None) -> None:
    global _flaresolverr_runtime_override
    _flaresolverr_runtime_override = dict(value) if isinstance(value, dict) else None


def _env_flaresolverr_config() -> dict[str, Any]:
    enabled = os.getenv("CHATGPT2API_FLARESOLVERR_ENABLED")
    url = os.getenv("CHATGPT2API_FLARESOLVERR_URL") or os.getenv("FLARESOLVERR_URL")
    max_timeout_ms = os.getenv("CHATGPT2API_FLARESOLVERR_MAX_TIMEOUT_MS")
    preload = os.getenv("CHATGPT2API_FLARESOLVERR_PRELOAD")
    if enabled is None and url is None and max_timeout_ms is None and preload is None:
        return {}
    return {
        key: value
        for key, value in {
            "enabled": enabled,
            "url": url,
            "max_timeout_ms": max_timeout_ms,
            "preload": preload,
        }.items()
        if value is not None
    }


def _probe_flaresolverr_url(api_url: str) -> bool:
    try:
        resp = requests.get(api_url.rstrip("/"), timeout=2, verify=False)
        return 200 <= int(resp.status_code) < 500
    except Exception:
        return False


def _auto_flaresolverr_config() -> dict[str, Any]:
    global _flaresolverr_autodetect_cache
    with _flaresolverr_autodetect_lock:
        if _flaresolverr_autodetect_cache is not None:
            return dict(_flaresolverr_autodetect_cache)
        auto_enabled = os.getenv("CHATGPT2API_FLARESOLVERR_AUTO")
        if auto_enabled is not None and not _truthy(auto_enabled, True):
            _flaresolverr_autodetect_cache = {}
            return {}
        for api_url in ("http://flaresolverr:8191", "http://127.0.0.1:8191", "http://localhost:8191"):
            if _probe_flaresolverr_url(api_url):
                _flaresolverr_autodetect_cache = {
                    "enabled": True,
                    "url": api_url,
                    "max_timeout_ms": os.getenv("CHATGPT2API_FLARESOLVERR_MAX_TIMEOUT_MS") or 60000,
                    "preload": os.getenv("CHATGPT2API_FLARESOLVERR_PRELOAD") or True,
                }
                return dict(_flaresolverr_autodetect_cache)
        _flaresolverr_autodetect_cache = {}
        return {}


def _current_flaresolverr_config() -> dict[str, Any]:
    if isinstance(_flaresolverr_runtime_override, dict):
        return _normalize_flaresolverr_config(_flaresolverr_runtime_override)
    raw = config.get("flaresolverr")
    explicit = False
    try:
        saved = json.loads(register_config_file.read_text(encoding="utf-8"))
        if isinstance(saved, dict) and "flaresolverr" in saved:
            raw = saved.get("flaresolverr")
            explicit = True
    except Exception:
        pass
    env_config = _env_flaresolverr_config()
    if env_config:
        explicit = True
    merged = dict(raw) if isinstance(raw, dict) else {}
    merged.update(env_config)
    normalized = _normalize_flaresolverr_config(merged)
    if normalized["enabled"] or (explicit and normalized["url"]):
        return normalized
    return _normalize_flaresolverr_config(_auto_flaresolverr_config())


def _flaresolverr_endpoint(api_url: str) -> str:
    return api_url if api_url.endswith("/v1") else f"{api_url}/v1"


def _split_proxy_for_flaresolverr(proxy: str) -> dict[str, str] | None:
    candidate = str(proxy or "").strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        return {"url": candidate}
    result: dict[str, str] = {"url": candidate}
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        result["url"] = urlunparse((parsed.scheme, host, parsed.path or "", "", parsed.query or "", ""))
        result["username"] = parsed.username or ""
        result["password"] = parsed.password or ""
    return result


def _sec_ch_ua_from_user_agent(value: str) -> str:
    match = re.search(r"(?:Chrome|Chromium)/(\d+)", value or "")
    major = match.group(1) if match else "145"
    return f'"Google Chrome";v="{major}", "Not?A_Brand";v="8", "Chromium";v="{major}"'


def _solution_cookie_names(solution: dict[str, Any]) -> list[str]:
    cookies = solution.get("cookies") if isinstance(solution, dict) else []
    if not isinstance(cookies, list):
        return []
    names = []
    for item in cookies:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _set_cookie_variants(session: requests.Session, name: str, value: str, domain: str, path: str) -> None:
    domains = [domain] if domain else []
    if domain.startswith("."):
        domains.append(domain.lstrip("."))
    if domain in {".openai.com", "openai.com", ".auth.openai.com", "auth.openai.com"}:
        domains.extend([".openai.com", "openai.com", "auth.openai.com"])
    domains = [item for idx, item in enumerate(domains) if item and item not in domains[:idx]]
    if not domains:
        session.cookies.set(name, value, path=path)
        return
    for candidate in domains:
        session.cookies.set(name, value, domain=candidate, path=path)


def _apply_flaresolverr_solution(session: requests.Session, solution: dict[str, Any]) -> tuple[int, str]:
    cookies = solution.get("cookies") if isinstance(solution, dict) else []
    applied = 0
    if isinstance(cookies, list):
        for item in cookies:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "")
            if not name:
                continue
            domain = str(item.get("domain") or "").strip()
            path = str(item.get("path") or "").strip() or "/"
            _set_cookie_variants(session, name, value, domain, path)
            applied += 1
    user_agent_value = str(solution.get("userAgent") or "").strip() if isinstance(solution, dict) else ""
    if user_agent_value:
        session.headers.update({"User-Agent": user_agent_value})
    return applied, user_agent_value


def solve_with_flaresolverr(session: requests.Session, target_url: str, proxy: str = "") -> tuple[int, str]:
    settings = _current_flaresolverr_config()
    if not settings["enabled"]:
        return 0, ""
    payload: dict[str, Any] = {
        "cmd": "request.get",
        "url": target_url,
        "maxTimeout": settings["max_timeout_ms"],
    }
    proxy_payload = _split_proxy_for_flaresolverr(proxy)
    if proxy_payload:
        payload["proxy"] = proxy_payload
    timeout = max(10.0, float(settings["max_timeout_ms"]) / 1000.0 + 10.0)
    resp = requests.post(_flaresolverr_endpoint(settings["url"]), json=payload, timeout=timeout, verify=False)
    data = _response_json(resp)
    if resp.status_code != 200:
        raise RuntimeError(f"flaresolverr_http_{resp.status_code}: {_response_debug_detail(resp)}")
    if str(data.get("status") or "").lower() != "ok":
        message = str(data.get("message") or data.get("error") or "unknown flaresolverr error")
        raise RuntimeError(f"flaresolverr_failed: {message}")
    solution = data.get("solution") if isinstance(data.get("solution"), dict) else {}
    solution_status = int(solution.get("status") or 0) if isinstance(solution, dict) else 0
    if solution_status >= 400:
        solution_url = str(solution.get("url") or "")[:300]
        cookie_names = ",".join(_solution_cookie_names(solution))
        raise RuntimeError(f"flaresolverr_solution_http_{solution_status}: url={solution_url}, cookies={cookie_names}")
    return _apply_flaresolverr_solution(session, solution)


def create_mailbox(username: str | None = None) -> dict:
    return mail_provider.create_mailbox(config["mail"], username)


def wait_for_code(mailbox: dict) -> str | None:
    return mail_provider.wait_for_code(config["mail"], mailbox)


from utils.sentinel import SentinelTokenGenerator, build_sentinel_token as _build_sentinel_token_tuple  # noqa: F401


def build_sentinel_token(session: requests.Session, device_id: str, flow: str, user_agent_override: str = "") -> str:
    """请求 sentinel token，返回 sentinel header 字符串（兼容旧接口）。"""
    ua = str(user_agent_override or user_agent)
    sentinel_val, _oai_sc_val = _build_sentinel_token_tuple(session, device_id, flow, user_agent=ua, sec_ch_ua=_sec_ch_ua_from_user_agent(ua))
    return sentinel_val


def create_session(proxy: str = "") -> Any:
    kwargs = {"impersonate": "chrome", "verify": False}
    if proxy:
        kwargs["proxy"] = proxy
    return requests.Session(**kwargs)


def request_with_local_retry(session: requests.Session, method: str, url: str, retry_attempts: int = 3, **kwargs):
    last_error = ""
    for _ in range(max(1, retry_attempts)):
        try:
            return session.request(method.upper(), url, timeout=default_timeout, **kwargs), ""
        except Exception as error:
            last_error = str(error)
            time.sleep(1)
    return None, last_error


def validate_otp(session: requests.Session, device_id: str, code: str, user_agent_override: str = ""):
    headers = dict(common_headers)
    ua = str(user_agent_override or user_agent)
    headers["user-agent"] = ua
    headers["sec-ch-ua"] = _sec_ch_ua_from_user_agent(ua)
    headers["referer"] = f"{auth_base}/email-verification"
    headers["oai-device-id"] = device_id
    headers.update(_make_trace_headers())
    resp, error = request_with_local_retry(session, "post", f"{auth_base}/api/accounts/email-otp/validate", json={"code": code}, headers=headers, verify=False)
    if resp is not None and resp.status_code == 200:
        return resp, ""
    headers["openai-sentinel-token"] = build_sentinel_token(session, device_id, "authorize_continue", ua)
    resp, error = request_with_local_retry(session, "post", f"{auth_base}/api/accounts/email-otp/validate", json={"code": code}, headers=headers, verify=False)
    return resp, error


def extract_oauth_callback_params_from_url(url: str) -> dict[str, str] | None:
    if not url:
        return None
    try:
        params = parse_qs(urlparse(url).query)
    except Exception:
        return None
    code = str((params.get("code") or [""])[0]).strip()
    if not code:
        return None
    return {"code": code, "state": str((params.get("state") or [""])[0]).strip(), "scope": str((params.get("scope") or [""])[0]).strip()}


def request_platform_oauth_token(session: requests.Session, code: str, code_verifier: str, user_agent_override: str = "") -> dict | None:
    ua = str(user_agent_override or user_agent)
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "auth0-client": platform_auth0_client,
        "cache-control": "no-cache",
        "content-type": "application/json",
        "origin": platform_base,
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": f"{platform_base}/",
        "sec-ch-ua": _sec_ch_ua_from_user_agent(ua),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": ua,
    }
    resp = session.post(
        f"{auth_base}/api/accounts/oauth/token",
        headers=headers,
        json={
            "client_id": platform_oauth_client_id,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": platform_oauth_redirect_uri,
        },
        verify=False,
        timeout=60,
    )
    if resp.status_code != 200:
        print(resp.text)
        return None
    return _response_json(resp)


class PlatformRegistrar:
    def __init__(self, proxy: str = "") -> None:
        self.proxy = str(proxy or "").strip()
        self.session = create_session(proxy)
        self.device_id = str(uuid.uuid4())
        self.code_verifier = ""
        self.platform_auth_code = ""
        self.user_agent = user_agent
        self.sec_ch_ua = sec_ch_ua

    def close(self) -> None:
        self.session.close()

    def _navigate_headers(self, referer: str = "") -> dict[str, str]:
        headers = dict(navigate_headers)
        headers["user-agent"] = self.user_agent
        headers["sec-ch-ua"] = self.sec_ch_ua
        if referer:
            headers["referer"] = referer
        return headers

    def _json_headers(self, referer: str) -> dict[str, str]:
        headers = dict(common_headers)
        headers["user-agent"] = self.user_agent
        headers["sec-ch-ua"] = self.sec_ch_ua
        headers["referer"] = referer
        headers["oai-device-id"] = self.device_id
        headers.update(_make_trace_headers())
        return headers

    def _solve_with_flaresolverr(self, target_url: str, index: int) -> bool:
        settings = _current_flaresolverr_config()
        if not settings["enabled"]:
            return False
        step(index, f"开始 FlareSolverr 预热 Cloudflare（{_proxy_log_label(self.proxy)}）")
        cookie_count, solved_user_agent = solve_with_flaresolverr(self.session, target_url, self.proxy)
        if solved_user_agent:
            self.user_agent = solved_user_agent
            self.sec_ch_ua = _sec_ch_ua_from_user_agent(solved_user_agent)
        step(index, f"FlareSolverr 完成，导入 {cookie_count} 个 cookie")
        return True

    def _platform_authorize(self, email: str, index: int) -> None:
        step(index, "开始 platform authorize")
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")
        self.code_verifier, code_challenge = _generate_pkce()
        params = {
            "issuer": auth_base,
            "client_id": platform_oauth_client_id,
            "audience": platform_oauth_audience,
            "redirect_uri": platform_oauth_redirect_uri,
            "device_id": self.device_id,
            "screen_hint": "login_or_signup",
            "max_age": "0",
            "login_hint": email,
            "scope": "openid profile email offline_access",
            "response_type": "code",
            "response_mode": "query",
            "state": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(32),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "auth0Client": platform_auth0_client,
        }
        authorize_url = f"{auth_base}/api/accounts/authorize?{urlencode(params)}"
        flaresolverr_settings = _current_flaresolverr_config()
        solved_with_flaresolverr = False
        if flaresolverr_settings["enabled"] and flaresolverr_settings["preload"]:
            solved_with_flaresolverr = self._solve_with_flaresolverr(authorize_url, index)
        resp, error = request_with_local_retry(self.session, "get", authorize_url, headers=self._navigate_headers(f"{platform_base}/"), allow_redirects=True, verify=False)
        if _is_cloudflare_challenge(resp) and flaresolverr_settings["enabled"]:
            retry_url = str(getattr(resp, "url", "") or authorize_url)
            step(index, "FlareSolverr 预热后仍被 Cloudflare 拦截，重新求解并重试", "yellow")
            self._solve_with_flaresolverr(retry_url, index)
            resp, error = request_with_local_retry(self.session, "get", authorize_url, headers=self._navigate_headers(f"{platform_base}/"), allow_redirects=True, verify=False)
        if resp is None or resp.status_code != 200:
            err = _response_json(resp).get("error", {}) if resp is not None else {}
            detail = f": {err.get('code', '')} - {err.get('message', '')}".strip(" -") if err else ""
            if _is_cloudflare_challenge(resp):
                raise RuntimeError("被 Cloudflare 拦截，请配置/检查 FlareSolverr 或更换 IP 重试")
            debug = _response_debug_detail(resp)
            status = getattr(resp, "status_code", "unknown")
            raise RuntimeError(error or f"platform_authorize_http_{status}{detail}, {debug}")
        step(index, "platform authorize 完成")

    def _register_user(self, email: str, password: str, index: int) -> None:
        step(index, "开始提交注册密码")
        headers = self._json_headers(f"{auth_base}/create-account/password")
        headers["openai-sentinel-token"] = build_sentinel_token(self.session, self.device_id, "username_password_create", self.user_agent)
        resp, error = request_with_local_retry(self.session, "post", f"{auth_base}/api/accounts/user/register", json={"username": email, "password": password}, headers=headers, verify=False)
        if resp is None or resp.status_code != 200:
            data = _response_json(resp) if resp is not None else {}
            if data.get("message") == "Failed to create account. Please try again.":
                step(index, "注册失败提示: 邮箱域名很可能因滥用被封禁，请更换邮箱域名", "yellow")
            detail = f", detail={json.dumps(data, ensure_ascii=False)}" if data else ""
            raise RuntimeError(error or f"user_register_http_{getattr(resp, 'status_code', 'unknown')}{detail}")
        step(index, "提交注册密码完成")

    def _send_otp(self, index: int) -> None:
        step(index, "开始发送验证码")
        resp, error = request_with_local_retry(self.session, "get", f"{auth_base}/api/accounts/email-otp/send", headers=self._navigate_headers(f"{auth_base}/create-account/password"), allow_redirects=True, verify=False)
        if resp is None or resp.status_code not in (200, 302):
            raise RuntimeError(error or f"send_otp_http_{getattr(resp, 'status_code', 'unknown')}")
        step(index, "发送验证码完成")

    def _validate_otp(self, code: str, index: int) -> None:
        step(index, f"开始校验验证码 {code}")
        resp, error = validate_otp(self.session, self.device_id, code, self.user_agent)
        if resp is None or resp.status_code != 200:
            body = ""
            try:
                body = (resp.text or "")[:500] if resp is not None else ""
            except Exception:
                pass
            raise RuntimeError(error or f"validate_otp_http_{getattr(resp, 'status_code', 'unknown')}_body={body}")
        step(index, "验证码校验完成")

    def _create_account(self, name: str, birthdate: str, index: int) -> None:
        step(index, "开始创建账号资料")
        headers = self._json_headers(f"{auth_base}/about-you")
        headers["openai-sentinel-token"] = build_sentinel_token(self.session, self.device_id, "oauth_create_account", self.user_agent)
        resp, error = request_with_local_retry(self.session, "post", f"{auth_base}/api/accounts/create_account", json={"name": name, "birthdate": birthdate}, headers=headers, verify=False)
        if resp is None or resp.status_code not in (200, 302):
            data = _response_json(resp) if resp is not None else {}
            if data.get("message") == "Failed to create account. Please try again.":
                step(index, "创建账号失败提示: 邮箱域名很可能因滥用被封禁，请更换邮箱域名", "yellow")
            detail = f", detail={json.dumps(data, ensure_ascii=False)}" if data else ""
            raise RuntimeError(error or f"create_account_http_{getattr(resp, 'status_code', 'unknown')}{detail}")
        data = _response_json(resp)
        callback_params = extract_oauth_callback_params_from_url(str(data.get("continue_url") or "").strip())
        self.platform_auth_code = str((callback_params or {}).get("code") or "").strip()
        step(index, "创建账号资料完成")

    def _exchange_registered_tokens(self, index: int) -> dict:
        step(index, "开始换 token")
        tokens = request_platform_oauth_token(self.session, self.platform_auth_code, self.code_verifier, self.user_agent)
        if not tokens:
            raise RuntimeError("token换取失败")
        step(index, "token 换取完成")
        return tokens

    def register(self, index: int) -> dict:
        step(index, "开始创建邮箱")
        mailbox = create_mailbox()
        email = str(mailbox.get("address") or "").strip()
        if not email:
            mail_provider.release_mailbox(mailbox)
            raise RuntimeError("邮箱服务未返回 address")
        label = str(mailbox.get("label") or "")
        step(index, f"邮箱创建完成[{label}]: {email}")
        try:
            password = _random_password()
            first_name, last_name = _random_name()
            self._platform_authorize(email, index)
            self._register_user(email, password, index)
            self._send_otp(index)
            step(index, "开始等待注册验证码")
            code = wait_for_code(mailbox)
            if not code:
                raise RuntimeError("等待注册验证码超时")
            step(index, f"收到注册验证码: {code}")
            self._validate_otp(code, index)
            self._create_account(f"{first_name} {last_name}", _random_birthdate(), index)
            tokens = self._exchange_registered_tokens(index)
        except Exception as error:
            mail_provider.mark_mailbox_result(mailbox, success=False, error=error)
            raise
        mail_provider.mark_mailbox_result(mailbox, success=True)
        return {
            "email": email,
            "password": password,
            "access_token": str(tokens.get("access_token") or "").strip(),
            "refresh_token": str(tokens.get("refresh_token") or "").strip(),
            "id_token": str(tokens.get("id_token") or "").strip(),
            "source_type": "web",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


def worker(index: int) -> dict:
    start = time.time()
    proxy, proxy_label = resolve_register_proxy()
    registrar = PlatformRegistrar(proxy)
    try:
        step(index, f"任务启动（{proxy_label}）")
        result = registrar.register(index)
        cost = time.time() - start
        account_service.add_account_items([result])
        with stats_lock:
            stats["done"] += 1
            stats["success"] += 1
            avg = (time.time() - stats["start_time"]) / stats["success"]
        log(f'{result["email"]} 注册成功，本次耗时{cost:.1f}s，全局平均每个号注册耗时{avg:.1f}s', "green")
        return {"ok": True, "index": index, "result": result}
    except Exception as e:
        cost = time.time() - start
        with stats_lock:
            stats["done"] += 1
            stats["fail"] += 1
        log(f"任务{index} 注册失败，本次耗时{cost:.1f}s，原因: {e}", "red")
        return {"ok": False, "index": index, "error": str(e)}
    finally:
        registrar.close()
