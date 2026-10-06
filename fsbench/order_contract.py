"""Dispatch ordering follows window starts, with no prescribed order among ties."""

from datetime import datetime, timezone
from collections.abc import Iterable


def window_starts_are_ordered(values: Iterable[str]) -> bool:
    starts = [
        datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        for value in values
    ]
    return starts == sorted(starts)
