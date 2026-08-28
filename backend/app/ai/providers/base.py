"""The ReasoningModel abstraction (Phase 4, Section 7).

The platform must not be coupled to a single model. Every provider
implements :class:`ReasoningModel` and returns a :class:`RawModelResponse`
-- the raw text plus the metadata Section 51 requires stored with every
diagnosis. Parsing/validating that text into a ``Diagnosis`` happens in
``app/ai/diagnosis.py``, identically for every provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.ai.context_builder import RecoveryContext


@dataclass(frozen=True)
class RawModelResponse:
    text: str
    model_name: str
    model_version: str
    prompt_version: str
    latency_ms: int


class ReasoningModelError(Exception):
    """The provider could not be reached or returned a transport-level error.

    Distinct from a diagnosis *validation* failure (the model replied, but
    the content was not usable) -- that is ``DiagnosisValidationError`` in
    ``app/ai/diagnosis.py``.
    """


class ReasoningModel(ABC):
    name: str

    @abstractmethod
    async def diagnose(self, context: RecoveryContext, *, prompt_version: str) -> RawModelResponse:
        """Run one diagnosis. Returns the raw model text and metadata.

        Must raise :class:`ReasoningModelError` on a transport/connection
        failure. Must not raise for a merely unparseable reply -- return it
        and let the diagnosis layer reject it.
        """
        raise NotImplementedError
