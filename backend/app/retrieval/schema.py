"""Historical case retrieval domain contracts (Phase 11: Historical
Recovery Intelligence -- scoped).

Phase 11's master plan (``docs/master-loop-engineering-prompt.md``,
Section 30) calls for "embeddings," "historical case retrieval," "vector
storage," "similarity search," "retrieval context," and "retrieval
evaluation".

Scope decision (owner-confirmed this session, same discipline as Phase 9
and Phase 10): "embeddings" here are DETERMINISTIC STRUCTURED-FEATURE
VECTORS -- a one-hot/normalized encoding of a case's already-recorded
disposition, outcome, evidence signals, and payment amount -- never a
learned/neural embedding. No embedding-model endpoint exists anywhere in
this repository (the same infrastructure gap KI-002 already documents for
the reasoning-model layer), and Phase 4 already establishes the precedent
this module follows: the system must work with no model infrastructure
(``MockProvider`` is the default, not an afterthought). Presenting a
neural-embedding-shaped feature as if a real embedding model computed it
would misrepresent a deterministic function as a learned one -- exactly
the overclaim Phase 9/10 already refused to make elsewhere.

"Retrieval context" is scoped to an operator-facing "similar past cases"
lookup (``GET /recovery/cases/{id}/similar-cases``) -- it does NOT modify
Phase 4's frozen diagnosis prompt/context-builder pipeline (no new prompt
version, no change to the four-layer prompt-injection boundary
``tests/test_ai_prompt_injection.py`` already covers). Historical-case
retrieval and similarity search are fully realizable without touching
that frozen pipeline, so this module does not reopen it.

"Retrieval evaluation" is a correctness check of the retrieval mechanism
itself (does it rank matching-disposition/outcome cases higher?), not a
claim about improved diagnosis accuracy -- there is no ground truth in
this repository that could support such a claim (KI-007 applies with
equal force here).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

#: Bumped only if the feature-vectorization function changes -- stored
#: alongside every persisted vector so old and new vectors are never
#: silently compared against each other.
FEATURE_VERSION = "1"


class SimilarCase(BaseModel):
    """One retrieved historical case, ranked by deterministic feature
    similarity. Never includes free-text reasoning (the AI trust
    boundary Phase 4 already established for policy inputs extends here
    too -- similarity is computed from typed fields only).
    """

    model_config = {"extra": "forbid"}

    case_id: UUID
    diagnosis_id: UUID
    similarity: float
    disposition: str
    outcome: str
    approved_strategy: str | None
    decision_status: str | None
    observed_outcome: str | None
