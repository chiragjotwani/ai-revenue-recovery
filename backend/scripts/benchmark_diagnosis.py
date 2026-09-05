"""Benchmark a reasoning-model provider on the diagnosis evaluation set
(Section 52).

Usage:
    python scripts/benchmark_diagnosis.py --provider mock
    python scripts/benchmark_diagnosis.py --provider qwen      # needs AI_QWEN_BASE_URL
    python scripts/benchmark_diagnosis.py --provider nemotron
    python scripts/benchmark_diagnosis.py --compare mock,qwen,nemotron  # Phase 10: side by side

Measures, over ``evaluation/diagnosis_cases.json``:
  - outcome accuracy      (predicted outcome == expected label)
  - schema-compliance rate (model output validated without error)
  - hallucination rate     (asserted a specific cause when the label is UNKNOWN)
  - confidence-band adherence (confidence within the case's [min, max])
  - mean / p95 latency, throughput

Memory/VRAM (also required by Section 52) is measured out of process
(e.g. ``nvidia-smi``) and is not collected here.

The evaluation set is fixed. Never edit it to improve a score (KI-007);
the numbers are agreement with synthetic labels, not real-world accuracy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ai.context_builder import RecoveryContext
from app.ai.diagnosis import DiagnosisValidationError, run_diagnosis
from app.ai.prompts import DIAGNOSIS_PROMPT_VERSION
from app.ai.providers.base import ReasoningModel, ReasoningModelError
from app.ai.providers.factory import get_reasoning_model
from app.ai.schema import DiagnosisOutcome
from app.core.config import get_settings

_DATASET = Path(__file__).resolve().parent.parent / "evaluation" / "diagnosis_cases.json"


def _build_provider(name: str) -> ReasoningModel:
    settings = get_settings()
    settings = settings.model_copy(update={"reasoning_provider": name})
    return get_reasoning_model(settings)


async def _run(provider: ReasoningModel, cases: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(cases)
    correct = 0
    schema_ok = 0
    latencies: list[float] = []
    confidence_band_ok = 0
    unknown_labels = 0
    hallucinations = 0
    per_case: list[dict[str, Any]] = []

    for case in cases:
        context = RecoveryContext.model_validate(case["context"])
        expect_unknown = bool(case["expect_unknown"])
        expected = case["expected_outcome"]
        if expect_unknown:
            unknown_labels += 1

        started = time.perf_counter()
        try:
            diagnosis, raw = await run_diagnosis(
                context, provider, prompt_version=DIAGNOSIS_PROMPT_VERSION
            )
        except (DiagnosisValidationError, ReasoningModelError) as exc:
            latencies.append((time.perf_counter() - started) * 1000)
            per_case.append({"id": case["id"], "error": str(exc)})
            continue

        latencies.append(raw.latency_ms or (time.perf_counter() - started) * 1000)
        schema_ok += 1
        predicted = diagnosis.outcome.value
        is_correct = predicted == expected
        correct += int(is_correct)

        if expect_unknown and diagnosis.outcome is not DiagnosisOutcome.UNKNOWN:
            hallucinations += 1

        if case["min_confidence"] <= diagnosis.confidence <= case["max_confidence"]:
            confidence_band_ok += 1

        per_case.append(
            {
                "id": case["id"],
                "expected": expected,
                "predicted": predicted,
                "correct": is_correct,
                "confidence": diagnosis.confidence,
            }
        )

    total_seconds = sum(latencies) / 1000 or 1e-9
    return {
        "provider": provider.name,
        "prompt_version": DIAGNOSIS_PROMPT_VERSION,
        "dataset": _DATASET.name,
        "n": n,
        "outcome_accuracy": round(correct / n, 4),
        "schema_compliance_rate": round(schema_ok / n, 4),
        "hallucination_rate": (
            round(hallucinations / unknown_labels, 4) if unknown_labels else 0.0
        ),
        "confidence_band_adherence": round(confidence_band_ok / n, 4),
        "mean_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "p95_latency_ms": (
            round(statistics.quantiles(latencies, n=20)[-1], 2) if len(latencies) >= 20 else None
        ),
        "throughput_per_s": round(n / total_seconds, 2),
        "generated_at": datetime.now(UTC).isoformat(),
        "per_case": per_case,
    }


_SUMMARY_KEYS = [
    "provider",
    "n",
    "outcome_accuracy",
    "schema_compliance_rate",
    "hallucination_rate",
    "confidence_band_adherence",
    "mean_latency_ms",
    "p95_latency_ms",
    "throughput_per_s",
]


def _print_summary(result: dict[str, Any]) -> None:
    width = max(len(k) for k in _SUMMARY_KEYS)
    print("\nDiagnosis benchmark")
    print("-" * (width + 20))
    for k in _SUMMARY_KEYS:
        print(f"{k.ljust(width)} : {result[k]}")
    print("-" * (width + 20))


_COMPARE_KEYS = ["requested_provider", *_SUMMARY_KEYS]


def _print_comparison(results: list[dict[str, Any]]) -> None:
    """Side-by-side comparison across providers (Phase 10, Section 29 --
    "model comparison"). A provider that errored on every case (e.g. not
    configured, or its endpoint unreachable) still gets a row -- its
    metrics are honestly 0.0/None, never omitted or backfilled, so a
    missing/unreachable provider is visibly different from a working one
    that happens to score low. ``requested_provider`` vs. ``provider``
    makes a config-time substitution (e.g. --compare qwen with no
    AI_QWEN_BASE_URL set) visible in the table itself, not hidden.
    """
    col = max(len(k) for k in _COMPARE_KEYS) + 2
    print("\nModel comparison (Phase 10)")
    header = "".join(k.ljust(col) for k in _COMPARE_KEYS)
    print(header)
    print("-" * len(header))
    for result in results:
        print("".join(str(result[k]).ljust(col) for k in _COMPARE_KEYS))


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default=None, help="mock | qwen | nemotron")
    parser.add_argument(
        "--compare",
        default=None,
        help="comma-separated provider list to run and compare side by side, "
        "e.g. --compare mock,qwen,nemotron. Overrides --provider.",
    )
    parser.add_argument("--dataset", default=str(_DATASET))
    parser.add_argument("--out", default=None, help="path to write the full JSON result")
    args = parser.parse_args()

    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))

    if args.compare:
        provider_names = [p.strip() for p in args.compare.split(",") if p.strip()]
        results = []
        for name in provider_names:
            provider = _build_provider(name)
            result = await _run(provider, cases)
            # `provider` in the result is the ACTUAL serving provider
            # (._run sets it from provider.name) -- if it differs from
            # `name`, that itself is the same observable substitution
            # app.ai.providers.factory.select_reasoning_model reports
            # (e.g. --compare qwen with no AI_QWEN_BASE_URL set actually
            # ran against mock). Never overwritten to hide that.
            result["requested_provider"] = name
            results.append(result)
        _print_comparison(results)
        stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        out = Path(args.out or f"benchmark_comparison_{'-'.join(provider_names)}_{stamp}.json")
        out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"\nfull result: {out}")
        return

    provider_name = args.provider or get_settings().reasoning_provider
    provider = _build_provider(provider_name)

    result = await _run(provider, cases)
    _print_summary(result)

    out = Path(
        args.out or f"benchmark_results_{provider.name}_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    )
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"\nfull result: {out}")


if __name__ == "__main__":
    asyncio.run(_main())
