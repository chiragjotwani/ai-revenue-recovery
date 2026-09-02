"""Select the configured :class:`ReasoningModel` (Phase 4; the router's
explicit/observable selection is Phase 10 -- Section 29 of the master
plan, KI-009).

``REASONING_PROVIDER`` chooses the implementation. If a self-hosted
provider is chosen but its base URL is not configured, we fall back to the
mock provider (and say so via ``resolved_provider_name``) rather than
failing to start -- Phase 4 must work with no model infrastructure.

``get_reasoning_model``/``resolved_provider_name`` are the original Phase
4 contract and are unchanged (frozen; ``app.services.diagnosis`` and
several tests depend on this exact shape). ``select_reasoning_model`` is
the Phase 10 addition: the same resolution, but returning a
:class:`ProviderSelection` that makes a config-time substitution
observable at the moment it happens (a structured log warning) rather
than only after the fact via a persisted diagnosis's ``model_name`` --
closing the gap KI-009 documented and left open.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.ai.providers.base import ReasoningModel
from app.ai.providers.mock import MockProvider
from app.ai.providers.nemotron import NemotronProvider
from app.ai.providers.qwen import QwenProvider
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def get_reasoning_model(settings: Settings | None = None) -> ReasoningModel:
    settings = settings or get_settings()
    choice = settings.reasoning_provider.strip().lower()

    if choice == "qwen" and settings.ai_qwen_base_url:
        return QwenProvider(
            base_url=settings.ai_qwen_base_url,
            model=settings.ai_qwen_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
        )
    if choice == "nemotron" and settings.ai_nemotron_base_url:
        return NemotronProvider(
            base_url=settings.ai_nemotron_base_url,
            model=settings.ai_nemotron_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
        )
    return MockProvider()


def resolved_provider_name(settings: Settings | None = None) -> str:
    return get_reasoning_model(settings).name


@dataclass(frozen=True)
class ProviderSelection:
    """The result of resolving ``REASONING_PROVIDER``, with the
    substitution decision made explicit -- never just "here is a
    provider", always "here is what was requested, what was resolved,
    and why they differ (if they do)".
    """

    provider: ReasoningModel
    requested_provider: str
    resolved_provider: str
    substituted: bool
    substitution_reason: str | None


def select_reasoning_model(settings: Settings | None = None) -> ProviderSelection:
    """The Phase 10 model router's selection step. Identical resolution
    logic to :func:`get_reasoning_model` (never diverges from it -- both
    must always agree on which provider a plain call would get), but
    returns the full :class:`ProviderSelection` and logs a warning at the
    moment a substitution happens, rather than leaving it discoverable
    only by inspecting a diagnosis after the fact.
    """
    settings = settings or get_settings()
    requested = settings.reasoning_provider.strip().lower()

    if requested == "qwen":
        if settings.ai_qwen_base_url:
            return ProviderSelection(
                provider=QwenProvider(
                    base_url=settings.ai_qwen_base_url,
                    model=settings.ai_qwen_model,
                    timeout_seconds=settings.ai_request_timeout_seconds,
                ),
                requested_provider="qwen",
                resolved_provider="qwen",
                substituted=False,
                substitution_reason=None,
            )
        reason = "AI_QWEN_BASE_URL is not configured"
        logger.warning("reasoning provider substitution: requested=qwen resolved=mock (%s)", reason)
        return ProviderSelection(MockProvider(), "qwen", "mock", True, reason)

    if requested == "nemotron":
        if settings.ai_nemotron_base_url:
            return ProviderSelection(
                provider=NemotronProvider(
                    base_url=settings.ai_nemotron_base_url,
                    model=settings.ai_nemotron_model,
                    timeout_seconds=settings.ai_request_timeout_seconds,
                ),
                requested_provider="nemotron",
                resolved_provider="nemotron",
                substituted=False,
                substitution_reason=None,
            )
        reason = "AI_NEMOTRON_BASE_URL is not configured"
        logger.warning(
            "reasoning provider substitution: requested=nemotron resolved=mock (%s)", reason
        )
        return ProviderSelection(MockProvider(), "nemotron", "mock", True, reason)

    if requested != "mock":
        reason = f"unrecognised REASONING_PROVIDER {requested!r}"
        logger.warning(
            "reasoning provider substitution: requested=%s resolved=mock (%s)", requested, reason
        )
        return ProviderSelection(MockProvider(), requested, "mock", True, reason)

    return ProviderSelection(MockProvider(), "mock", "mock", False, None)
