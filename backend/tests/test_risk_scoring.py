from app.risk.features import RiskFeatures
from app.risk.scoring import (
    _CONSECUTIVE_FAILURES_WEIGHT,
    _FAILURE_REASON_WEIGHT,
    _UNRELIABILITY_WEIGHT,
    compute_risk_score,
    failure_reason_severity,
    risk_level,
)


def test_scoring_weights_sum_to_one() -> None:
    assert _CONSECUTIVE_FAILURES_WEIGHT + _FAILURE_REASON_WEIGHT + _UNRELIABILITY_WEIGHT == 1.0


def test_score_is_bounded_between_zero_and_one() -> None:
    worst = RiskFeatures(
        consecutive_failures=100,
        historical_success_rate=0.0,
        has_prior_success=False,
        failure_reason="fraud_suspected",
    )
    best = RiskFeatures(
        consecutive_failures=0,
        historical_success_rate=1.0,
        has_prior_success=True,
        failure_reason="insufficient_funds",
    )
    assert 0.0 <= compute_risk_score(worst) <= 1.0
    assert 0.0 <= compute_risk_score(best) <= 1.0
    assert compute_risk_score(worst) > compute_risk_score(best)


def test_more_consecutive_failures_increases_score() -> None:
    low = RiskFeatures(
        consecutive_failures=1,
        historical_success_rate=0.5,
        has_prior_success=True,
        failure_reason="insufficient_funds",
    )
    high = RiskFeatures(
        consecutive_failures=5,
        historical_success_rate=0.5,
        has_prior_success=True,
        failure_reason="insufficient_funds",
    )
    assert compute_risk_score(high) > compute_risk_score(low)


def test_higher_historical_success_rate_decreases_score() -> None:
    reliable = RiskFeatures(
        consecutive_failures=1,
        historical_success_rate=0.9,
        has_prior_success=True,
        failure_reason="insufficient_funds",
    )
    unreliable = RiskFeatures(
        consecutive_failures=1,
        historical_success_rate=0.1,
        has_prior_success=True,
        failure_reason="insufficient_funds",
    )
    assert compute_risk_score(reliable) < compute_risk_score(unreliable)


def test_unknown_failure_reason_uses_default_severity() -> None:
    assert failure_reason_severity("some_reason_never_seen_before") == 0.6
    assert failure_reason_severity(None) == 0.6


def test_risk_level_buckets() -> None:
    assert risk_level(0.0) == "low"
    assert risk_level(0.33) == "low"
    assert risk_level(0.34) == "medium"
    assert risk_level(0.66) == "medium"
    assert risk_level(0.67) == "high"
    assert risk_level(1.0) == "high"
