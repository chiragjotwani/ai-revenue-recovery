"""Versioned diagnosis prompts (Phase 4, Section 50).

Every production prompt has a version string that is stored with every
diagnosis. A prompt is never edited in place -- a change means a new
``diagnosis_prompt_vN`` and a new constant here.
"""

from __future__ import annotations

import json

from app.ai.context_builder import RecoveryContext
from app.ai.schema import DiagnosisOutcome, RecoveryStrategy

DIAGNOSIS_PROMPT_VERSION = "diagnosis_prompt_v1"

_OUTCOME_VALUES = ", ".join(o.value for o in DiagnosisOutcome)
_STRATEGY_VALUES = ", ".join(s.value for s in RecoveryStrategy)

_SYSTEM = f"""\
You are a payment-failure diagnosis assistant for a revenue-recovery platform.
You are given a bounded summary of one failed payment and the customer's
history. Judge the most likely CAUSE of the failure and suggest a recovery
approach.

Rules:
- Reply with a single JSON object and nothing else. No prose, no markdown.
- "outcome" must be exactly one of: {_OUTCOME_VALUES}.
- If the evidence is insufficient to choose a specific cause, use
  "unknown" -- that is a correct answer, not a failure.
- "confidence" is a number from 0 to 1.
- "recommended_strategy" must be exactly one of: {_STRATEGY_VALUES}.
- "recommended_delay_hours" is an integer number of hours (0-720) or null.
- You never execute anything. Your output is advisory and is validated and
  overridden by a separate policy engine.

JSON shape:
{{"outcome": "...", "confidence": 0.0, "reasoning": "one or two sentences",
  "recommended_strategy": "...", "recommended_delay_hours": null}}
"""


def render_diagnosis_messages(context: RecoveryContext) -> list[dict[str, str]]:
    """Build the chat messages for a diagnosis request.

    Returns a list of ``{"role", "content"}`` dicts in the OpenAI chat
    format, which every supported provider accepts.
    """
    payload = context.model_dump(mode="json")
    user = (
        "Diagnose this failed payment. Context:\n"
        + json.dumps(payload, indent=2, sort_keys=True)
        + "\n\nRespond with the JSON object only."
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
