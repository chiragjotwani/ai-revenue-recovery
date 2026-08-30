"""Decision domain contracts (Phase 5A: Decision Domain & Contracts).

This module defines ONLY the types Phase 5 needs. No policy logic (5B), no
decision service (5C), no persistence (5E), no API (5F). It exists so later
workstreams share one vocabulary instead of each inventing their own.

Phase 5 is the Recovery Decision & Policy Engine (see the Phase 5
Architecture Revision report). It is deterministic and makes no AI calls;
it reads the Phase 4 ``Diagnosis`` (``app/ai/schema.py``) as its only
AI-derived input. Nothing here duplicates a Phase 4 concept -- strategies
are the existing ``RecoveryStrategy`` enum, reused, not re-declared.

Key decisions this module encodes (see the architecture revision for the
full rationale):

* ``Recoverability`` is a **policy-derived classification**, never a
  predictive probability or calibrated score -- there is deliberately no
  confidence threshold anywhere in this module.
* ``candidate_strategy`` is always the diagnosis's own
  ``recommended_strategy``, unchanged. Phase 5 does not generate
  alternative candidates the AI never proposed (Architecture Revision,
  Issue 2).
* ``scheduled_not_before`` derives from the diagnosis's own
  ``recommended_delay_hours`` (already bounded 0-720h by
  ``ModelDiagnosisJSON``). Phase 5 introduces no separate cooldown
  constant.
* Decision identity is exactly ``(case_id, diagnosis_id)`` -- the sole
  idempotency key for a later workstream's database uniqueness constraint
  (Architecture Revision, Issue 8 / KI-008). This module only represents
  that identity; it enforces nothing.
* ``decision_engine_version`` is the one version field (no separate
  "policy_version" -- Architecture Revision, Issue 7).
* Rationale is built exclusively from typed rule outcomes, never from
  parsing a diagnosis's free-text ``reasoning`` (Architecture Revision,
  Issue 11 / AI Trust Boundary). ``reasoning`` never appears in this
  module.
* No monetary/high-value field exists anywhere here (Architecture
  Revision, Issue 4 -- KI-006 remains unresolved for cross-currency
  amounts, so Phase 5 does not introduce an amount-based rule).
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.ai.schema import RecoveryStrategy

DECISION_ENGINE_VERSION: Literal["1"] = "1"


class Recoverability(str, enum.Enum):
    """Policy-derived recoverability classification (Phase 5).

    NOT a predictive probability, NOT a calibrated score, NOT an ML output.
    The policy engine (5B) computes this from the diagnosis's
    ``disposition`` plus the context builder's ``evidence_sufficiency`` /
    ``signals_conflict`` signals (``app/ai/context_builder.py``). This
    module only defines the vocabulary.
    """

    LIKELY_RECOVERABLE = "likely_recoverable"
    CONDITIONALLY_RECOVERABLE = "conditionally_recoverable"
    NOT_RECOVERABLE_AUTOMATICALLY = "not_recoverable_automatically"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class DecisionStatus(str, enum.Enum):
    """The outcome of a Phase 5 decision attempt for one (case, diagnosis).

    * ``APPROVED`` -- an admissible decision exists; the case may advance.
      "Already paid" (``approved_strategy = no_action``) and a retry-cap
      downgrade to ``manual_review`` are both ``APPROVED`` -- an approved
      decision to do nothing, or to escalate, is still a decision, not a
      failure.
    * ``REJECTED`` -- no admissible decision could be made; the case does
      not advance (stays in ``DIAGNOSED``).
    * ``ESCALATED`` -- the decision routes to manual review because
      evidence is insufficient or the disposition is suspected fraud.
    * ``SUPERSEDED`` -- a newer diagnosis exists for the case; an older
      ``DecisionResult`` is marked superseded rather than deleted
      (append-only, same philosophy as ``RecoveryCaseTransition``).
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    SUPERSEDED = "superseded"


class DecisionRationaleEntry(BaseModel):
    """One deterministic policy-rule outcome contributing to a decision.

    Rationale is built exclusively from typed rule evaluations -- never by
    parsing a diagnosis's free-text ``reasoning`` field. ``reasoning`` is
    display-only and is never a policy input (Phase 5 AI Trust Boundary).
    """

    model_config = {"extra": "forbid"}

    rule_id: str = Field(min_length=1, max_length=100)
    outcome: Literal["passed", "failed", "not_applicable"]
    reason_code: str | None = Field(default=None, max_length=100)


class DecisionIdentity(BaseModel):
    """The authoritative identity of a decision attempt.

    ``(case_id, diagnosis_id)`` is the sole idempotency key: a decision is
    a function of exactly one case deciding against exactly one diagnosis.
    Database-level uniqueness and any existence check are a later
    workstream (5C/5E) -- this type only represents the identity.
    """

    model_config = {"extra": "forbid"}

    case_id: UUID
    diagnosis_id: UUID


class DecisionResult(BaseModel):
    """A Phase 5 decision: what happened when a diagnosis was evaluated
    against deterministic policy.

    ``candidate_strategy`` is the diagnosis's own ``recommended_strategy``,
    unchanged. ``approved_strategy`` is what policy actually allows; it MAY
    differ from ``candidate_strategy`` (a downgrade), never the reverse --
    policy never upgrades a strategy the AI did not propose. Comparing the
    two is how Phase 8 will later tell whether a policy override happened.

    ``scheduled_not_before`` is ``None`` for strategies with no timing
    meaning (``manual_review``, ``no_action``).
    """

    model_config = {"extra": "forbid"}

    identity: DecisionIdentity
    recoverability: Recoverability
    candidate_strategy: RecoveryStrategy
    approved_strategy: RecoveryStrategy
    decision_status: DecisionStatus
    rationale: list[DecisionRationaleEntry] = Field(default_factory=list)
    scheduled_not_before: datetime | None = None
    decision_engine_version: Literal["1"] = DECISION_ENGINE_VERSION
