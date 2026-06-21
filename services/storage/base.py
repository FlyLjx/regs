from __future__ import annotations

from pathlib import Path
from typing import Protocol


class StorageBackend(Protocol):
    data_dir: Path

    def load_accounts(self) -> list[dict]:
        ...

    def save_accounts(self, items: list[dict]) -> None:
        ...
