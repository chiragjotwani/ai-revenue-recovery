"""Select the configured :class:`ReasoningModel`.

``REASONING_PROVIDER`` chooses the implementation. If a self-hosted
provider is chosen but its base URL is not configured, we fall back to the
mock provider (and say so via ``resolved_provider_name``) rather than
failing to start -- Phase 4 must work with no model infrastructure.
"""

from __future__ import annotations

from app.ai.providers.base import ReasoningModel
from app.ai.providers.mock import MockProvider
from app.ai.providers.nemotron import NemotronProvider
from app.ai.providers.qwen import QwenProvider
from app.core.config import Settings, get_settings


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
