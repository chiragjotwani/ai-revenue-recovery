"""Real, recorded model usage reporting (Phase 10, Section 29 of the
master plan -- "model comparison" / "latency monitoring" / "model
evaluation", the read-only half; the offline half is
``scripts/benchmark_diagnosis.py --compare``).

Every number here comes from actually-persisted ``Diagnosis`` rows --
never from the synthetic evaluation set
(``backend/evaluation/diagnosis_cases.json``), which stays governed by
KI-007 and is never mixed into this report.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.factory import select_reasoning_model
from app.models.diagnosis import Diagnosis
from app.schemas.ai import ModelReportEntry, ModelReportOut, ProviderStatusOut


async def get_model_report(session: AsyncSession) -> ModelReportOut:
    selection = select_reasoning_model()
    router = ProviderStatusOut(
        requested_provider=selection.requested_provider,
        resolved_provider=selection.resolved_provider,
        substituted=selection.substituted,
        substitution_reason=selection.substitution_reason,
    )

    rows = list((await session.scalars(select(Diagnosis))).all())

    by_model: dict[str, list[Diagnosis]] = defaultdict(list)
    for row in rows:
        by_model[row.model_name].append(row)

    entries = [
        ModelReportEntry(
            model_name=name,
            diagnosis_count=len(diagnoses),
            mean_latency_ms=round(sum(d.latency_ms for d in diagnoses) / len(diagnoses), 2),
            mean_confidence=round(float(sum(d.confidence for d in diagnoses)) / len(diagnoses), 4),
            escalation_count=sum(1 for d in diagnoses if d.router_escalated),
        )
        for name, diagnoses in by_model.items()
    ]
    entries.sort(key=lambda e: e.model_name)

    return ModelReportOut(router=router, by_model=entries)
