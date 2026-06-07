"""File persistence helpers for app configuration."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(filepath: str, data: dict[str, Any]) -> None:
    """Write JSON atomically by replacing the target with a complete temp file."""
    target = Path(filepath)
    target_dir = target.parent if str(target.parent) else Path(".")
    target_dir.mkdir(parents=True, exist_ok=True)
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target_dir,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(data, temp_file, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

