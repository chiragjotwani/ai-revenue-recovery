"""API-facing schemas for the Phase 10 model router / model report.

``ModelReportEntry`` aggregates only REAL recorded ``Diagnosis`` rows
(``latency_ms``, ``confidence`` as already persisted by Phase 4) --
never synthetic evaluation data (that stays in
``backend/evaluation/diagnosis_cases.json`` / ``scripts/benchmark_diagnosis.py``,
governed by KI-007, and is never mixed into this production-data report).
"""

from __future__ import annotations

from pydantic import BaseModel


class ProviderStatusOut(BaseModel):
    """The model router's current resolution -- what was requested, what
    was actually resolved, and why they differ (if they do). Mirrors
    ``app.ai.providers.factory.ProviderSelection`` without exposing the
    live ``ReasoningModel`` instance.
    """

    model_config = {"extra": "forbid"}

    requested_provider: str
    resolved_provider: str
    substituted: bool
    substitution_reason: str | None


class ModelReportEntry(BaseModel):
    """Real, recorded usage for one ``model_name`` that has actually
    produced at least one persisted diagnosis. Not a benchmark, not a
    prediction -- a summary of what actually happened.
    """

    model_config = {"extra": "forbid"}

    model_name: str
    diagnosis_count: int
    mean_latency_ms: float
    mean_confidence: float
    escalation_count: int


class ModelReportOut(BaseModel):
    """The full Phase 10 model report: current router status plus real
    recorded per-model usage. ``escalation_count`` on each entry is the
    number of diagnoses whose ``reasoning`` carries the router's own
    safeguard-style escalation marker (see ``app.ai.report``) -- a
    transport-failure fallback, never a confidence-based one.
    """

    model_config = {"extra": "forbid"}

    router: ProviderStatusOut
    by_model: list[ModelReportEntry]
