"""Tools for the eval watchdog: notice model changes (new versions, silent updates), re-run evals, compare
against baselines, report regressions.

Change detection has two signals:

* the provider's model listing (`GET {base_url}/models` for OpenAI-compatible endpoints such as Nutanix
  Enterprise AI or vLLM; a snapshots file in mock mode), compared with the last listing the watchdog saw;
* a behavioural fingerprint per model: the hash of its answers to fixed canary prompts at temperature 0.
  Same name, different fingerprint = a silent update.

Baselines and accepted fingerprints live in the data directory; until one exists there, the copies shipped
under `examples/eval_watchdog/` are used.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from langchain_core.messages import HumanMessage

from nai_agents.core.data import load_json_state, read_text, resolve_input, save_json_state, to_json
from nai_agents.core.evals import EvalReport, compare_reports, load_suite, report_to_dict, run_suite
from nai_agents.core.models import CatalogModel, build_candidate, load_catalog
from nai_agents.core.settings import get_settings

SNAPSHOTS = "eval_watchdog/model_snapshots.json"
CANARIES = "eval_watchdog/canaries.yaml"
DEFAULT_SUITE = "evals/general.yaml"
KNOWN_FILE = "eval_watchdog/known_models.json"
FINGERPRINTS_FILE = "eval_watchdog/fingerprints.json"
REPORT_FILE = "/watchdog-report.md"
TOLERANCE = 0.05


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)


def _snapshots() -> dict[str, Any]:
    return json.loads(read_text(SNAPSHOTS))


def _mock_mode() -> bool:
    return get_settings().llm_provider == "mock"


# ---- listing ---------------------------------------------------------------------------------------


def _api_listing() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.llm_base_url:
        raise ValueError("LLM_BASE_URL is not set; can't list the provider's models")
    url = settings.llm_base_url.rstrip("/") + "/models"
    if not url.startswith(("http://", "https://")):
        raise ValueError("Only http(s) model endpoints are supported")
    headers = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"
    resp = httpx.get(url, headers=headers, timeout=min(settings.llm_timeout_s, 30.0))
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data", data) if isinstance(data, dict) else data
    return [
        {"id": m["id"], **{k: v for k, v in m.items() if k in {"created", "owned_by"}}}
        for m in items
        if isinstance(m, dict) and "id" in m
    ]


def _listing() -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """(source, current listing, previous listing from the snapshots file)."""
    if _mock_mode() or get_settings().llm_provider not in {"openai_compatible", "openai"}:
        snaps = _snapshots()["snapshots"]
        return "snapshot", snaps[-1]["models"], snaps[-2]["models"] if len(snaps) > 1 else []
    return "api", _api_listing(), []


def list_provider_models() -> str:
    """The provider's current model listing: `GET {LLM_BASE_URL}/models` for OpenAI-compatible endpoints
    (Nutanix Enterprise AI, vLLM, ...), or the latest snapshot in examples/eval_watchdog in mock mode."""
    source, current, _ = _listing()
    return to_json({"source": source, "count": len(current), "models": current})


def _known(previous: list[dict[str, Any]]) -> list[str]:
    known = load_json_state(KNOWN_FILE, None)
    return known if known is not None else [m["id"] for m in previous]


def detect_model_changes() -> str:
    """Compare the provider's listing with the last one the watchdog recorded: models added (new versions)
    and removed. Also flags catalog models the provider no longer lists."""
    source, current, previous = _listing()
    known = _known(previous)
    ids = [m["id"] for m in current]
    added = [m for m in current if m["id"] not in known]
    removed = [k for k in known if k not in ids]
    catalog_missing = [m.name for m in load_catalog() if m.model not in ids and m.name not in ids]
    return to_json(
        {
            "source": source,
            "known": len(known),
            "listed": len(ids),
            "added": added,
            "removed": removed,
            "catalog_models_not_listed": catalog_missing,
        }
    )


# ---- entries, fingerprints -----------------------------------------------------------------------


def _entry(name: str) -> CatalogModel:
    """The model as the provider serves it now. Mock mode applies the snapshot's silent updates and builds
    entries for newly listed models."""
    catalog = {m.name: m for m in load_catalog()}
    by_model = {m.model: m for m in catalog.values()}
    entry = catalog.get(name) or by_model.get(name)
    if _mock_mode():
        snaps = _snapshots()
        if entry is None:
            listed = next((m for m in snaps["snapshots"][-1]["models"] if m["id"] == name), None)
            if listed is None:
                raise KeyError(f"{name!r} is neither in the catalog nor in the provider listing")
            fields = {f.name for f in dataclasses.fields(CatalogModel)}
            entry = CatalogModel(
                name=name,
                provider="mock",
                model=name,
                **{k: v for k, v in listed.items() if k in fields and k not in {"name", "model"}},
            )
        update = snaps.get("silent_updates", {}).get(entry.name)
        if update:
            entry = dataclasses.replace(entry, **{k: v for k, v in update.items() if k.startswith("sim_")})
        return entry
    if entry is None:
        settings = get_settings()
        entry = CatalogModel(
            name=name, provider=settings.llm_provider, model=name, base_url=settings.llm_base_url
        )
    return entry


def _accepted_fingerprints() -> dict[str, Any]:
    shipped_path = get_settings().examples_dir / "eval_watchdog" / "fingerprints.json"
    shipped = json.loads(shipped_path.read_text(encoding="utf-8")) if shipped_path.exists() else {}
    return {**shipped, **load_json_state(FINGERPRINTS_FILE, {})}


def compute_fingerprint(entry: CatalogModel) -> dict[str, Any]:
    canaries = load_suite(resolve_input(CANARIES))
    model = build_candidate(entry, answers=canaries.answers, difficulty=canaries.difficulty)
    outputs = []
    for case in canaries.cases:
        try:
            msg = model.invoke([HumanMessage(content=case.prompt)])
            text = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        except Exception as exc:  # an unreachable model has no fingerprint, but must not crash the run
            return {"fingerprint": None, "error": f"{type(exc).__name__}: {exc}"[:300]}
        outputs.append(f"{case.id}\x1f{text.strip()}")
    digest = hashlib.sha256("\x1e".join(outputs).encode()).hexdigest()[:16]
    return {"fingerprint": digest, "canaries": len(outputs)}


def fingerprint_models(models: str = "") -> str:
    """Fingerprint models (comma-separated; default: every catalog model) by hashing their answers to the
    canary prompts at temperature 0, and compare with the accepted fingerprint. A changed fingerprint under
    the same name is a silent update."""
    names = [m.strip() for m in models.split(",") if m.strip()] or [m.name for m in load_catalog()]
    accepted = _accepted_fingerprints()
    rows = []
    for name in names:
        fp = compute_fingerprint(_entry(name))
        prev = accepted.get(name, {}).get("fingerprint")
        rows.append(
            {
                "model": name,
                **fp,
                "accepted": prev,
                "changed": bool(prev and fp.get("fingerprint") and fp["fingerprint"] != prev),
                "first_seen": prev is None,
            }
        )
    history = load_json_state("eval_watchdog/fingerprint_history.json", [])
    history.append({"at": _now(), "rows": rows})
    save_json_state("eval_watchdog/fingerprint_history.json", history[-200:])
    return to_json({"changed": [r["model"] for r in rows if r["changed"]], "models": rows})


# ---- evals -----------------------------------------------------------------------------------------


def _baseline(name: str) -> dict[str, Any] | None:
    own = load_json_state(f"eval_watchdog/baselines/{_safe(name)}.json", None)
    if own is not None:
        return own
    shipped = get_settings().examples_dir / "eval_watchdog" / "baselines" / f"{_safe(name)}.json"
    return json.loads(shipped.read_text(encoding="utf-8")) if shipped.exists() else None


def run_watch_evals(models: str, suite_path: str = DEFAULT_SUITE) -> str:
    """Re-run the eval suite on the given models (comma-separated) and compare each with its baseline.

    Returns pass rate, delta vs. baseline, newly failing and newly passing case ids, and `regressed`
    (overall drop over 5 points, or a category drop over 10). Models without a baseline are reported as new.
    """
    names = [m.strip() for m in models.split(",") if m.strip()]
    if not names:
        raise ValueError("Name at least one model")
    suite = load_suite(resolve_input(suite_path))
    rows = []
    for name in names:
        entry = _entry(name)
        report: EvalReport = run_suite(entry, suite)
        save_json_state(
            f"eval_watchdog/last_runs/{_safe(name)}.json",
            {
                **report_to_dict(report),
                "suite_path": suite_path,
                "sim_version": entry.sim_version,
                "at": _now(),
            },
        )
        base = _baseline(name)
        if base is None:
            rows.append(
                {
                    "model": name,
                    "pass_rate": report.pass_rate,
                    "has_baseline": False,
                    "regressed": False,
                    "failed_cases": [r.id for r in report.results if not r.passed],
                    "note": "new model: no baseline yet",
                }
            )
            continue
        cmp = compare_reports(base, report, tolerance=TOLERANCE)
        rows.append({**cmp, "has_baseline": True, "baseline_at": base.get("at")})
    result = {
        "suite": suite.name,
        "checked_at": _now(),
        "results": rows,
        "regressions": [r["model"] for r in rows if r["regressed"]],
    }
    save_json_state("eval_watchdog/last_check.json", result)
    return to_json(result)


def explain_regression(model_name: str) -> str:
    """Case-by-case comparison of a model's latest eval run with its baseline: every case that flipped,
    with the category and both outputs, so you can tell what kind of capability changed."""
    run = load_json_state(f"eval_watchdog/last_runs/{_safe(model_name)}.json", None)
    base = _baseline(model_name)
    if run is None or base is None:
        raise ValueError(f"Need both a baseline and a run for {model_name!r}; call run_watch_evals first")
    before = {r["id"]: r for r in base.get("results", [])}
    flips = []
    for r in run["results"]:
        b = before.get(r["id"])
        if b and b["passed"] != r["passed"]:
            flips.append(
                {
                    "case": r["id"],
                    "category": r["category"],
                    "change": "now failing" if b["passed"] else "now passing",
                    "baseline_output": b["output"][:200],
                    "current_output": r["output"][:200],
                }
            )
    by_cat: dict[str, int] = {}
    for f in flips:
        if f["change"] == "now failing":
            by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
    return to_json(
        {
            "model": model_name,
            "baseline_version": base.get("sim_version"),
            "current_version": run.get("sim_version"),
            "baseline_pass_rate": base.get("pass_rate"),
            "current_pass_rate": run["pass_rate"],
            "newly_failing_by_category": by_cat,
            "flips": flips,
        }
    )


def save_baseline(model_name: str) -> str:
    """Accept a model's latest eval run as its new baseline, record its current fingerprint and mark it as a
    known model. Needs approval: after this, today's behaviour is what future runs are compared with."""
    run = load_json_state(f"eval_watchdog/last_runs/{_safe(model_name)}.json", None)
    if run is None:
        raise ValueError(f"No eval run for {model_name!r} yet; call run_watch_evals first")
    save_json_state(f"eval_watchdog/baselines/{_safe(model_name)}.json", run)
    fp = compute_fingerprint(_entry(model_name))
    stored = load_json_state(FINGERPRINTS_FILE, {})
    stored[model_name] = {**fp, "at": _now()}
    save_json_state(FINGERPRINTS_FILE, stored)
    _, _, previous = _listing()
    known = _known(previous)
    if model_name not in known:
        known.append(model_name)
        save_json_state(KNOWN_FILE, known)
    return to_json(
        {
            "status": "saved",
            "model": model_name,
            "pass_rate": run["pass_rate"],
            "fingerprint": fp.get("fingerprint"),
            "baseline_file": f"baselines/{_safe(model_name)}.json",
        }
    )


def write_shipped_baselines(out_dir: Path, suite_path: str = DEFAULT_SUITE) -> None:
    """Regenerate examples/eval_watchdog/{baselines,fingerprints.json} from the catalog as it is (no silent
    updates applied). Run by hand: `python -c "from nai_agents.agents.eval_watchdog.tools import *;
    write_shipped_baselines(Path('examples/eval_watchdog'))"`."""
    suite = load_suite(resolve_input(suite_path))
    fps = {}
    for entry in load_catalog():
        report = run_suite(entry, suite)
        (out_dir / "baselines").mkdir(parents=True, exist_ok=True)
        (out_dir / "baselines" / f"{_safe(entry.name)}.json").write_text(
            json.dumps(
                {
                    **report_to_dict(report),
                    "suite_path": suite_path,
                    "sim_version": entry.sim_version,
                    "at": "2026-10-13T06:30:00+00:00",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        fps[entry.name] = {**compute_fingerprint(entry), "at": "2026-10-13T06:30:00+00:00"}
    (out_dir / "fingerprints.json").write_text(json.dumps(fps, indent=2) + "\n", encoding="utf-8")


TOOLS = [
    list_provider_models,
    detect_model_changes,
    fingerprint_models,
    run_watch_evals,
    explain_regression,
    save_baseline,
]
