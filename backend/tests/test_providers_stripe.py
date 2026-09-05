"""Stripe TEST-mode payment provider (Phase 16: Real Payment Integration).

Uses ``httpx.MockTransport`` (the same test seam
``app.ai.providers.openai_compatible`` already established) rather than a
real network call to api.stripe.com -- these tests must be able to run
without a real Stripe account. A live-key smoke test would need a real
``sk_test_...`` key, which does not exist in this environment; this suite
verifies the request/response contract and the config/routing decisions
instead, which are the parts entirely under this codebase's control.
"""

from __future__ import annotations

import httpx
import pytest

from app.decision.providers import (
    ProviderAttemptResult,
    SimulatedPaymentProvider,
    SimulationOutcome,
    simulated_payment_provider,
)
from app.decision.providers_stripe import (
    StripeConfigurationError,
    StripePaymentProvider,
    select_retry_provider,
)


def test_live_key_is_rejected() -> None:
    with pytest.raises(StripeConfigurationError, match="TEST secret key"):
        StripePaymentProvider("sk_live_something")


def test_test_key_is_accepted() -> None:
    StripePaymentProvider("sk_test_something")  # must not raise


def _provider_for(handler: httpx.MockTransport) -> StripePaymentProvider:
    return StripePaymentProvider("sk_test_fake", transport=handler)


async def test_succeeded_payment_intent_is_a_real_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"] == "arr-retry:corr-1:1"
        return httpx.Response(200, json={"id": "pi_123", "status": "succeeded"})

    provider = _provider_for(httpx.MockTransport(handler))
    result = await provider.attempt(
        channel="retry", failure_reason="insufficient_funds", attempt_no=1, correlation_id="corr-1"
    )
    assert result.outcome is SimulationOutcome.SUCCESS
    assert result.is_real is True
    assert result.simulated_reference == "stripe:pi_123"


async def test_retriable_decline_is_a_real_temporary_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pi_456",
                "status": "requires_payment_method",
                "last_payment_error": {"decline_code": "insufficient_funds"},
            },
        )

    provider = _provider_for(httpx.MockTransport(handler))
    result = await provider.attempt(
        channel="retry", failure_reason="do_not_honor", attempt_no=1, correlation_id="corr-2"
    )
    assert result.outcome is SimulationOutcome.TEMPORARY_FAILURE
    assert result.is_real is True


async def test_non_retriable_decline_is_a_real_permanent_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pi_789",
                "status": "requires_payment_method",
                "last_payment_error": {"decline_code": "processing_error"},
            },
        )

    provider = _provider_for(httpx.MockTransport(handler))
    result = await provider.attempt(
        channel="retry", failure_reason="processing_error", attempt_no=1, correlation_id="corr-3"
    )
    assert result.outcome is SimulationOutcome.PERMANENT_FAILURE
    assert result.is_real is True


async def test_stripe_server_error_is_a_real_temporary_failure() -> None:
    provider = _provider_for(httpx.MockTransport(lambda _req: httpx.Response(500, text="boom")))
    result = await provider.attempt(
        channel="retry", failure_reason="insufficient_funds", attempt_no=1, correlation_id="corr-4"
    )
    assert result.outcome is SimulationOutcome.TEMPORARY_FAILURE
    assert result.is_real is True


async def test_transport_error_is_a_real_temporary_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider_for(httpx.MockTransport(handler))
    result = await provider.attempt(
        channel="retry", failure_reason="insufficient_funds", attempt_no=1, correlation_id="corr-5"
    )
    assert result.outcome is SimulationOutcome.TEMPORARY_FAILURE
    assert result.is_real is True


async def test_amount_sent_is_a_fixed_nominal_value_never_caller_supplied() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(x.split("=") for x in request.content.decode().split("&")))
        return httpx.Response(200, json={"id": "pi_amt", "status": "succeeded"})

    provider = _provider_for(httpx.MockTransport(handler))
    await provider.attempt(
        channel="retry", failure_reason="insufficient_funds", attempt_no=1, correlation_id="corr-6"
    )
    assert captured["amount"] == "100"
    assert captured["currency"] == "usd"


def test_select_retry_provider_falls_back_to_simulated_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings

    monkeypatch.setattr(
        "app.decision.providers_stripe.get_settings", lambda: Settings(stripe_api_key=None)
    )
    provider = select_retry_provider()
    assert provider is simulated_payment_provider


def test_select_retry_provider_returns_stripe_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings

    monkeypatch.setattr(
        "app.decision.providers_stripe.get_settings",
        lambda: Settings(stripe_api_key="sk_test_configured"),
    )
    provider = select_retry_provider()
    assert isinstance(provider, StripePaymentProvider)


async def test_simulated_provider_result_is_never_marked_real() -> None:
    result = await simulated_payment_provider.attempt(
        channel="retry", failure_reason="insufficient_funds", attempt_no=1, correlation_id="corr-7"
    )
    assert result.is_real is False


def test_provider_attempt_result_default_is_real_is_false() -> None:
    result = ProviderAttemptResult(
        outcome=SimulationOutcome.SUCCESS, detail="d", simulated_reference="sim:x"
    )
    assert result.is_real is False


async def test_retry_executor_uses_stripe_when_provider_injected() -> None:
    from app.decision.executors import RetryExecutor

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "pi_exec", "status": "succeeded"})

    stripe_provider = StripePaymentProvider("sk_test_fake", transport=httpx.MockTransport(handler))
    executor = RetryExecutor(provider=stripe_provider)
    result = await executor.attempt(
        failure_reason="insufficient_funds", attempt_no=1, correlation_id="corr-8"
    )
    assert result.is_real is True
    assert result.outcome is SimulationOutcome.SUCCESS


async def test_retry_executor_defaults_to_simulated_when_no_stripe_key_configured() -> None:
    from app.decision.executors import RetryExecutor

    executor = RetryExecutor()
    assert isinstance(executor._provider, SimulatedPaymentProvider)
