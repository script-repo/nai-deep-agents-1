"""Tools for the new-model release scout: read release feeds, find unseen model launches, benchmark them,
compare with production, recommend.

Feeds are RSS 2.0, Atom, or simple HTML listing pages (Hugging Face-style model lists). In mock mode every
source is read from its local sample file; otherwise its URL is fetched with httpx (http/https only, with a
timeout and a size cap). Feeds containing XML entity declarations are refused.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

import httpx
import yaml
from langchain_core.messages import HumanMessage

from nai_agents.core.data import load_json_state, read_text, resolve_input, save_json_state, to_json
from nai_agents.core.evals import load_suite, run_suite
from nai_agents.core.mock import stable_fraction
from nai_agents.core.models import CatalogModel, build_candidate, find_model
from nai_agents.core.settings import get_settings

SOURCES = "release_scout/sources.yaml"
SEEN_FILE = "release_scout/seen.json"
CANDIDATES_FILE = "release_scout/candidates.json"
REPORT_FILE = "/scout-report.md"
MAX_FEED_BYTES = 2_000_000
FETCH_TIMEOUT_S = 15.0
ATOM = "{http://www.w3.org/2005/Atom}"

RELEASE_WORDS = re.compile(
    r"\b(introducing|now available|launch(?:es|ing)?|releas(?:e|es|ing)|announcing|meet)\b", re.I
)
NOT_A_MODEL = re.compile(
    r"\b(recap|policy|how we|why we|update to|webinar|hiring|case study|red-team)\b", re.I
)
VARIANT = re.compile(r"-(GGUF|AWQ|GPTQ|MLX|EXL2|FP8|INT4|INT8|bnb-4bit)$", re.I)


# ---- sources -----------------------------------------------------------------------------------


def _config() -> dict[str, Any]:
    return yaml.safe_load(read_text(SOURCES)) or {}


def list_sources() -> str:
    """The release feeds the scout watches (name, provider, type, URL), the production model and the
    switch policy."""
    cfg = _config()
    return to_json(
        {
            "sources": cfg.get("sources", []),
            "production": cfg.get("production"),
            "policy": cfg.get("policy"),
            "mode": "mock (local samples)" if _mock() else "live",
        }
    )


def _mock() -> bool:
    return get_settings().llm_provider == "mock"


def _fetch(src: dict[str, Any]) -> str:
    if _mock() or not src.get("url"):
        return read_text(f"release_scout/{src['sample']}")
    url = str(src["url"])
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Only http(s) feeds are allowed: {url!r}")
    with httpx.stream(
        "GET",
        url,
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": "nai-release-scout/0.1"},
    ) as resp:
        resp.raise_for_status()
        if not str(resp.url).startswith(("http://", "https://")):
            raise ValueError("Redirected to a non-http(s) URL")
        chunks, size = [], 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > MAX_FEED_BYTES:
                raise ValueError(f"{url} is larger than {MAX_FEED_BYTES} bytes")
            chunks.append(chunk)
    return b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")


# ---- parsing -----------------------------------------------------------------------------------


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value[:10]


def _xml(text: str) -> ET.Element:
    if re.search(r"<!ENTITY", text, re.I):
        raise ValueError("Feed declares XML entities; refused")
    return ET.fromstring(text.encode("utf-8") if text.lstrip().startswith("<?xml") else text)


def parse_rss(text: str) -> list[dict[str, Any]]:
    root = _xml(text)
    return [
        {
            "title": (i.findtext("title") or "").strip(),
            "link": (i.findtext("link") or "").strip(),
            "date": _iso(i.findtext("pubDate")),
            "summary": (i.findtext("description") or "").strip(),
        }
        for i in root.iter("item")
    ]


def parse_atom(text: str) -> list[dict[str, Any]]:
    root = _xml(text)
    items = []
    for e in root.iter(f"{ATOM}entry"):
        link = e.find(f"{ATOM}link")
        items.append(
            {
                "title": (e.findtext(f"{ATOM}title") or "").strip(),
                "link": link.get("href", "") if link is not None else "",
                "date": _iso(e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated")),
                "summary": (e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content") or "").strip(),
            }
        )
    return items


class _ListingParser(HTMLParser):
    """Model cards on a Hugging Face-style listing: <a href="/org/name"> ... <time datetime=...> ... </a>."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        href = a.get("href") or ""
        if tag == "a" and re.fullmatch(r"/[\w.-]+/[\w.-]+", href):
            self._current = {"title": href.strip("/"), "link": href, "date": None, "text": ""}
        elif tag == "time" and self._current is not None:
            self._current["date"] = _iso(a.get("datetime"))

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current is not None:
            text = re.sub(r"\s+", " ", self._current.pop("text")).strip()
            self.items.append({**self._current, "summary": text})
            self._current = None


def parse_listing(text: str) -> list[dict[str, Any]]:
    parser = _ListingParser()
    parser.feed(text)
    return parser.items


def fetch_feeds(sources: str = "") -> str:
    """Fetch and parse the release feeds (comma-separated source names; default all). Returns every item
    with source, provider, title, link, date and summary, plus any per-source errors."""
    wanted = [s.strip() for s in sources.split(",") if s.strip()]
    items, errors = [], []
    for src in _config().get("sources", []):
        if wanted and src["name"] not in wanted:
            continue
        try:
            text = _fetch(src)
            parsed = {"rss": parse_rss, "atom": parse_atom, "html": parse_listing}[src["type"]](text)
        except Exception as exc:  # one broken feed must not hide the others
            errors.append({"source": src["name"], "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        for item in parsed:
            items.append({"source": src["name"], "provider": src["provider"], "type": src["type"], **item})
    return to_json({"count": len(items), "items": items, "errors": errors})


# ---- extraction --------------------------------------------------------------------------------


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", name.lower()).strip("-")


def _model_name(item: dict[str, Any]) -> str | None:
    title = item["title"]
    if item["type"] == "html":
        name = title.split("/")[-1]
        return None if VARIANT.search(name) else name
    if NOT_A_MODEL.search(title):
        return None
    m = (
        re.match(r"^(?:introducing|announcing|meet)\s+(.+?)(?:[:—–-]\s|$)", title, re.I)
        or re.match(r"^(.+?)\s+(?:is\s+)?(?:now available|launches|released)\b", title, re.I)
        or re.match(r"^([\w.-]+(?:[ -][\w.]+){0,2}):\s", title)
    )
    if not m:
        return None
    name = m.group(1).strip()
    if not re.search(r"\d", name) or len(name) > 40:
        return None  # model names carry a version
    if not (RELEASE_WORDS.search(title) or ":" in title):
        return None
    return name


def _context(text: str) -> int | None:
    m = re.search(r"context window of ([\d,.]+)\s*(k|K|M)?\s*tokens", text) or re.search(
        r"([\d,.]+)\s*(K|k|M)?(?:[- ]token)?\s+context", text
    )
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return int(value * (1000 if unit == "k" else 1_000_000 if unit == "m" else 1))


def _prices(text: str) -> tuple[float | None, float | None]:
    per = r"\s*(?:per|/)\s*(?:1M|1 M|million|M)"
    pin = re.search(r"\$([\d.]+)" + per + r"\s+input", text, re.I)
    pout = re.search(r"\$([\d.]+)" + per + r"\s+output", text, re.I)
    if pin and pout:
        return float(pin.group(1)), float(pout.group(1))
    both = re.search(r"\$([\d.]+)\s*/\s*\$([\d.]+)" + per + r"\s+input\s*/\s*output", text, re.I)
    if both:
        return float(both.group(1)), float(both.group(2))
    return None, None


def _seen() -> list[str]:
    shipped = resolve_input("release_scout/seen.json")
    base = yaml.safe_load(shipped.read_text(encoding="utf-8")) or []
    return list(dict.fromkeys([*base, *load_json_state(SEEN_FILE, [])]))


def find_new_models(sources: str = "") -> str:
    """Fetch the feeds, keep only model releases (not blog posts, not quantized re-uploads), drop the ones
    already seen, and extract metadata: provider, name, id, date, context window and price per Mtok when the
    post states them. The candidates are remembered for benchmark_model."""
    feed = __import__("json").loads(fetch_feeds(sources))
    seen = set(_seen())
    new, skipped = [], []
    for item in feed["items"]:
        name = _model_name(item)
        if not name:
            skipped.append({"title": item["title"], "why": "not a model release"})
            continue
        model_id = _slug(name)
        if model_id in seen:
            skipped.append({"title": item["title"], "why": "already seen"})
            continue
        text = f"{item['title']}. {item['summary']}"
        pin, pout = _prices(text)
        new.append(
            {
                "id": model_id,
                "name": name,
                "provider": item["provider"],
                "source": item["source"],
                "date": item["date"],
                "link": item["link"],
                "context_window": _context(text),
                "input_per_mtok": pin,
                "output_per_mtok": pout,
                "open_weights": item["type"] == "html",
            }
        )
        seen.add(model_id)
    save_json_state(CANDIDATES_FILE, new)
    return to_json({"new": new, "skipped": skipped, "errors": feed["errors"]})


# ---- benchmarking ------------------------------------------------------------------------------


def _candidate(model_id: str) -> dict[str, Any]:
    for c in load_json_state(CANDIDATES_FILE, []):
        if c["id"] == model_id or c["name"] == model_id:
            return c
    raise KeyError(f"{model_id!r} is not a known candidate; call find_new_models first")


def _priced(c: dict[str, Any], policy: dict[str, Any]) -> tuple[float, float, bool]:
    if c.get("input_per_mtok") is not None and c.get("output_per_mtok") is not None:
        return float(c["input_per_mtok"]), float(c["output_per_mtok"]), False
    return (
        float(policy.get("self_hosted_input_per_mtok", 0.1)),
        float(policy.get("self_hosted_output_per_mtok", 0.4)),
        True,
    )


def _sim_skill(c: dict[str, Any]) -> float:
    """Mock-mode quality for a candidate: grows with list price (or parameter count for open weights),
    plus a little deterministic per-model jitter. Only used for simulated benchmarks."""
    size = re.search(r"(\d+(?:\.\d+)?)\s*B\b", c["name"], re.I)
    if c.get("output_per_mtok") is not None:
        base = 0.75 + 0.1 * math.log2(1 + float(c["output_per_mtok"]))
    elif size:
        base = 0.70 + 0.08 * math.log2(float(size.group(1)))
    else:
        base = 0.8
    jitter = (stable_fraction("release-scout", c["id"]) - 0.5) * 0.08
    return round(min(1.15, max(0.4, base + jitter)), 3)


def benchmark_model(model_id: str, suite_path: str = "") -> str:
    """Benchmark one new candidate on the production eval suite.

    Mock mode registers it as a temporary simulated catalog model whose skill is derived deterministically
    from its name. With a real provider it is benchmarked only if the configured endpoint serves it (a
    one-line probe); otherwise it is reported as unreachable.
    """
    cfg = _config()
    c = _candidate(model_id)
    pin, pout, assumed = _priced(c, cfg.get("policy", {}))
    suite = load_suite(resolve_input(suite_path or cfg["production"].get("suite", "evals/general.yaml")))
    settings = get_settings()
    if _mock():
        entry = CatalogModel(
            name=c["id"],
            provider="mock",
            model=c["id"],
            input_per_mtok=pin,
            output_per_mtok=pout,
            context_window=c.get("context_window"),
            tags=["candidate"],
            sim_skill=_sim_skill(c),
            sim_latency_ms=300 + 900 * stable_fraction("latency", c["id"]),
        )
    else:
        entry = CatalogModel(
            name=c["id"],
            provider=settings.llm_provider,
            model=c["name"],
            base_url=settings.llm_base_url,
            input_per_mtok=pin,
            output_per_mtok=pout,
            context_window=c.get("context_window"),
        )
        try:
            build_candidate(entry).invoke([HumanMessage(content="Reply with OK.")])
        except Exception as exc:
            result = {
                "model": c["id"],
                "status": "unreachable",
                "error": f"{type(exc).__name__}: {exc}"[:300],
            }
            save_json_state(f"release_scout/benchmarks/{c['id']}.json", result)
            return to_json(result)
    report = run_suite(entry, suite)
    result = {
        "model": c["id"],
        "status": "benchmarked",
        "simulated": _mock(),
        "pass_rate": report.pass_rate,
        "by_category": report.by_category,
        "p50_latency_ms": report.p50_latency_ms,
        "meets_threshold": report.meets_threshold,
        "input_per_mtok": pin,
        "output_per_mtok": pout,
        "price_assumed": assumed,
        "failed_cases": [r.id for r in report.results if not r.passed],
    }
    save_json_state(f"release_scout/benchmarks/{c['id']}.json", result)
    return to_json(result)


def compare_to_production(models: str = "") -> str:
    """Compare benchmarked candidates (comma-separated; default all) with the production model on quality
    and monthly cost, and recommend `switch` or `hold` for each with reasons, using the policy in
    release_scout/sources.yaml."""
    cfg = _config()
    prod_cfg, policy = cfg["production"], cfg.get("policy", {})
    prod = find_model(prod_cfg["model"])
    suite = load_suite(resolve_input(prod_cfg.get("suite", "evals/general.yaml")))
    prod_report = run_suite(prod, suite)
    vol_in, vol_out = float(prod_cfg["monthly_input_mtok"]), float(prod_cfg["monthly_output_mtok"])
    prod_cost = vol_in * prod.input_per_mtok + vol_out * prod.output_per_mtok
    wanted = [m.strip() for m in models.split(",") if m.strip()] or [
        c["id"] for c in load_json_state(CANDIDATES_FILE, [])
    ]
    rows = []
    for mid in wanted:
        bench = load_json_state(f"release_scout/benchmarks/{mid}.json", None)
        if bench is None or bench.get("status") != "benchmarked":
            rows.append({"model": mid, "decision": "hold", "reasons": ["not benchmarked (or unreachable)"]})
            continue
        cost = vol_in * bench["input_per_mtok"] + vol_out * bench["output_per_mtok"]
        delta = round(bench["pass_rate"] - prod_report.pass_rate, 4)
        change_pct = round((cost / prod_cost - 1) * 100, 1) if prod_cost else 0.0
        reasons = []
        cheaper_ok = delta >= -float(policy.get("max_quality_drop", 0.02)) and -change_pct >= float(
            policy.get("min_savings_pct", 15)
        )
        better_ok = delta >= float(policy.get("min_quality_gain", 0.05)) and change_pct <= float(
            policy.get("max_cost_increase_pct", 25)
        )
        pts = f"{delta * 100:+.1f} pts"
        if cheaper_ok:
            reasons.append(f"{-change_pct:.0f}% cheaper than production at {pts} quality")
        elif better_ok:
            reasons.append(f"{pts} quality for {change_pct:+.0f}% cost")
        else:
            reasons.append(f"{pts} quality at {change_pct:+.0f}% cost: outside the switch policy")
        if not bench["meets_threshold"]:
            reasons.append("below the suite threshold")
        if bench.get("price_assumed"):
            reasons.append("price assumed (self-hosted estimate)")
        decision = "switch" if (cheaper_ok or better_ok) and bench["meets_threshold"] else "hold"
        rows.append(
            {
                "model": mid,
                "decision": decision,
                "pass_rate": bench["pass_rate"],
                "quality_delta": delta,
                "monthly_cost_usd": round(cost, 2),
                "cost_change_pct": change_pct,
                "reasons": reasons,
            }
        )
    switch = [r for r in rows if r["decision"] == "switch"]
    best = max(switch, key=lambda r: (r["quality_delta"], -r["monthly_cost_usd"]), default=None)
    return to_json(
        {
            "production": {
                "model": prod.name,
                "pass_rate": prod_report.pass_rate,
                "monthly_cost_usd": round(prod_cost, 2),
            },
            "candidates": rows,
            "recommendation": {"action": "switch", "model": best["model"], "why": best["reasons"]}
            if best
            else {
                "action": "hold",
                "model": prod.name,
                "why": ["no candidate beats production within the policy"],
            },
        }
    )


def mark_seen(model_ids: str) -> str:
    """Record models (comma-separated ids) as seen so future runs skip them. Needs approval."""
    ids = [m.strip() for m in model_ids.split(",") if m.strip()]
    if not ids:
        raise ValueError("Name at least one model id")
    stored = load_json_state(SEEN_FILE, [])
    added = [i for i in ids if i not in stored]
    save_json_state(SEEN_FILE, stored + added)
    return to_json({"status": "saved", "added": added, "seen_total": len(_seen())})


TOOLS = [list_sources, fetch_feeds, find_new_models, benchmark_model, compare_to_production, mark_seen]
