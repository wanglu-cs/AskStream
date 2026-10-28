#!/usr/bin/env python3
"""Local HTTP server for the video uncertainty annotation interface.

The server deliberately uses only Python's standard library.  It serves the
repository as static files (including byte ranges for video seeking) and owns
the small JSON persistence API used by the annotation page.
"""

from __future__ import annotations

import argparse
import email.utils
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import threading
from datetime import timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO, Optional, Tuple
from urllib.parse import unquote, urlsplit


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 32 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ANNOTATION_FILE = Path(__file__).resolve().parent / "video_uncertainty_annotations.json"
TRACKED_VIDEO_ROOT = (
    REPOSITORY_ROOT / "data" / "COIN" / "processed" / "video"
)
LEGACY_TRACKED_VIDEO_ROOT = (
    REPOSITORY_ROOT / "data" / "COIN" / "processed" / "video_processed"
)
VIDEO_VERIFICATION_FILE = TRACKED_VIDEO_ROOT / "youtube_video_only_verification_20260722.json"

LOGGER = logging.getLogger("video_uncertainty_annotation")
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)\Z", re.IGNORECASE)


class AnnotationHTTPServer(ThreadingHTTPServer):
    """Threaded server carrying immutable paths and a persistence lock."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        static_root: Path,
        annotation_file: Path,
        tracked_video_root: Path,
        initial_document: Optional[dict] = None,
    ) -> None:
        super().__init__(server_address, AnnotationRequestHandler)
        self.static_root = static_root.resolve()
        self.annotation_file = annotation_file.resolve()
        self.tracked_video_root = tracked_video_root.resolve()
        self.initial_document = initial_document or {}
        self.annotation_lock = threading.Lock()


class AnnotationRequestHandler(BaseHTTPRequestHandler):
    """Serve static assets and the annotation JSON API."""

    protocol_version = "HTTP/1.1"
    server_version = "VideoUncertaintyAnnotation/1.0"

    @property
    def app_server(self) -> AnnotationHTTPServer:
        return self.server  # type: ignore[return-value]

    def handle(self) -> None:
        """Silence normal browser disconnects while a video range is in flight."""

        try:
            super().handle()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            LOGGER.debug("Client disconnected before the response completed")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = self._route_path()
        if route == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": "video-uncertainty-annotation",
                    "annotation_file": str(
                        self.app_server.annotation_file.relative_to(
                            self.app_server.static_root
                        )
                    ).replace(os.sep, "/"),
                },
            )
            return
        if route == "/api/annotations":
            self._get_annotations()
            return
        if route == "/api/tracked-videos":
            self._get_tracked_videos()
            return
        if route.startswith("/api/"):
            self._send_error_json(HTTPStatus.NOT_FOUND, "API endpoint not found")
            return
        self._serve_static(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = self._route_path()
        if route == "/api/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"}, send_body=False)
            return
        if route == "/api/annotations":
            self._get_annotations(send_body=False)
            return
        if route == "/api/tracked-videos":
            self._get_tracked_videos(send_body=False)
            return
        if route.startswith("/api/"):
            self._send_error_json(
                HTTPStatus.NOT_FOUND, "API endpoint not found", send_body=False
            )
            return
        self._serve_static(send_body=False)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_write_request()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_write_request()

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self._route_path() == "/api/annotations":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Allow", "GET, HEAD, POST, PUT, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "API endpoint not found")

    def _route_path(self) -> str:
        """Return a normalized URL path for routing, retaining root slash."""

        try:
            path = urlsplit(self.path).path
        except ValueError:
            return ""
        if path != "/":
            path = path.rstrip("/")
        return path

    def _get_annotations(self, send_body: bool = True) -> None:
        path = self.app_server.annotation_file
        try:
            with self.app_server.annotation_lock:
                if not path.exists():
                    payload = self.app_server.initial_document
                else:
                    with path.open("r", encoding="utf-8") as source:
                        payload = json.load(source)
                    if not isinstance(payload, dict):
                        raise ValueError("stored annotation JSON is not an object")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            LOGGER.exception("Cannot read annotation file %s", path)
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "The annotation file could not be read",
                send_body=send_body,
            )
            return
        self._send_json(HTTPStatus.OK, payload, send_body=send_body)

    def _get_tracked_videos(self, send_body: bool = True) -> None:
        try:
            payload = build_tracked_video_manifest(
                self.app_server.tracked_video_root,
                self.app_server.static_root,
            )
        except (OSError, ValueError):
            LOGGER.exception(
                "Cannot build tracked video manifest from %s",
                self.app_server.tracked_video_root,
            )
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "The tracked video manifest could not be built",
                send_body=send_body,
            )
            return
        self._send_json(HTTPStatus.OK, payload, send_body=send_body)

    def _handle_write_request(self) -> None:
        if self._route_path() != "/api/annotations":
            self.close_connection = True
            self._send_error_json(HTTPStatus.NOT_FOUND, "API endpoint not found")
            return

        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                "Transfer-Encoding is not supported; send Content-Length",
            )
            return

        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._send_error_json(
                HTTPStatus.LENGTH_REQUIRED, "A valid Content-Length is required"
            )
            return
        if length > MAX_REQUEST_BYTES:
            self.close_connection = True
            self._send_error_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"Request body exceeds the {MAX_REQUEST_BYTES} byte limit",
            )
            return

        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            self.close_connection = True
            self._send_error_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Type must be application/json",
            )
            return

        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Incomplete request body")
            return

        try:
            payload = json.loads(
                body.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {value}")
                ),
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "Body must be valid UTF-8 JSON")
            return
        if not isinstance(payload, dict):
            self._send_error_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "The annotation document must be a JSON object",
            )
            return

        try:
            self._save_annotations(payload)
        except (OSError, TypeError, ValueError):
            LOGGER.exception(
                "Cannot save annotation file %s", self.app_server.annotation_file
            )
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "The annotation file could not be saved",
            )
            return

        LOGGER.info(
            "Saved annotations (%d request bytes) to %s",
            length,
            self.app_server.annotation_file,
        )
        self._send_json(HTTPStatus.OK, {"ok": True, "saved": True})

    def _save_annotations(self, payload: dict) -> None:
        path = self.app_server.annotation_file
        serialized = (
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
        temporary_path: Optional[Path] = None

        with self.app_server.annotation_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                self._write_backup(path)

            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=path.parent,
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(serialized)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_path, path)
                temporary_path = None
            finally:
                if temporary_path is not None:
                    try:
                        temporary_path.unlink()
                    except FileNotFoundError:
                        pass

    @staticmethod
    def _write_backup(path: Path) -> None:
        """Atomically refresh ``<annotation file>.bak`` with the old contents."""

        backup = path.with_name(f"{path.name}.bak")
        temporary_path: Optional[Path] = None
        try:
            with path.open("rb") as source, tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{backup.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                shutil.copyfileobj(source, temporary, COPY_CHUNK_BYTES)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, backup)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def _serve_static(self, send_body: bool) -> None:
        path = self._static_path()
        if path is None:
            self._send_error_json(HTTPStatus.FORBIDDEN, "Static path is outside root")
            return

        if path.is_dir():
            parsed = urlsplit(self.path)
            if not parsed.path.endswith("/"):
                location = parsed.path + "/"
                if parsed.query:
                    location += "?" + parsed.query
                self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path = path / "index.html"

        try:
            stat = path.stat()
            if not path.is_file():
                raise FileNotFoundError
            source = path.open("rb")
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            self._send_error_json(HTTPStatus.NOT_FOUND, "Static file not found")
            return

        with source:
            etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
            selected_range: Optional[Tuple[int, int]] = None
            range_header = self.headers.get("Range")
            if range_header and self._if_range_matches(etag, stat.st_mtime):
                selected_range = self._parse_range(range_header, stat.st_size)
                if selected_range is None:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{stat.st_size}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

            content_type, encoding = mimetypes.guess_type(str(path))
            content_type = content_type or "application/octet-stream"
            if content_type.startswith("text/"):
                content_type += "; charset=utf-8"

            if selected_range is None:
                start, end = 0, stat.st_size - 1
                status = HTTPStatus.OK
                content_length = stat.st_size
            else:
                start, end = selected_range
                status = HTTPStatus.PARTIAL_CONTENT
                content_length = end - start + 1

            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.send_header("Content-Length", str(content_length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", email.utils.formatdate(stat.st_mtime, usegmt=True))
            if selected_range is not None:
                self.send_header(
                    "Content-Range", f"bytes {start}-{end}/{stat.st_size}"
                )
            self.end_headers()

            if not send_body or content_length == 0:
                return
            try:
                source.seek(start)
                self._copy_bytes(source, content_length)
            except (BrokenPipeError, ConnectionResetError):
                LOGGER.debug("Client disconnected while receiving %s", path)

    def _static_path(self) -> Optional[Path]:
        try:
            decoded = unquote(urlsplit(self.path).path, errors="strict")
            candidate = (
                self.app_server.static_root / decoded.lstrip("/").replace("/", os.sep)
            ).resolve()
            if os.path.commonpath(
                (str(self.app_server.static_root), str(candidate))
            ) != str(self.app_server.static_root):
                return None
            return candidate
        except (UnicodeError, ValueError, OSError):
            return None

    def _if_range_matches(self, etag: str, modified_time: float) -> bool:
        value = self.headers.get("If-Range")
        if not value:
            return True
        if value.startswith('"') or value.startswith("W/"):
            return value == etag
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(modified_time) <= int(parsed.timestamp())
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _parse_range(value: str, size: int) -> Optional[Tuple[int, int]]:
        """Parse a single RFC 7233 byte range, returning inclusive bounds."""

        match = _RANGE_RE.fullmatch(value.strip())
        if match is None or size <= 0:
            return None
        first, last = match.groups()
        if not first and not last:
            return None

        if not first:
            suffix_length = int(last)
            if suffix_length <= 0:
                return None
            return max(size - suffix_length, 0), size - 1

        start = int(first)
        if start >= size:
            return None
        if not last:
            return start, size - 1

        end = int(last)
        if end < start:
            return None
        return start, min(end, size - 1)

    def _copy_bytes(self, source: BinaryIO, count: int) -> None:
        remaining = count
        while remaining:
            block = source.read(min(COPY_CHUNK_BYTES, remaining))
            if not block:
                break
            self.wfile.write(block)
            remaining -= len(block)

    def _send_json(
        self, status: HTTPStatus, payload: object, send_body: bool = True
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _send_error_json(
        self, status: HTTPStatus, message: str, send_body: bool = True
    ) -> None:
        self._send_json(
            status,
            {"ok": False, "error": message, "status": int(status)},
            send_body=send_body,
        )

    def log_message(self, format_string: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format_string % args)


def build_tracked_video_manifest(tracked_root: Path, static_root: Path) -> dict:
    """Build one manifest from the legacy tracked set and verified YouTube videos."""

    tracked_root = tracked_root.resolve()
    static_root = static_root.resolve()
    if not tracked_root.is_dir():
        return {"source_root": [], "count": 0, "videos": []}
    try:
        source_root = tracked_root.relative_to(static_root)
    except ValueError as exc:
        raise ValueError("tracked video root must be inside the static root") from exc

    verification_path = tracked_root / VIDEO_VERIFICATION_FILE.name
    if not verification_path.is_file():
        raise ValueError(f"verification report does not exist: {verification_path}")
    with verification_path.open("r", encoding="utf-8") as source:
        verification = json.load(source)
    entries = verification.get("videos") if isinstance(verification, dict) else None
    if not isinstance(entries, list):
        raise ValueError("verification report does not contain a videos list")

    videos = []
    legacy_root = LEGACY_TRACKED_VIDEO_ROOT.resolve()
    if not legacy_root.is_dir():
        raise ValueError(f"legacy tracked video root does not exist: {legacy_root}")
    for directory in sorted(legacy_root.iterdir(), key=lambda path: path.name.lower()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        video_id = directory.name
        video_path = directory / f"{video_id}.mp4"
        metadata_path = directory / "metadata.json"
        if not video_path.is_file() or not metadata_path.is_file():
            continue
        with metadata_path.open("r", encoding="utf-8") as source:
            metadata = json.load(source)
        if not isinstance(metadata, dict):
            raise ValueError(f"metadata is not an object: {metadata_path}")
        relative_video = video_path.resolve().relative_to(static_root)
        relative_metadata = metadata_path.resolve().relative_to(static_root)
        videos.append(
            {
                "id": video_id,
                "filename": video_path.name,
                "uri": f"/{relative_video.as_posix()}",
                "metadata_uri": f"/{relative_metadata.as_posix()}",
                "size_bytes": video_path.stat().st_size,
                "tracking_mode": metadata.get("tracking_mode"),
                "fps": metadata.get("fps"),
                "frame_count": metadata.get("frame_count"),
                "object_count": len(metadata.get("objects") or {}),
                "source_video": metadata.get("source_video"),
            }
        )

    existing_ids = {item["id"] for item in videos}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("file"):
            raise ValueError("verification report contains an invalid video entry")
        video_id = str(entry["id"])
        if video_id in existing_ids:
            raise ValueError(f"duplicate video id across sources: {video_id}")
        reported_path = Path(str(entry["file"]))
        video_path = reported_path if reported_path.is_absolute() else static_root / reported_path
        video_path = video_path.resolve()
        try:
            relative_video = video_path.relative_to(static_root)
        except ValueError as exc:
            raise ValueError(f"video is outside the static root: {video_path}") from exc
        if not video_path.is_file() or video_path.parent.name != video_id:
            raise ValueError(f"verified video is missing or misplaced: {video_path}")

        info = {}
        info_path = video_path.with_suffix(".info.json")
        if info_path.is_file():
            with info_path.open("r", encoding="utf-8") as source:
                info = json.load(source)
            if not isinstance(info, dict):
                raise ValueError(f"video info is not an object: {info_path}")

        videos.append(
            {
                "id": video_id,
                "filename": video_path.name,
                "uri": f"/{relative_video.as_posix()}",
                "size_bytes": video_path.stat().st_size,
                "tracking_mode": "youtube_video_only",
                "duration_s": info.get("duration"),
                "fps": entry.get("fps") or info.get("fps"),
                "width": entry.get("width") or info.get("width"),
                "height": entry.get("height") or info.get("height"),
                "format_id": info.get("format_id"),
                "codec": entry.get("codec") or info.get("vcodec"),
                "audio_streams": entry.get("audio_streams", 0),
                "source_video": info.get("webpage_url"),
                "object_count": 0,
            }
        )
        existing_ids.add(video_id)

    return {
        "source_root": [
            f"/{legacy_root.relative_to(static_root).as_posix()}",
            f"/{source_root.as_posix()}",
        ],
        "count": len(videos),
        "videos": videos,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the video uncertainty annotation interface and API."
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"address to bind (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP port to bind (default: {DEFAULT_PORT})",
    )
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    return args


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    server = AnnotationHTTPServer(
        (args.host, args.port),
        REPOSITORY_ROOT,
        ANNOTATION_FILE,
        TRACKED_VIDEO_ROOT,
        {
            "schema_version": "pvb.video_uncertainty.v6",
            "benchmark": "AskStream",
            "task": "video_information_uncertainty",
            "videos": {},
            "revision": 0,
            "ui_state": {"last_video_id": None},
        },
    )
    host, port = server.server_address[:2]
    LOGGER.info("Serving repository root: %s", server.static_root)
    LOGGER.info("Annotation file: %s", server.annotation_file)
    LOGGER.info("Tracked video root: %s", server.tracked_video_root)
    LOGGER.info(
        "Open http://%s:%s/web/02_video_annoation/",
        host,
        port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping server")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
