"""A small, dependency-free eval harness shared by the router, watchdog, scout and reviewer.

A suite is YAML or JSON:

```yaml
name: support-tasks
threshold: 0.8          # pass rate a model needs to be "good enough"
cases:
  - id: refund-policy
    category: extraction
    prompt: "What is 17 * 23? Answer with the number only."
    expected: "391"
    grader: exact        # exact | contains | regex | number | json_key | any_of
```
"""

from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from nai_agents.core.models import CatalogModel, build_candidate


@dataclass
class EvalCase:
    id: str
    prompt: str
    expected: str
    grader: str = "contains"
    category: str = "general"
    weight: float = 1.0
    difficulty: float = 0.0  # only used by simulated (mock) candidates
    key: str | None = None  # for json_key
    options: list[str] = field(default_factory=list)  # for any_of


@dataclass
class EvalSuite:
    name: str
    cases: list[EvalCase]
    threshold: float = 0.8
    description: str = ""

    @property
    def answers(self) -> dict[str, str]:
        return {c.prompt.strip(): c.expected for c in self.cases}

    @property
    def difficulty(self) -> dict[str, float]:
        return {c.prompt.strip(): c.difficulty for c in self.cases}

    def categories(self) -> list[str]:
        return sorted({c.category for c in self.cases})

    def subset(self, category: str | None) -> EvalSuite:
        if not category:
            return self
        cases = [c for c in self.cases if c.category == category]
        if not cases:
            raise ValueError(f"No cases in category {category!r}; categories: {', '.join(self.categories())}")
        return EvalSuite(f"{self.name}:{category}", cases, self.threshold, self.description)


@dataclass
class CaseResult:
    id: str
    category: str
    passed: bool
    output: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class EvalReport:
    suite: str
    model: str
    pass_rate: float
    passed: int
    total: int
    p50_latency_ms: float
    p95_latency_ms: float
    cost_usd: float
    by_category: dict[str, float]
    results: list[CaseResult]
    meets_threshold: bool

    def summary(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("results")
        d["failed_cases"] = [r.id for r in self.results if not r.passed]
        return d


def load_suite(path: str | Path) -> EvalSuite:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
    cases = [EvalCase(**{k: v for k, v in c.items() if k in EvalCase.__dataclass_fields__})
             for c in data.get("cases", [])]
    return EvalSuite(name=data.get("name", path.stem), cases=cases, threshold=float(data.get("threshold", 0.8)),
                     description=data.get("description", ""))


def grade(case: EvalCase, output: str) -> bool:
    out = output.strip()
    exp = str(case.expected).strip()
    g = case.grader
    if g == "exact":
        return out.lower().rstrip(".") == exp.lower().rstrip(".")
    if g == "contains":
        return exp.lower() in out.lower()
    if g == "regex":
        return re.search(exp, out, flags=re.IGNORECASE | re.MULTILINE) is not None
    if g == "number":
        nums = re.findall(r"-?\d+(?:\.\d+)?", out.replace(",", ""))
        try:
            return any(abs(float(n) - float(exp)) < 1e-6 for n in nums)
        except ValueError:
            return False
    if g == "json_key":
        try:
            match = re.search(r"\{.*\}", out, flags=re.DOTALL)
            obj = json.loads(match.group(0) if match else out)
            return str(obj.get(case.key or "answer", "")).strip().lower() == exp.lower()
        except (json.JSONDecodeError, AttributeError):
            return False
    if g == "any_of":
        return any(opt.lower() in out.lower() for opt in (case.options or [exp]))
    raise ValueError(f"Unknown grader {g!r}")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


def run_suite(entry: CatalogModel, suite: EvalSuite, model: BaseChatModel | None = None) -> EvalReport:
    """Run every case once against a catalog model and score it."""
    model = model or build_candidate(entry, answers=suite.answers, difficulty=suite.difficulty)
    results: list[CaseResult] = []
    for case in suite.cases:
        start = time.perf_counter()
        try:
            msg = model.invoke([HumanMessage(content=case.prompt)])
            text = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
            usage = getattr(msg, "usage_metadata", None) or {}
            simulated = (getattr(msg, "response_metadata", None) or {}).get("simulated_latency_ms")
        except Exception as exc:  # a failing endpoint is a failed case, not a crashed run
            text, usage, simulated = f"ERROR: {exc}", {}, None
        latency = simulated if simulated is not None else (time.perf_counter() - start) * 1000
        tin = int(usage.get("input_tokens") or len(case.prompt) // 4 + 1)
        tout = int(usage.get("output_tokens") or len(text) // 4 + 1)
        results.append(CaseResult(case.id, case.category, grade(case, text), text[:500], round(latency, 1),
                                  tin, tout, entry.cost(tin, tout)))
    passed = sum(1 for r in results if r.passed)
    total = len(results) or 1
    by_cat: dict[str, list[bool]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r.passed)
    latencies = [r.latency_ms for r in results]
    pass_rate = passed / total
    return EvalReport(
        suite=suite.name, model=entry.name, pass_rate=round(pass_rate, 4), passed=passed, total=len(results),
        p50_latency_ms=round(statistics.median(latencies), 1) if latencies else 0.0,
        p95_latency_ms=round(_percentile(latencies, 95), 1),
        cost_usd=round(sum(r.cost_usd for r in results), 6),
        by_category={k: round(sum(v) / len(v), 4) for k, v in by_cat.items()},
        results=results, meets_threshold=pass_rate >= suite.threshold,
    )


def compare_reports(baseline: dict[str, Any], current: EvalReport, tolerance: float = 0.05) -> dict[str, Any]:
    """Regression check between a saved baseline summary (with per-case results) and a fresh run."""
    base_cases = {c["id"]: c["passed"] for c in baseline.get("results", [])}
    newly_failing = [r.id for r in current.results if base_cases.get(r.id) and not r.passed]
    newly_passing = [r.id for r in current.results if base_cases.get(r.id) is False and r.passed]
    delta = round(current.pass_rate - float(baseline.get("pass_rate", 0.0)), 4)
    cat_deltas = {
        cat: round(rate - float(baseline.get("by_category", {}).get(cat, rate)), 4)
        for cat, rate in current.by_category.items()
    }
    regressed = delta < -tolerance or any(d < -tolerance * 2 for d in cat_deltas.values())
    return {
        "model": current.model, "baseline_pass_rate": baseline.get("pass_rate"),
        "current_pass_rate": current.pass_rate, "delta": delta, "category_deltas": cat_deltas,
        "newly_failing": newly_failing, "newly_passing": newly_passing, "regressed": regressed,
        "latency_delta_ms": round(current.p50_latency_ms - float(baseline.get("p50_latency_ms", 0.0)), 1),
    }


def report_to_dict(report: EvalReport) -> dict[str, Any]:
    return asdict(report)
