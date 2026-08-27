"""Integration tests for the recovery case API, against real Postgres."""

import uuid
from datetime import UTC, datetime

from httpx import AsyncClient

BASE_TIME = datetime(2026, 2, 1, tzinfo=UTC)


async def _ingest_payment(
    client: AsyncClient,
    *,
    idempotency_key: str,
    external_reference: str,
    event_type: str = "payment.failed",
    failure_reason: str | None = "insufficient_funds",
    customer_external_id: str = "cust-rec-1",
) -> str:
    payload = {
        "idempotency_key": idempotency_key,
        "event_type": event_type,
        "source": "test-suite",
        "occurred_at": BASE_TIME.isoformat(),
        "customer": {"external_id": customer_external_id, "email": "c@example.com"},
        "payment": {
            "external_reference": external_reference,
            "amount": "4999.00",
            "currency": "inr",
            "failure_reason": failure_reason,
        },
    }
    response = await client.post("/events", json=payload)
    assert response.status_code == 201
    return response.json()["payment_id"]


async def _open_case(client: AsyncClient, payment_id: str) -> dict:
    response = await client.post("/recovery/cases", json={"payment_id": payment_id})
    assert response.status_code == 201, response.text
    return response.json()


async def test_open_case_for_failed_payment(client: AsyncClient) -> None:
    payment_id = await _ingest_payment(client, idempotency_key="e1", external_reference="p1")

    response = await client.post("/recovery/cases", json={"payment_id": payment_id})

    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "detected"
    assert body["payment_id"] == payment_id
    assert body["closed_at"] is None

    detail = (await client.get(f"/recovery/cases/{body['id']}")).json()
    assert len(detail["history"]) == 1
    assert detail["history"][0]["from_state"] is None
    assert detail["history"][0]["to_state"] == "detected"
    assert detail["history"][0]["actor"] == "api"


async def test_open_case_is_idempotent_on_payment(client: AsyncClient) -> None:
    payment_id = await _ingest_payment(client, idempotency_key="e1", external_reference="p1")

    first = await client.post("/recovery/cases", json={"payment_id": payment_id})
    second = await client.post("/recovery/cases", json={"payment_id": payment_id})

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    listed = (await client.get("/recovery/cases")).json()
    assert len(listed) == 1

    detail = (await client.get(f"/recovery/cases/{first.json()['id']}")).json()
    assert len(detail["history"]) == 1


async def test_open_case_unknown_payment_is_404(client: AsyncClient) -> None:
    response = await client.post("/recovery/cases", json={"payment_id": str(uuid.uuid4())})
    assert response.status_code == 404


async def test_open_case_for_non_failed_payment_is_409(client: AsyncClient) -> None:
    payment_id = await _ingest_payment(
        client,
        idempotency_key="e1",
        external_reference="p1",
        event_type="payment.succeeded",
        failure_reason=None,
    )

    response = await client.post("/recovery/cases", json={"payment_id": payment_id})
    assert response.status_code == 409


async def test_legal_transition_via_api(client: AsyncClient) -> None:
    payment_id = await _ingest_payment(client, idempotency_key="e1", external_reference="p1")
    case = await _open_case(client, payment_id)

    response = await client.post(
        f"/recovery/cases/{case['id']}/transitions",
        json={"to_state": "diagnosing", "reason": "starting diagnosis"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "diagnosing"

    detail = (await client.get(f"/recovery/cases/{case['id']}")).json()
    assert [h["to_state"] for h in detail["history"]] == ["detected", "diagnosing"]
    assert detail["history"][-1]["from_state"] == "detected"
    assert detail["history"][-1]["reason"] == "starting diagnosis"


async def test_illegal_transition_via_api_is_409_and_changes_nothing(client: AsyncClient) -> None:
    payment_id = await _ingest_payment(client, idempotency_key="e1", external_reference="p1")
    case = await _open_case(client, payment_id)

    response = await client.post(
        f"/recovery/cases/{case['id']}/transitions", json={"to_state": "recovered"}
    )
    assert response.status_code == 409

    detail = (await client.get(f"/recovery/cases/{case['id']}")).json()
    assert detail["state"] == "detected"
    assert len(detail["history"]) == 1


async def test_transition_unknown_case_is_404(client: AsyncClient) -> None:
    response = await client.post(
        f"/recovery/cases/{uuid.uuid4()}/transitions", json={"to_state": "diagnosing"}
    )
    assert response.status_code == 404


async def test_terminal_transition_sets_closed_at_and_freezes_the_case(client: AsyncClient) -> None:
    payment_id = await _ingest_payment(client, idempotency_key="e1", external_reference="p1")
    case = await _open_case(client, payment_id)

    abandoned = await client.post(
        f"/recovery/cases/{case['id']}/transitions",
        json={"to_state": "abandoned", "reason": "duplicate of another case"},
    )
    assert abandoned.status_code == 200
    assert abandoned.json()["state"] == "abandoned"
    assert abandoned.json()["closed_at"] is not None

    # Nothing leaves a terminal state.
    again = await client.post(
        f"/recovery/cases/{case['id']}/transitions", json={"to_state": "diagnosing"}
    )
    assert again.status_code == 409


async def test_list_filters_by_state(client: AsyncClient) -> None:
    p1 = await _ingest_payment(client, idempotency_key="e1", external_reference="p1")
    p2 = await _ingest_payment(
        client,
        idempotency_key="e2",
        external_reference="p2",
        customer_external_id="cust-rec-2",
    )
    c1 = await _open_case(client, p1)
    await _open_case(client, p2)

    await client.post(f"/recovery/cases/{c1['id']}/transitions", json={"to_state": "diagnosing"})

    all_cases = (await client.get("/recovery/cases")).json()
    assert len(all_cases) == 2

    detected = (await client.get("/recovery/cases", params={"state": "detected"})).json()
    assert [c["id"] for c in detected] == [c["id"] for c in all_cases if c["state"] == "detected"]
    assert len(detected) == 1

    diagnosing = (await client.get("/recovery/cases", params={"state": "diagnosing"})).json()
    assert len(diagnosing) == 1
    assert diagnosing[0]["id"] == c1["id"]


async def test_case_detail_history_is_chronological(client: AsyncClient) -> None:
    payment_id = await _ingest_payment(client, idempotency_key="e1", external_reference="p1")
    case = await _open_case(client, payment_id)

    for to_state in ("diagnosing", "diagnosed", "decision_pending"):
        r = await client.post(
            f"/recovery/cases/{case['id']}/transitions", json={"to_state": to_state}
        )
        assert r.status_code == 200

    detail = (await client.get(f"/recovery/cases/{case['id']}")).json()
    assert [h["to_state"] for h in detail["history"]] == [
        "detected",
        "diagnosing",
        "diagnosed",
        "decision_pending",
    ]
    timestamps = [h["created_at"] for h in detail["history"]]
    assert timestamps == sorted(timestamps)
