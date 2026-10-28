"""Paths rooted at AskStream, independent of the process working directory."""

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "model"
DATA_ROOT = Path(os.environ.get("ASKSTREAM_DATA_ROOT", ROOT / "data")).expanduser().resolve()
RESULT_ROOT = Path(os.environ.get("ASKSTREAM_RESULT_ROOT", ROOT / "result")).expanduser().resolve()
DEFAULT_PRIVACY_FILE = MODEL_ROOT / "offline_api" / ".privacy.json"
