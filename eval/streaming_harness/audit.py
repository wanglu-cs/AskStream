from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonlWriter:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("a", encoding="utf-8", newline="\n")

    def write(self, payload: dict[str, Any]) -> None:
        self._file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
