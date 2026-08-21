"""Provider-neutral adapters. Transports are injected and live calls fail closed."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .safety import Provider, require_live_provider


class FailureClass(StrEnum):
    PERMANENT = "PERMANENT"
    TRANSIENT = "TRANSIENT"
    AMBIGUOUS = "AMBIGUOUS"
    VALIDATION = "VALIDATION"
    LOCAL_PROCESSING = "LOCAL_PROCESSING"


class ProviderFailure(RuntimeError):
    def __init__(self, message: str, classification: FailureClass):
        super().__init__(message)
        self.classification = classification


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
    def __init__(
        self,
        provider: Provider,
        transport: Callable[[dict[str, Any]], ProviderResult],
        *,
        live: bool = False,
    ):
        self.provider = provider
        self.transport = transport
        self.live = live

    def preflight(self) -> None:
        """Reject disabled live execution before a ledger intent is created."""
        if self.live:
            require_live_provider(self.provider)

    def execute(self, request: dict[str, Any]) -> ProviderResult:
        self.preflight()
        return self.transport(request)


class GoogleAdapter(Adapter):
    def __init__(
        self, transport: Callable[[dict[str, Any]], ProviderResult], *, live: bool = False
    ):
        super().__init__(Provider.GOOGLE, transport, live=live)


class NvidiaAdapter(Adapter):
    def __init__(
        self, transport: Callable[[dict[str, Any]], ProviderResult], *, live: bool = False
    ):
        super().__init__(Provider.NVIDIA, transport, live=live)


class OpenAIAdapter(Adapter):
    def __init__(
        self, transport: Callable[[dict[str, Any]], ProviderResult], *, live: bool = False
    ):
        super().__init__(Provider.OPENAI, transport, live=live)


class LegacyAnthropicAdapter(Adapter):
    def __init__(
        self, transport: Callable[[dict[str, Any]], ProviderResult], *, live: bool = False
    ):
        super().__init__(Provider.ANTHROPIC, transport, live=live)


def _credential(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise ProviderFailure(f"missing credential: {name}", FailureClass.PERMANENT)
    return value


def _post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float = 60
) -> ProviderResult:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            provider_id = response.headers.get("x-request-id")
            usage: dict[str, Any] | None = None
            try:
                envelope = json.loads(raw)
                if isinstance(envelope, dict):
                    response_id = envelope.get("id")
                    if provider_id is None and isinstance(response_id, str):
                        provider_id = response_id
                    raw_usage = envelope.get("usage") or envelope.get("usageMetadata")
                    if isinstance(raw_usage, dict):
                        usage = raw_usage
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            return ProviderResult(raw=raw, provider_id=provider_id, usage=usage)
    except urllib.error.HTTPError as exc:
        raise ProviderFailure(f"provider HTTP {exc.code}", classify_status(exc.code)) from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        # No provider acceptance evidence is available for these synchronous transports.
        raise ProviderFailure(
            "provider transport outcome is ambiguous", FailureClass.AMBIGUOUS
        ) from exc


def google_live_adapter(
    endpoint: str = "https://generativelanguage.googleapis.com/v1beta",
) -> GoogleAdapter:
    def transport(request: dict[str, Any]) -> ProviderResult:
        key = _credential("GEMINI_API_KEY")
        payload = dict(request)
        model = payload.pop("model")
        return _post_json(
            f"{endpoint}/models/{model}:generateContent", payload, {"x-goog-api-key": key}
        )

    return GoogleAdapter(transport, live=True)


def openai_compatible_live_adapter(provider: Provider, endpoint: str) -> Adapter:
    if provider not in (Provider.NVIDIA, Provider.OPENAI):
        raise ValueError("unsupported OpenAI-compatible provider")
    credential_name = "NVIDIA_API_KEY" if provider is Provider.NVIDIA else "OPENAI_API_KEY"

    def transport(request: dict[str, Any]) -> ProviderResult:
        key = _credential(credential_name)
        return _post_json(
            endpoint.rstrip("/") + "/chat/completions", request, {"Authorization": f"Bearer {key}"}
        )

    cls = NvidiaAdapter if provider is Provider.NVIDIA else OpenAIAdapter
    return cls(transport, live=True)


def openai_responses_live_adapter(endpoint: str = "https://api.openai.com/v1") -> OpenAIAdapter:
    def transport(request: dict[str, Any]) -> ProviderResult:
        key = _credential("OPENAI_API_KEY")
        return _post_json(
            endpoint.rstrip("/") + "/responses",
            request,
            {"Authorization": f"Bearer {key}"},
        )

    return OpenAIAdapter(transport, live=True)
