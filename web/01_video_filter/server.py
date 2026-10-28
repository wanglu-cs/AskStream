#!/usr/bin/env python3
"""Video Filter server; reuse the annotation server's atomic persistence unchanged."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = APP_ROOT.parents[1]
spec = importlib.util.spec_from_file_location(
    "video_filter_annotation_server",
    APP_ROOT.parent / "02_video_annoation" / "server.py",
)
annotation_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(annotation_server)
DATA_FILE = APP_ROOT / "video_filter_data.json"


class VideoFilterRequestHandler(annotation_server.AnnotationRequestHandler):
    def _get_annotations(self, send_body: bool = True) -> None:
        if self.app_server.annotation_file.exists():
            super()._get_annotations(send_body=send_body)
            return
        self._send_json(200, {"records": [], "decisions": {}}, send_body=send_body)

    def _get_tracked_videos(self, send_body: bool = True) -> None:
        self._send_error_json(HTTPStatus.NOT_FOUND, "API endpoint not found", send_body=send_body)


class VideoFilterHTTPServer(annotation_server.AnnotationHTTPServer):
    def __init__(self, address, data_file: Path = DATA_FILE):
        super().__init__(address, REPOSITORY_ROOT, data_file, APP_ROOT)
        self.RequestHandlerClass = VideoFilterRequestHandler


def main() -> int:
    annotation_server.DEFAULT_PORT = 10001
    args = annotation_server.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = VideoFilterHTTPServer((args.host, args.port))
    logging.info("Video Filter data: %s", DATA_FILE)
    logging.info(
        "Open http://%s:%s/web/01_video_filter/",
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
