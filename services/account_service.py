from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any

from services.config import config


class AccountService:
    def __init__(self) -> None:
        self.storage = config.get_storage_backend()
        self._lock = Lock()
        self._accounts: dict[str, dict[str, Any]] = {}
        for item in self.storage.load_accounts():
            token = str(item.get("access_token") or "").strip()
            if token:
                self._accounts[token] = dict(item)

    def _save(self) -> None:
        self.storage.save_accounts(list(self._accounts.values()))

    def add_account_items(self, items: list[dict[str, Any]]) -> dict[str, int]:
        added = 0
        with self._lock:
            for item in items:
                if not isinstance(item, dict):
                    continue
                token = str(item.get("access_token") or "").strip()
                if not token:
                    continue
                payload = dict(item)
                payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                if token not in self._accounts:
                    added += 1
                self._accounts[token] = payload
            self._save()
        return {"added": added}

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._accounts.values()]


account_service = AccountService()
