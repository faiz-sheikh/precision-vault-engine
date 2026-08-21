"""Provider-neutral adapters. Transports are injected and live calls fail closed."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from .safety import Provider, require_live_provider


class FailureClass(StrEnum):
    PERMANENT = "PERMANENT"
    TRANSIENT = "TRANSIENT"
    AMBIGUOUS = "AMBIGUOUS"
    VALIDATION = "VALIDATION"
    LOCAL_PROCESSING = "LOCAL_PROCESSING"


class ProviderFailure(RuntimeError):
    def __init__(self, message: str, classification: FailureClass):
        super().__init__(message); self.classification = classification


@dataclass(frozen=True)
class ProviderResult:
    raw: bytes
    provider_id: str | None = None
    usage: dict[str, Any] | None = None


def classify_status(status: int | None, accepted_unknown: bool = False) -> FailureClass:
    if accepted_unknown:
        return FailureClass.AMBIGUOUS
    if status in (408, 409, 425, 429) or (status is not None and status >= 500):
        return FailureClass.TRANSIENT
    if status is not None and 400 <= status < 500:
        return FailureClass.PERMANENT
    return FailureClass.LOCAL_PROCESSING


class Adapter:
    def __init__(self, provider: Provider, transport: Callable[[dict[str, Any]], ProviderResult], *, live: bool = False):
        self.provider = provider
        self.transport = transport
        self.live = live

    def execute(self, request: dict[str, Any]) -> ProviderResult:
        if self.live:
            require_live_provider(self.provider)
        return self.transport(request)


class GoogleAdapter(Adapter):
    def __init__(self, transport: Callable[[dict[str, Any]], ProviderResult], *, live: bool = False):
        super().__init__(Provider.GOOGLE, transport, live=live)


class NvidiaAdapter(Adapter):
    def __init__(self, transport: Callable[[dict[str, Any]], ProviderResult], *, live: bool = False):
        super().__init__(Provider.NVIDIA, transport, live=live)


class OpenAIAdapter(Adapter):
    def __init__(self, transport: Callable[[dict[str, Any]], ProviderResult], *, live: bool = False):
        super().__init__(Provider.OPENAI, transport, live=live)


class LegacyAnthropicAdapter(Adapter):
    def __init__(self, transport: Callable[[dict[str, Any]], ProviderResult], *, live: bool = False):
        super().__init__(Provider.ANTHROPIC, transport, live=live)
