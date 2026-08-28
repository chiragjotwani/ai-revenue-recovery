"""Turn a raw model reply into a validated :class:`Diagnosis` (Section 37).

Pipeline: provider text -> extract JSON -> schema validation -> safeguards.
Any failure raises :class:`DiagnosisValidationError`; the caller must not
advance the case or persist anything on that path. Nothing here executes a
recovery action -- that boundary is Phase 5/6 (ADR-003).
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from app.ai.context_builder import RecoveryContext
from app.ai.providers.base import RawModelResponse, ReasoningModel
from app.ai.schema import (
    Diagnosis,
    DiagnosisDisposition,
    DiagnosisOutcome,
    ModelDiagnosisJSON,
    RecoveryStrategy,
)

_MAX_ATTEMPTS = 2
_CONFLICT_CONFIDENCE_CAP = 0.5


class DiagnosisValidationError(Exception):
    """The model replied, but the content could not be turned into a valid
    diagnosis after every attempt. Never execute on this path.
    """

    def __init__(self, detail: str, *, attempts: int) -> None:
        self.detail = detail
        self.attempts = attempts
        super().__init__(f"diagnosis validation failed after {attempts} attempt(s): {detail}")


def _extract_json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    if stripped.startswith("```"):
        # ```json\n{...}\n```  or  ```\n{...}\n```
        stripped = stripped.split("```", 2)[1]
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip().rstrip("`").strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model output")
    try:
        parsed = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model output JSON is not an object")
    return parsed


def _apply_safeguards(diagnosis: Diagnosis, context: RecoveryContext) -> Diagnosis:
    """Cheap guards against over-confident output that ignores the context."""
    sparse = context.evidence_sufficiency == "sparse"
    if sparse and diagnosis.outcome is not DiagnosisOutcome.UNKNOWN:
        return diagnosis.model_copy(
            update={
                "outcome": DiagnosisOutcome.UNKNOWN,
                "disposition": DiagnosisDisposition.UNKNOWN,
                "confidence": min(diagnosis.confidence, 0.3),
                "recommended_strategy": RecoveryStrategy.MANUAL_REVIEW,
                "recommended_delay_hours": None,
                "reasoning": (
                    "[safeguard] evidence was insufficient for a specific cause; "
                    f"downgraded to UNKNOWN. Model said: {diagnosis.reasoning}"
                ),
            }
        )

    if context.signals_conflict and diagnosis.confidence > _CONFLICT_CONFIDENCE_CAP:
        return diagnosis.model_copy(
            update={
                "confidence": _CONFLICT_CONFIDENCE_CAP,
                "reasoning": (
                    "[safeguard] signals conflict with customer history; confidence "
                    f"capped at {_CONFLICT_CONFIDENCE_CAP}. Model said: {diagnosis.reasoning}"
                ),
            }
        )

    return diagnosis


async def run_diagnosis(
    context: RecoveryContext,
    provider: ReasoningModel,
    *,
    prompt_version: str,
    max_attempts: int = _MAX_ATTEMPTS,
) -> tuple[Diagnosis, RawModelResponse]:
    """Run the model and return ``(diagnosis, raw_response)``.

    Retries only parse/validation failures (a transient bad reply), up to
    ``max_attempts``. Transport failures (:class:`ReasoningModelError`)
    propagate immediately.
    """
    last_error = "no attempt made"

    for _attempt in range(max_attempts):
        raw = await provider.diagnose(context, prompt_version=prompt_version)
        try:
            parsed = _extract_json_object(raw.text)
            model_json = ModelDiagnosisJSON.model_validate(parsed)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)
            continue

        diagnosis = _apply_safeguards(Diagnosis.from_model_json(model_json), context)
        return diagnosis, raw

    raise DiagnosisValidationError(last_error, attempts=max_attempts)
