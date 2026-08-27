from app.risk.features import RiskFeatures

# Deterministic severity weights for known failure reasons, in [0, 1].
# Higher means less likely to self-resolve without intervention. This is a
# rule-based judgment call (Section 5/44: no ML model here yet -- that is
# Phase 9's strategy learning). Unknown/unlisted reasons default to 0.6:
# treated as moderately severe because the uncertainty itself is a risk
# factor, not because it is assumed to be a hard decline.
_FAILURE_REASON_SEVERITY: dict[str, float] = {
    "insufficient_funds": 0.3,
    "card_expired": 0.5,
    "do_not_honor": 0.6,
    "processing_error": 0.4,
    "stolen_card": 0.9,
    "lost_card": 0.9,
    "fraud_suspected": 0.95,
}
_DEFAULT_FAILURE_REASON_SEVERITY = 0.6

# Weights must sum to 1.0; enforced by test_risk_scoring.py.
_CONSECUTIVE_FAILURES_WEIGHT = 0.4
_FAILURE_REASON_WEIGHT = 0.4
_UNRELIABILITY_WEIGHT = 0.2

# Consecutive failures at or above this count contribute the maximum
# possible weight for that factor.
_CONSECUTIVE_FAILURES_SATURATION = 3

RiskLevel = str  # "low" | "medium" | "high"

_LOW_THRESHOLD = 0.34
_MEDIUM_THRESHOLD = 0.67


def failure_reason_severity(failure_reason: str | None) -> float:
    if failure_reason is None:
        return _DEFAULT_FAILURE_REASON_SEVERITY
    return _FAILURE_REASON_SEVERITY.get(failure_reason, _DEFAULT_FAILURE_REASON_SEVERITY)


def compute_risk_score(features: RiskFeatures) -> float:
    """Deterministic, explainable rule-based risk score in [0, 1].

    Higher means less likely this revenue is recovered without
    intervention. Every input is a feature computed from Postgres
    (ADR-001) -- there is no ML or LLM involvement at this phase.
    """
    consecutive_factor = min(features.consecutive_failures / _CONSECUTIVE_FAILURES_SATURATION, 1.0)
    reason_factor = failure_reason_severity(features.failure_reason)
    unreliability_factor = 1.0 - features.historical_success_rate

    score = (
        consecutive_factor * _CONSECUTIVE_FAILURES_WEIGHT
        + reason_factor * _FAILURE_REASON_WEIGHT
        + unreliability_factor * _UNRELIABILITY_WEIGHT
    )
    return round(min(max(score, 0.0), 1.0), 4)


def risk_level(score: float) -> RiskLevel:
    if score < _LOW_THRESHOLD:
        return "low"
    if score < _MEDIUM_THRESHOLD:
        return "medium"
    return "high"
