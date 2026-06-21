from __future__ import annotations

from pathlib import Path

from services.storage.json_storage import JsonStorage


def create_storage_backend(data_dir: Path) -> JsonStorage:
    return JsonStorage(data_dir)
