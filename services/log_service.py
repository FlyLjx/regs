from __future__ import annotations

LOG_TYPE_ACCOUNT = "account"


class _LogService:
    def add(self, log_type: str, message: str, payload: dict | None = None) -> None:
        return


log_service = _LogService()
