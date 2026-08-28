"""Shared HTTP client for self-hosted, OpenAI-compatible model servers.

`QwenProvider` and `NemotronProvider` are thin subclasses that only supply
a name, a base URL, and a model id. Any server that implements
``POST {base_url}/chat/completions`` in the OpenAI chat format works --
Ollama, llama.cpp's server, vLLM, LM Studio, or a hosted endpoint. See
``docs/ai/local-model-setup.md``.

This module never imports GPU or model libraries; it only speaks HTTP.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.ai.context_builder import RecoveryContext
from app.ai.prompts import render_diagnosis_messages
from app.ai.providers.base import RawModelResponse, ReasoningModel, ReasoningModelError


class OpenAICompatibleProvider(ReasoningModel):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        # Test seam: inject an httpx MockTransport to exercise this without a
        # running model server.
        self._transport = transport

    async def diagnose(self, context: RecoveryContext, *, prompt_version: str) -> RawModelResponse:
        messages = render_diagnosis_messages(context)
        request_body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.0,
            "stream": False,
            # Ask servers that support it to constrain output to JSON.
            "response_format": {"type": "json_object"},
        }

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions", json=request_body
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ReasoningModelError(
                f"{self.name} provider request to {self._base_url} failed: {exc}"
            ) from exc
        latency_ms = int((time.perf_counter() - start) * 1000)

        try:
            text = data["choices"][0]["message"]["content"]
            model_version = str(data.get("model", self._model))
        except (KeyError, IndexError, TypeError) as exc:
            raise ReasoningModelError(
                f"{self.name} provider returned an unexpected response shape: {exc}"
            ) from exc

        if not isinstance(text, str) or not text.strip():
            raise ReasoningModelError(f"{self.name} provider returned an empty completion")

        return RawModelResponse(
            text=text,
            model_name=self.name,
            model_version=model_version,
            prompt_version=prompt_version,
            latency_ms=latency_ms,
        )
