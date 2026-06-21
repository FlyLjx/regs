from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUI_FILE = PROJECT_ROOT / "reg" / "gui.py"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
REGISTER_CONFIG_FILE = PROJECT_ROOT / "reg" / "register.json"


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "chatgpt2api-reg",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--add-data",
        f"{REGISTER_CONFIG_FILE};.",
        str(GUI_FILE),
    ]
    print("Running:", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
