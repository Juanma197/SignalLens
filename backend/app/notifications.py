"""Provider-neutral, bounded operational notifications."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

ALLOWED_KINDS = {"stale_data", "missing_data", "refresh_failure", "month_end_plan_ready",
                 "shadow_outcome_matured", "backup_failure"}


class NotificationProvider(Protocol):
    def send(self, kind: str, message: str) -> None: ...


def bounded_message(message: str, limit: int = 400) -> str:
    """Strip common secret/path material and cap provider-bound content."""
    words = []
    for word in str(message).split():
        lower = word.lower()
        if any(marker in lower for marker in ("token", "authorization", "password", "secret")):
            words.append("[redacted]")
        elif word.startswith(("/", "file:", "postgres:", "duckdb:")):
            words.append("[path-redacted]")
        else:
            words.append(word)
    return " ".join(words)[:limit]


@dataclass
class OfflineNotificationSink:
    """Safe local/test sink; holds only bounded, sanitized messages."""
    messages: list[dict[str, str]] = field(default_factory=list)

    def send(self, kind: str, message: str) -> None:
        if kind not in ALLOWED_KINDS:
            raise ValueError("unsupported notification kind")
        self.messages.append({"kind": kind, "message": bounded_message(message)})
        del self.messages[:-50]
