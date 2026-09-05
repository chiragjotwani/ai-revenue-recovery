"""Model router: failure-based provider escalation (Phase 10, Section 29
of the master plan -- "model router" / "advanced-model escalation").

Scope boundary (owner decision, this session): escalation is driven
EXCLUSIVELY by an observable transport failure
(:class:`~app.ai.providers.base.ReasoningModelError` -- the primary
provider could not be reached at all), never by the diagnosis's
self-reported ``confidence``. This project has twice already documented
that confidence is model-reported and uncalibrated (Phase 4.1's AI
validation stage; ADR-006, which forbids exactly this shape of thing --
confidence driving an automated decision -- at the policy layer) and
declined to treat it as a trustworthy signal. Extending that same
discipline here: a "confidence routing" feature that picks a different
model because the current one claims to be unsure would be trusting the
one number this project has explicitly refused to trust, on no better
evidence than Phase 9 had for a recovery-probability model. It is
deliberately not built.

``run_diagnosis`` itself (``app.ai.diagnosis``) is unchanged and untouched
by this module: it still takes exactly one provider, still retries only
parse/validation failures against that same provider, and still
propagates :class:`ReasoningModelError` immediately -- this is the
frozen Phase 4 contract several existing tests depend on. This module is
a layer ABOVE it: it calls ``run_diagnosis`` once against the primary
provider, and only on a transport failure retries once more against a
fallback provider (``MockProvider`` by default -- always available, no
network, the same "must work with no model infrastructure" guarantee
Phase 4's factory already relies on).
"""

from __future__ import annotations

import logging

from app.ai.context_builder import RecoveryContext
from app.ai.diagnosis import run_diagnosis
from app.ai.providers.base import RawModelResponse, ReasoningModel, ReasoningModelError
from app.ai.providers.mock import MockProvider
from app.ai.schema import Diagnosis

logger = logging.getLogger(__name__)


class DiagnosisRouterResult:
    """The outcome of one routed diagnosis attempt: the result itself,
    plus whether the primary provider's transport failure forced a
    fallback -- always available to the caller, never hidden inside a
    side channel only visible by re-reading a persisted row afterward.
    """

    __slots__ = ("diagnosis", "raw", "escalated", "escalation_reason")

    def __init__(
        self,
        diagnosis: Diagnosis,
        raw: RawModelResponse,
        *,
        escalated: bool,
        escalation_reason: str | None,
    ) -> None:
        self.diagnosis = diagnosis
        self.raw = raw
        self.escalated = escalated
        self.escalation_reason = escalation_reason


async def run_diagnosis_with_failover(
    context: RecoveryContext,
    primary: ReasoningModel,
    *,
    prompt_version: str,
    fallback: ReasoningModel | None = None,
) -> DiagnosisRouterResult:
    """Run a diagnosis against ``primary``; on a transport failure
    (:class:`ReasoningModelError`), escalate once to ``fallback``
    (``MockProvider()`` by default). Never escalates on a validation
    failure -- ``run_diagnosis`` already owns that retry, against the
    same provider, and this module must not second-guess it.

    Raises :class:`ReasoningModelError` if ``primary`` already IS the
    fallback provider (nothing to escalate to) or if the fallback attempt
    also fails transport-wise (should not happen for the default
    ``MockProvider`` fallback, which makes no network call, but is not
    assumed away).
    """
    fallback = fallback or MockProvider()

    try:
        diagnosis, raw = await run_diagnosis(context, primary, prompt_version=prompt_version)
        return DiagnosisRouterResult(diagnosis, raw, escalated=False, escalation_reason=None)
    except ReasoningModelError as exc:
        if primary.name == fallback.name:
            raise  # already the fallback provider -- nothing left to escalate to

        reason = f"{primary.name} provider unreachable: {exc}"
        logger.warning(
            "model router escalation: primary=%s fallback=%s reason=%s",
            primary.name,
            fallback.name,
            reason,
        )
        diagnosis, raw = await run_diagnosis(context, fallback, prompt_version=prompt_version)
        return DiagnosisRouterResult(diagnosis, raw, escalated=True, escalation_reason=reason)
