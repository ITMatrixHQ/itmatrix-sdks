"""Stable SDK errors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ITMError(Exception):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        # Assigned explicitly: `Exception` keeps the text only in `args`, so
        # without this `e.message` raises AttributeError while every sibling
        # field resolves — and `message` is the one an error handler reaches
        # for first. TypeScript's ITMError exposes it, so this also keeps the
        # two languages saying the same thing.
        self.message = message
        self.details = details
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds
