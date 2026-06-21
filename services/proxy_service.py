from __future__ import annotations

from dataclasses import dataclass

from services.config import config


@dataclass(frozen=True)
class ProxyRuntimeProfile:
    proxy_url: str = ""
    proxy_source: str = "direct"


class ProxySettingsStore:
    def get_profile(
        self,
        account: dict | None = None,
        proxy: str = "",
        resource: bool = False,
        upstream: bool = False,
        allow_runtime_proxy: bool = True,
    ) -> ProxyRuntimeProfile:
        account_proxy = str((account or {}).get("proxy") or "").strip() if isinstance(account, dict) else ""
        explicit_proxy = str(proxy or "").strip()
        global_proxy = str(config.get_proxy_settings() or "").strip()
        if account_proxy:
            return ProxyRuntimeProfile(proxy_url=account_proxy, proxy_source="account")
        if explicit_proxy:
            return ProxyRuntimeProfile(proxy_url=explicit_proxy, proxy_source="explicit")
        if global_proxy:
            return ProxyRuntimeProfile(proxy_url=global_proxy, proxy_source="global")
        return ProxyRuntimeProfile()

    def build_session_kwargs(
        self,
        account: dict | None = None,
        proxy: str = "",
        resource: bool = False,
        upstream: bool = False,
        allow_runtime_proxy: bool = True,
        **session_kwargs,
    ) -> dict[str, object]:
        profile = self.get_profile(
            account=account,
            proxy=proxy,
            resource=resource,
            upstream=upstream,
            allow_runtime_proxy=allow_runtime_proxy,
        )
        if profile.proxy_url:
            session_kwargs["proxy"] = profile.proxy_url
        return session_kwargs


proxy_settings = ProxySettingsStore()
