from __future__ import annotations

from dataclasses import dataclass, field

from .interfaces import GenerationBackend, ModelCallResult
from .config import EvaluationConfig
from .frames import EncodedFrame
from .prompts import build_long_term_messages, build_mid_term_messages


@dataclass
class MemoryUpdate:
    chunk_closed: bool
    mid_term_result: ModelCallResult | None = None
    long_term_result: ModelCallResult | None = None


@dataclass(frozen=True)
class MemorySnapshot:
    short_term_frame_count: int
    mid_term_summaries: tuple[str, ...]
    long_term_blocks: tuple[str, ...]

    @property
    def text(self) -> str:
        parts: list[str] = []
        if self.long_term_blocks:
            parts.append("[LONG-TERM MEMORY]\n" + "\n\n".join(self.long_term_blocks))
        if self.mid_term_summaries:
            parts.append("[MID-TERM MEMORY]\n" + "\n\n".join(self.mid_term_summaries))
        return "\n\n".join(parts) or "None"

    def audit_dict(self) -> dict[str, object]:
        return {
            "short_term_frame_count": self.short_term_frame_count,
            "mid_term_summary_count": len(self.mid_term_summaries),
            "long_term_block_count": len(self.long_term_blocks),
            "text": self.text,
        }


@dataclass
class MemoryManager:
    config: EvaluationConfig
    model: GenerationBackend
    summaries_enabled: bool = True
    short_term_frames: list[EncodedFrame] = field(default_factory=list)
    mid_term_summaries: list[str] = field(default_factory=list)
    long_term_blocks: list[str] = field(default_factory=list)

    def observe(self, frame: EncodedFrame) -> MemoryUpdate:
        self.short_term_frames.append(frame)
        if len(self.short_term_frames) < self.config.short_term_frame_count:
            return MemoryUpdate(chunk_closed=False)
        if not self.summaries_enabled:
            self.short_term_frames.clear()
            return MemoryUpdate(chunk_closed=True)

        mid_result = self.model.complete(
            build_mid_term_messages(self.short_term_frames),
            max_tokens=self.config.mid_term_max_tokens,
            call_type="mid_term_memory",
        )
        self.mid_term_summaries.append(mid_result.content.strip())
        self.short_term_frames.clear()
        long_result: ModelCallResult | None = None
        if len(self.mid_term_summaries) >= self.config.mid_term_capacity:
            long_result = self.model.complete(
                build_long_term_messages(self.mid_term_summaries),
                max_tokens=self.config.long_term_max_tokens,
                call_type="long_term_memory",
            )
            self.long_term_blocks.append(long_result.content.strip())
            self.long_term_blocks = self.long_term_blocks[-self.config.long_term_capacity :]
            self.mid_term_summaries.clear()
        return MemoryUpdate(True, mid_result, long_result)

    def snapshot(self) -> MemorySnapshot:
        return MemorySnapshot(
            short_term_frame_count=len(self.short_term_frames),
            mid_term_summaries=tuple(self.mid_term_summaries),
            long_term_blocks=tuple(self.long_term_blocks),
        )

__all__ = [
    "MemoryManager",
    "MemorySnapshot",
    "MemoryUpdate",
]
