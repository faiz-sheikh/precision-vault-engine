"""Provider-neutral adapters. Transports are injected and live calls fail closed."""

from __future__ import annotations

import json
import os
import re
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


@dataclass(frozen=True)
class HTTPErrorEvidence:
    status: int
    headers: dict[str, str]
    body: bytes
    provider_error_code: str | None = None
    provider_error_message: str | None = None


class _HTTPResponseError(RuntimeError):
    """Internal unclassified HTTP response carried to the durability boundary."""

    def __init__(self, evidence: HTTPErrorEvidence):
        super().__init__(f"provider HTTP {evidence.status}")
        self.evidence = evidence


class ProviderFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        classification: FailureClass,
        *,
        http_evidence: HTTPErrorEvidence | None = None,
        request_sent: bool | None = None,
    ):
        super().__init__(message)
        self.classification = classification
        self.http_evidence = http_evidence
        self.request_sent = request_sent


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

    def execute(
        self,
        request: dict[str, Any],
        on_http_error: Callable[[HTTPErrorEvidence], None] | None = None,
    ) -> ProviderResult:
        self.preflight()
        try:
            return self.transport(request)
        except _HTTPResponseError as exc:
            if on_http_error is not None:
                # The response becomes durable before it is classified or exposed
                # outside the adapter boundary.
                on_http_error(exc.evidence)
            raise ProviderFailure(
                str(exc),
                classify_status(exc.evidence.status),
                http_evidence=exc.evidence,
                request_sent=True,
            ) from exc
        except ProviderFailure as exc:
            if exc.http_evidence is not None and on_http_error is not None:
                # Compatibility transports may already supply a classified failure.
                on_http_error(exc.http_evidence)
            raise


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
        raise ProviderFailure(
            f"missing credential: {name}", FailureClass.PERMANENT, request_sent=False
        )
    return value


_SAFE_ERROR_HEADERS = {
    "content-type",
    "date",
    "retry-after",
    "request-id",
    "x-request-id",
    "x-goog-request-id",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}
_SECRET_PATTERNS = (
    re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(rb"nvapi-[0-9A-Za-z_-]{20,}"),
    re.compile(rb"sk-(?:ant-)?[0-9A-Za-z_-]{20,}"),
)


def _sanitize_error_body(raw: bytes, secrets: tuple[str, ...]) -> bytes:
    sanitized = raw
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret.encode("utf-8"), b"[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(b"[REDACTED]", sanitized)
    return sanitized


def _error_details(raw: bytes) -> tuple[str | None, str | None]:
    try:
        envelope = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("error"), dict):
        return None, None
    error = envelope["error"]
    code = error.get("status") or error.get("code") or error.get("type")
    message = error.get("message")
    return (str(code) if code is not None else None, str(message) if message is not None else None)


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
        raw = exc.read()
        secret_values = tuple(
            value.removeprefix("Bearer ")
            for name, value in headers.items()
            if name.casefold() in {"authorization", "x-goog-api-key", "api-key"}
        )
        sanitized = _sanitize_error_body(raw, secret_values)
        safe_headers = {
            name.casefold(): _sanitize_error_body(value.encode("utf-8"), secret_values).decode(
                "utf-8", errors="replace"
            )
            for name, value in (exc.headers or {}).items()
            if name.casefold() in _SAFE_ERROR_HEADERS
        }
        code, message = _error_details(sanitized)
        evidence = HTTPErrorEvidence(
            status=exc.code,
            headers=safe_headers,
            body=sanitized,
            provider_error_code=code,
            provider_error_message=message,
        )
        raise _HTTPResponseError(evidence) from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        # No provider acceptance evidence is available for these synchronous transports.
        raise ProviderFailure(
            "provider transport outcome is ambiguous",
            FailureClass.AMBIGUOUS,
            request_sent=True,
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
