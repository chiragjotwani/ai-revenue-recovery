"""Outcome domain contracts (Phase 7: Outcome Observation & Recovery Outcome).

This module defines ONLY the vocabulary Phase 7 needs, mirroring
``app.decision.schema``'s own scope discipline: no persistence, no
observation logic, no API.

Phase 7 is the Outcome Observation layer. It is deterministic and makes no
AI calls -- it reads only authoritative payment/event evidence already
represented by ``app.models.payment.Payment`` (the same source of truth
``app.decision.service``'s own ``already_paid`` check and
``app.risk.service``'s at-risk-payment exclusion both already read).

Key decisions this module encodes:

* ``ObservedOutcome`` is a classification of REALITY, never a prediction.
  "Action executed" (Phase 6) never implies "recovered" -- Phase 7 exists
  precisely to keep those two facts distinct.
* ``UNRESOLVED`` is a first-class, safe, non-terminal outcome -- absence
  of conclusive evidence is represented explicitly, never guessed past.
* No confidence field, no monetary field, no FX assumption, no arbitrary
  timeout -- same discipline as ``app.decision.schema``
  (KI-006/ADR-006) and for the same reason: this project does not invent
  a business rule where the architecture supplies none.
"""

from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel


class ObservedOutcome(str, enum.Enum):
    """What was observed to have actually happened, from authoritative
    payment/event evidence -- never from AI reasoning, never from the
    mere fact that an action executed.

    * ``RECOVERED`` -- a later ``payment.succeeded`` event exists for the
      same customer, after the originally failed payment. The exact same
      deterministic relationship ``app.decision.service``'s own
      ``already_paid`` check and ``app.risk.service``'s at-risk exclusion
      already use -- Phase 7 does not invent a new correlation rule.
    * ``NOT_RECOVERED`` -- a later ``payment.failed`` event exists for the
      same customer, after the originally failed payment, with no
      intervening success. Real, observed, negative evidence -- not a
      guess, not a timeout.
    * ``UNRESOLVED`` -- neither of the above. The honest default: no
      conclusive evidence exists yet.
    """

    RECOVERED = "recovered"
    NOT_RECOVERED = "not_recovered"
    UNRESOLVED = "unresolved"


class OutcomeClassification(BaseModel):
    """The pure classification result for one observation attempt.

    Deliberately identity-free (no case_id/action_id/attempt_no) -- the
    observation service combines this with both when it persists a
    ``RecoveryOutcomeObservation`` row, the same separation
    ``app.decision.policy.PolicyOutcome`` keeps from
    ``app.decision.schema.DecisionResult``.
    """

    model_config = {"extra": "forbid"}

    outcome: ObservedOutcome
    is_terminal: bool
    evidence_payment_id: UUID | None = None
