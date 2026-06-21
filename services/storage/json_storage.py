from __future__ import annotations

import json
from pathlib import Path


class JsonStorage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.accounts_file = self.data_dir / "accounts.json"

    def load_accounts(self) -> list[dict]:
        if not self.accounts_file.exists():
            return []
        try:
            raw = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def save_accounts(self, items: list[dict]) -> None:
        self.accounts_file.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
