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
    PERMANENT_REQUEST = "PERMANENT_REQUEST"
    PERMANENT = "PERMANENT_REQUEST"  # Compatibility alias.
    ACCOUNT_OR_BILLING_BLOCKED = "ACCOUNT_OR_BILLING_BLOCKED"
    AUTH_BLOCKED = "AUTH_BLOCKED"
    ACCESS_BLOCKED = "ACCESS_BLOCKED"
    MODEL_ACCESS_BLOCKED = "MODEL_ACCESS_BLOCKED"
    RATE_LIMIT_TRANSIENT = "RATE_LIMIT_TRANSIENT"
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
    if status == 429:
        return FailureClass.RATE_LIMIT_TRANSIENT
    if status in (408, 409, 425) or (status is not None and status >= 500):
        return FailureClass.TRANSIENT
    if status == 401:
        return FailureClass.AUTH_BLOCKED
    if status == 403:
        return FailureClass.ACCESS_BLOCKED
    if status is not None and 400 <= status < 500:
        return FailureClass.PERMANENT_REQUEST
    return FailureClass.LOCAL_PROCESSING


_RATE_LIMIT_MARKERS = (
    "rate_limit_exceeded",
    "rate limit exceeded",
    "rate-limit",
    "requests per minute",
    "requests_per_minute",
    "tokens per minute",
    "tokens_per_minute",
    "requests per second",
    "rpm",
    "tpm",
)
_ACCOUNT_BILLING_MARKERS = (
    "insufficient_quota",
    "quota_exceeded",
    "billing",
    "payment required",
    "payment_required",
    "credit balance",
    "credits exhausted",
    "spend limit",
    "current quota",
    "check your plan",
)
_AUTH_MARKERS = (
    "invalid_api_key",
    "invalid api key",
    "authentication_error",
    "authentication failed",
    "unauthenticated",
    "invalid credential",
    "invalid x-api-key",
)
_MODEL_MARKERS = (
    "model_not_found",
    "model not found",
    "model_not_available",
    "model is not available",
    "does not have access to model",
    "access to this model",
    "unknown model",
)


def classify_http_error(provider: Provider, evidence: HTTPErrorEvidence) -> FailureClass:
    """Classify sanitized provider evidence without weakening status-based safety."""
    del provider  # Marker semantics are deliberately shared across provider adapters.
    text = " ".join(
        value
        for value in (
            evidence.provider_error_code,
            evidence.provider_error_message,
            evidence.body.decode("utf-8", errors="replace"),
        )
        if value
    ).casefold()
    if any(marker in text for marker in _AUTH_MARKERS) or evidence.status == 401:
        return FailureClass.AUTH_BLOCKED
    if any(marker in text for marker in _MODEL_MARKERS):
        return FailureClass.MODEL_ACCESS_BLOCKED
    # Explicit time-window/rate-unit evidence wins over generic quota wording.
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return FailureClass.RATE_LIMIT_TRANSIENT
    if any(marker in text for marker in _ACCOUNT_BILLING_MARKERS):
        return FailureClass.ACCOUNT_OR_BILLING_BLOCKED
    if evidence.status == 429:
        # Unknown 429 semantics remain bounded but retryable; known quota/billing
        # bodies are caught above and never enter automatic retry.
        return FailureClass.RATE_LIMIT_TRANSIENT
    if evidence.status == 403:
        return FailureClass.ACCESS_BLOCKED
    return classify_status(evidence.status)


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
                classify_http_error(self.provider, exc.evidence),
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


class ContentValidationError(ValueError):
    """A successful transport produced content that cannot satisfy its contract."""


def normalize_finish_reason(reason: object) -> str:
    """Map provider stop reasons to a small fail-closed completion vocabulary."""
    if not isinstance(reason, str) or not reason.strip():
        return "UNKNOWN"
    normalized = reason.strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"stop", "stop_sequence", "end_turn", "completed", "complete"}:
        return "COMPLETE"
    if normalized in {"length", "max_tokens", "max_output_tokens", "token_limit"}:
        return "TRUNCATED"
    if normalized in {"content_filter", "safety", "blocked", "recitation"}:
        return "SAFETY_STOP"
    if normalized in {"tool_calls", "tool_call", "function_call"}:
        return "TOOL_STOP"
    return "UNKNOWN"


def parse_structured_message(
    raw: bytes, *, required_string_fields: tuple[str, ...]
) -> dict[str, Any]:
    """Extract and validate structured assistant content from a chat response envelope."""
    try:
        envelope = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentValidationError("provider response envelope is not valid JSON") from exc
    if not isinstance(envelope, dict):
        raise ContentValidationError("provider response envelope must be an object")
    choices = envelope.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ContentValidationError("provider response must contain exactly one choice")
    choice = choices[0]
    completion = normalize_finish_reason(choice.get("finish_reason"))
    if completion != "COMPLETE":
        raise ContentValidationError(f"provider completion is not complete: {completion}")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ContentValidationError("assistant message content is missing")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ContentValidationError("assistant message content is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ContentValidationError("assistant structured content must be an object")
    missing = [
        field
        for field in required_string_fields
        if not isinstance(parsed.get(field), str) or not parsed[field].strip()
    ]
    if missing:
        raise ContentValidationError(
            "assistant structured content has invalid required fields: " + ", ".join(missing)
        )
    return parsed


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
