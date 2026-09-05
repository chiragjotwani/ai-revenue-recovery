"""Phase 11: integration tests for historical case retrieval
(``app.retrieval.service``) and its API surface
(``GET /recovery/cases/{id}/similar-cases``).

Deliberately scoped: "embeddings" are deterministic structured-feature
vectors, never a learned/neural embedding, and retrieval never touches
the Phase 4 diagnosis prompt/context-builder pipeline (see
``app/retrieval/schema.py``'s module docstring). These tests assert that
scope boundary explicitly, and include a small retrieval-correctness
"evaluation" (does it rank matching cases higher?), not an accuracy claim.

Real Postgres, real HTTP (project policy: no mocking the database).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case_feature_vector import CaseFeatureVector

BASE = datetime(2026, 3, 1, tzinfo=UTC)


async def _ingest_one(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    occurred_at: datetime,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-retr",
    amount: str = "4999.00",
) -> dict:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": occurred_at.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "retr@e.com"},
        "payment": {
            "external_reference": external_reference,
            "amount": amount,
            "currency": "inr",
            "failure_reason": failure_reason,
        },
    }
    r = await client.post("/events", json=payload)
    assert r.status_code == 201
    return r.json()


async def _open_case(client: AsyncClient, payment_id: str) -> uuid.UUID:
    r = await client.post("/recovery/cases", json={"payment_id": payment_id})
    assert r.status_code == 201
    return uuid.UUID(r.json()["id"])


async def _diagnosed_case(
    client: AsyncClient,
    *,
    external_reference: str,
    customer_external_id: str,
    failure_reason: str = "insufficient_funds",
    amount: str = "4999.00",
) -> uuid.UUID:
    for i in range(3):
        await _ingest_one(
            client,
            idempotency_key=f"{external_reference}-s{i}",
            external_reference=f"{external_reference}-s{i}",
            occurred_at=BASE - timedelta(days=30 - i),
            event_type="payment.succeeded",
            failure_reason=None,
            customer_external_id=customer_external_id,
            amount=amount,
        )
    payment = await _ingest_one(
        client,
        idempotency_key=f"{external_reference}-f",
        external_reference=external_reference,
        occurred_at=BASE,
        failure_reason=failure_reason,
        customer_external_id=customer_external_id,
        amount=amount,
    )
    case_id = await _open_case(client, payment["payment_id"])
    assert (await client.post(f"/recovery/cases/{case_id}/diagnose")).status_code == 200
    return case_id


# --- historical case retrieval / similarity search --------------------------


async def test_similar_cases_before_any_history_returns_empty(client: AsyncClient) -> None:
    case_id = await _diagnosed_case(client, external_reference="r1", customer_external_id="cust-r1")
    r = await client.get(f"/recovery/cases/{case_id}/similar-cases")
    assert r.status_code == 200
    assert r.json() == []  # no other diagnosed cases exist yet -- never fabricated


async def test_similar_cases_never_includes_the_query_case_itself(client: AsyncClient) -> None:
    case_id = await _diagnosed_case(
        client, external_reference="r2a", customer_external_id="cust-r2a"
    )
    await _diagnosed_case(client, external_reference="r2b", customer_external_id="cust-r2b")

    results = (await client.get(f"/recovery/cases/{case_id}/similar-cases")).json()
    assert all(row["case_id"] != str(case_id) for row in results)


async def test_similar_cases_before_diagnosis_returns_409(client: AsyncClient) -> None:
    payment = await _ingest_one(
        client, idempotency_key="r3-f", external_reference="r3-f", occurred_at=BASE
    )
    case_id = await _open_case(client, payment["payment_id"])  # detected, not diagnosed
    r = await client.get(f"/recovery/cases/{case_id}/similar-cases")
    assert r.status_code == 409


async def test_similar_cases_unknown_case_returns_404(client: AsyncClient) -> None:
    r = await client.get(f"/recovery/cases/{uuid.uuid4()}/similar-cases")
    assert r.status_code == 404


# --- "retrieval evaluation": does it rank matching cases higher? -----------
# A correctness check of the mechanism, never an accuracy/impact claim
# (KI-007 discipline extended here -- see app/retrieval/schema.py).


async def test_matching_disposition_and_outcome_ranks_above_a_dissimilar_case(
    client: AsyncClient,
) -> None:
    query_case = await _diagnosed_case(
        client,
        external_reference="r4-query",
        customer_external_id="cust-r4-query",
        failure_reason="insufficient_funds",  # -> retriable_transient
        amount="5000.00",
    )
    matching_case = await _diagnosed_case(
        client,
        external_reference="r4-match",
        customer_external_id="cust-r4-match",
        failure_reason="insufficient_funds",  # same outcome/disposition, similar amount
        amount="5100.00",
    )
    dissimilar_case = await _diagnosed_case(
        client,
        external_reference="r4-diff",
        customer_external_id="cust-r4-diff",
        failure_reason="fraud_suspected",  # -> suspected_fraud, very different
        amount="50.00",
    )

    results = (await client.get(f"/recovery/cases/{query_case}/similar-cases")).json()
    by_case = {row["case_id"]: row["similarity"] for row in results}
    assert by_case[str(matching_case)] > by_case[str(dissimilar_case)]


async def test_similarity_scores_are_deterministic_across_repeated_calls(
    client: AsyncClient,
) -> None:
    query_case = await _diagnosed_case(
        client, external_reference="r5-query", customer_external_id="cust-r5-query"
    )
    await _diagnosed_case(
        client, external_reference="r5-other", customer_external_id="cust-r5-other"
    )

    first = (await client.get(f"/recovery/cases/{query_case}/similar-cases")).json()
    second = (await client.get(f"/recovery/cases/{query_case}/similar-cases")).json()
    assert first == second


# --- vector storage idempotency (KI-008 discipline) --------------------------


async def test_repeated_retrieval_does_not_duplicate_the_stored_vector(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    case_id = await _diagnosed_case(client, external_reference="r6", customer_external_id="cust-r6")
    for _ in range(3):
        await client.get(f"/recovery/cases/{case_id}/similar-cases")

    row_count = await db_session.scalar(
        select(func.count())
        .select_from(CaseFeatureVector)
        .where(CaseFeatureVector.case_id == case_id)
    )
    assert row_count == 1


# --- separation from Phase 4/5/6/7/8 -----------------------------------------


async def test_retrieval_never_changes_the_decision_or_diagnosis(client: AsyncClient) -> None:
    case_id = await _diagnosed_case(client, external_reference="r7", customer_external_id="cust-r7")
    await client.post(f"/recovery/cases/{case_id}/decide")
    before = (await client.get(f"/recovery/cases/{case_id}")).json()

    await client.get(f"/recovery/cases/{case_id}/similar-cases")

    after = (await client.get(f"/recovery/cases/{case_id}")).json()
    assert before == after


def test_similar_case_schema_never_includes_free_text_reasoning() -> None:
    """The AI trust boundary Phase 4/5 already established (never let raw
    free-text into an automated pathway) extends to retrieval: structural
    guarantee, not just a docstring claim.
    """
    from app.retrieval.schema import SimilarCase

    field_names = set(SimilarCase.model_fields)
    assert "reasoning" not in field_names
    for forbidden in ("probability", "prediction", "embedding_model"):
        assert not any(forbidden in name for name in field_names), field_names
