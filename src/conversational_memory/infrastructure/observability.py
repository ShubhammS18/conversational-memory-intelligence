"""Bounded local delivery and timing adapters for privacy-safe events."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import BinaryIO

from conversational_memory.application.events import MemoryEvent, serialize_json_line


@dataclass(slots=True)
class JsonLineEventSink:
    """Write one canonical event line at a time to an injected binary stream."""

    stream: BinaryIO
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def emit(self, event: MemoryEvent) -> None:
        payload = serialize_json_line(event)
        with self._lock:
            written = self.stream.write(payload)
            if written is not None and written != len(payload):
                raise OSError("event sink did not write the complete event")
            self.stream.flush()


class SystemTelemetryClock:
    """Supply independent UTC wall time and process-monotonic timing."""

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def monotonic_ns() -> int:
        return time.monotonic_ns()
