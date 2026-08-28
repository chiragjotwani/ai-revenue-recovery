"""Nemotron reasoning-model provider.

Contract candidate: Nemotron 3 Nano 30B-A3B (Section 7). Same HTTP contract
as every other provider; only ``AI_NEMOTRON_BASE_URL`` / ``AI_NEMOTRON_MODEL``
differ. Selection between this and Qwen is decided by the benchmark once
model infrastructure exists (KI-002).
"""

from __future__ import annotations

from app.ai.providers.openai_compatible import OpenAICompatibleProvider


class NemotronProvider(OpenAICompatibleProvider):
    def __init__(self, *, base_url: str, model: str, timeout_seconds: float = 60.0) -> None:
        super().__init__(
            name="nemotron", base_url=base_url, model=model, timeout_seconds=timeout_seconds
        )
