from __future__ import annotations

import base64
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class EncodedFrame:
    timestamp_s: float
    jpeg_bytes: bytes
    width: int
    height: int
    source: str
    requested_timestamp_s: float | None = None
    timestamp_source: str = "decoded_pts"

    @property
    def data_url(self) -> str:
        payload = base64.b64encode(self.jpeg_bytes).decode("ascii")
        return f"data:image/jpeg;base64,{payload}"

    def audit_dict(self) -> dict[str, object]:
        return {
            "timestamp_s": self.timestamp_s,
            "requested_timestamp_s": self.requested_timestamp_s,
            "timestamp_source": self.timestamp_source,
            "width": self.width,
            "height": self.height,
            "source": self.source,
            "jpeg_bytes": len(self.jpeg_bytes),
            "sha256": hashlib.sha256(self.jpeg_bytes).hexdigest(),
        }


class VideoFrameSampler:
    def __init__(
        self,
        path: Path,
        *,
        max_pixels: int,
        jpeg_quality: int,
        source_label: str,
    ) -> None:
        self.path = path.resolve()
        self.max_pixels = max_pixels
        self.jpeg_quality = jpeg_quality
        self.source_label = source_label
        try:
            import av
        except ImportError as exc:
            raise RuntimeError(
                "PyAV is required for causal frame sampling with real PTS; "
                "install the streaming harness requirements"
            ) from exc
        try:
            self._av_container = av.open(str(self.path))
            self._av_stream = self._av_container.streams.video[0]
            self._av_stream.thread_type = "AUTO"
        except Exception as exc:
            raise OSError(f"unable to open video with PyAV: {self.path}: {exc}") from exc
        self.duration_s = self._read_duration()

    def close(self) -> None:
        if self._av_container is not None:
            self._av_container.close()
            self._av_container = None
            self._av_stream = None

    def __enter__(self) -> "VideoFrameSampler":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def frame_at(self, timestamp_s: float) -> EncodedFrame:
        requested_timestamp = max(timestamp_s, 0.0)
        if self.duration_s > 0.0:
            # Evidence remains visible after its last frame. Seek just inside
            # the presentation interval and let the PTS-aware decoder choose
            # the latest real frame at or before that point.
            decode_timestamp = min(
                requested_timestamp,
                max(
                    0.0,
                    self.duration_s - float(self._av_stream.time_base),
                ),
            )
        else:
            decode_timestamp = requested_timestamp
        frame, decoded_timestamp, timestamp_source = self._frame_at_with_pyav(
            decode_timestamp
        )
        if decoded_timestamp > requested_timestamp + 1e-9:
            raise RuntimeError(
                f"non-causal decode for {self.path}: requested "
                f"{requested_timestamp:.9f}s, decoded {decoded_timestamp:.9f}s"
            )
        return encode_frame(
            frame,
            timestamp_s=decoded_timestamp,
            requested_timestamp_s=requested_timestamp,
            timestamp_source=timestamp_source,
            max_pixels=self.max_pixels,
            jpeg_quality=self.jpeg_quality,
            source=self.source_label,
        )

    @property
    def end_tolerance_s(self) -> float:
        """Duration margin covered by the final decodable frame."""

        assert self._av_stream is not None
        candidates = [float(self._av_stream.time_base)]
        if self._av_stream.average_rate:
            candidates.append(1.0 / float(self._av_stream.average_rate))
        return max(0.0, *candidates)

    def _frame_at_with_pyav(
        self, requested_timestamp: float
    ) -> tuple[np.ndarray, float, str]:
        assert self._av_container is not None and self._av_stream is not None
        stream = self._av_stream
        stream_start_pts = int(stream.start_time or 0)
        target_pts = stream_start_pts + int(
            requested_timestamp / float(stream.time_base)
        )
        self._av_container.seek(
            target_pts, stream=stream, any_frame=False, backward=True
        )
        selected = None
        selected_timestamp = float("nan")
        for candidate in self._av_container.decode(stream):
            if candidate.pts is None or candidate.time_base is None:
                continue
            candidate_timestamp = float(
                (candidate.pts - stream_start_pts) * candidate.time_base
            )
            if candidate_timestamp <= requested_timestamp + 1e-9:
                selected = candidate
                selected_timestamp = candidate_timestamp
                continue
            break
        if selected is None:
            raise EOFError(
                f"unable to find a causal frame in {self.path} at "
                f"{requested_timestamp:.3f}s"
            )
        return selected.to_ndarray(format="bgr24"), selected_timestamp, "decoded_pts"

    def sample_range(
        self,
        start_s: float,
        end_s: float,
        *,
        fps: float = 1.0,
        max_frames: int = 10,
    ) -> list[EncodedFrame]:
        if end_s <= start_s:
            return [self.frame_at(start_s)]
        step = 1.0 / fps
        frames: list[EncodedFrame] = []
        timestamp = start_s
        while timestamp < end_s and len(frames) < max_frames:
            frames.append(self.frame_at(timestamp))
            timestamp += step
        return frames

    def _read_duration(self) -> float:
        if self._av_stream.duration is not None:
            return float(self._av_stream.duration * self._av_stream.time_base)
        if self._av_container.duration is not None:
            # FFmpeg container durations use AV_TIME_BASE units.
            return float(self._av_container.duration) / 1_000_000.0
        if self._av_stream.frames and self._av_stream.average_rate:
            return float(self._av_stream.frames / self._av_stream.average_rate)
        return 0.0


def load_image(
    path: Path,
    *,
    max_pixels: int,
    jpeg_quality: int,
    source_label: str,
) -> EncodedFrame:
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if frame is None:
        raise OSError(f"unable to open image: {path}")
    return encode_frame(
        frame,
        timestamp_s=0.0,
        max_pixels=max_pixels,
        jpeg_quality=jpeg_quality,
        source=source_label,
    )


def encode_frame(
    frame: np.ndarray,
    *,
    timestamp_s: float,
    max_pixels: int,
    jpeg_quality: int,
    source: str,
    requested_timestamp_s: float | None = None,
    timestamp_source: str = "decoded_pts",
) -> EncodedFrame:
    height, width = frame.shape[:2]
    pixels = width * height
    if pixels > max_pixels:
        scale = math.sqrt(max_pixels / pixels)
        width = max(1, int(width * scale))
        height = max(1, int(height * scale))
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
    )
    if not ok:
        raise OSError("failed to encode JPEG frame")
    return EncodedFrame(
        timestamp_s=timestamp_s,
        jpeg_bytes=encoded.tobytes(),
        width=width,
        height=height,
        source=source,
        requested_timestamp_s=requested_timestamp_s,
        timestamp_source=timestamp_source,
    )
