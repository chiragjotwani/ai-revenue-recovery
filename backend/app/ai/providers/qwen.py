"""Qwen reasoning-model provider.

Contract candidate: Qwen3-30B-A3B-Instruct-2507 (Section 7). On modest
hardware a smaller Qwen (e.g. ``qwen3:4b`` via Ollama) is a legitimate
choice -- selection is deferred to the benchmark (KI-002). Either way this
class is unchanged; only ``AI_QWEN_BASE_URL`` / ``AI_QWEN_MODEL`` differ.
"""

from __future__ import annotations

from app.ai.providers.openai_compatible import OpenAICompatibleProvider


class QwenProvider(OpenAICompatibleProvider):
    def __init__(self, *, base_url: str, model: str, timeout_seconds: float = 60.0) -> None:
        super().__init__(
            name="qwen", base_url=base_url, model=model, timeout_seconds=timeout_seconds
        )
