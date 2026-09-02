"""Deterministic structured-feature vectorization (Phase 11). See
``app.retrieval.schema`` module docstring for the scope decision this
implements: NOT a learned/neural embedding.

The vector is a pure function of already-recorded, typed fields --
disposition, outcome, model-reported confidence, consecutive failures,
historical success rate, and payment amount (log-scaled so a 50 vs. 5000
payment doesn't dominate the distance). No free-text (``reasoning``) ever
enters it -- same AI trust boundary discipline
``app.decision.policy``/``app.outcome.service`` already apply to their own
inputs.
"""

from __future__ import annotations

import math
from decimal import Decimal

from app.ai.schema import DiagnosisDisposition, DiagnosisOutcome

FEATURE_VERSION = "1"

# Fixed, stable ordering -- the position of each one-hot slot must never
# change once vectors have been persisted under this FEATURE_VERSION (a
# reordering would silently corrupt every stored vector's meaning).
_OUTCOMES: tuple[DiagnosisOutcome, ...] = tuple(DiagnosisOutcome)
_DISPOSITIONS: tuple[DiagnosisDisposition, ...] = tuple(DiagnosisDisposition)

#: outcome one-hot + disposition one-hot + 4 numeric features.
VECTOR_LENGTH = len(_OUTCOMES) + len(_DISPOSITIONS) + 4


def compute_case_features(
    *,
    outcome: DiagnosisOutcome,
    disposition: DiagnosisDisposition,
    confidence: Decimal | float,
    consecutive_failures: int,
    historical_success_rate: float,
    amount: Decimal | float,
) -> list[float]:
    """A deterministic vector: same inputs always produce the exact same
    output, byte-for-byte -- no randomness, no external call, no model.
    """
    outcome_one_hot = [1.0 if o is outcome else 0.0 for o in _OUTCOMES]
    disposition_one_hot = [1.0 if d is disposition else 0.0 for d in _DISPOSITIONS]

    numeric = [
        float(confidence),
        # Bounded, monotonic squashing so an unusually long streak of
        # failures doesn't dominate the distance the way a raw count would.
        min(float(consecutive_failures) / 10.0, 1.0),
        float(historical_success_rate),
        # log1p keeps amount comparable in scale to the other [0, 1]-ish
        # features without needing a currency-specific normalization
        # (KI-006 discipline: this is a shape feature, never a monetary
        # aggregate, and is never compared or summed across currencies).
        math.log1p(max(float(amount), 0.0)) / 20.0,
    ]

    return [*outcome_one_hot, *disposition_one_hot, *numeric]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain-Python cosine similarity -- no numpy dependency for a
    handful of small, fixed-length vectors. Returns 0.0 for a zero
    vector (never raises a division-by-zero).
    """
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
