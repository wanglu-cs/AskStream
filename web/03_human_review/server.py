#!/usr/bin/env python3
"""Serve Human Review with annotation persistence in this directory."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = APP_ROOT.parents[1]
spec = importlib.util.spec_from_file_location(
    "human_review_annotation_server",
    APP_ROOT.parent / "02_video_annoation" / "server.py",
)
annotation_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(annotation_server)

DATA_FILE = APP_ROOT / "human_review_annotations.json"


def main() -> int:
    annotation_server.DEFAULT_PORT = 10003
    args = annotation_server.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = annotation_server.AnnotationHTTPServer(
        (args.host, args.port),
        REPOSITORY_ROOT,
        DATA_FILE,
        annotation_server.TRACKED_VIDEO_ROOT,
        {
            "schema_version": "pvb.video_uncertainty.v6",
            "benchmark": "AskStream",
            "task": "video_information_uncertainty",
            "videos": {},
            "revision": 0,
            "ui_state": {"last_video_id": None},
        },
    )
    logging.info("Human Review data: %s", DATA_FILE)
    logging.info(
        "Open http://%s:%s/web/03_human_review/",
        *server.server_address[:2],
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
