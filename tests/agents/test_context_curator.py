"""Agent 6: context and memory curator."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from _harness import assert_clean_run, interrupts, run_approved, run_rejected, tool_calls

from nai_agents.agents.context_curator import SPEC
from nai_agents.agents.context_curator import tools as t
from nai_agents.core.data import PathError
from nai_agents.core.settings import get_settings


def test_finds_context_files(data_dir: Path) -> None:
    data = json.loads(t.find_context_files())
    paths = {f["path"] for f in data["files"]}
    assert {"AGENTS.md", "CLAUDE.md", ".cursorrules", ".github/copilot-instructions.md"} <= paths
    assert {"memory/project.md", "memory/decisions.md"} <= paths
    assert "src/server.ts" not in paths
    assert data["total_tokens_est"] > 0


def test_detects_planted_contradictions(data_dir: Path) -> None:
    data = json.loads(t.analyze_context())
    by_topic = {c["topic"]: c for c in data["contradictions"]}
    pm = by_topic["Conflicting package manager"]
    assert set(pm["values"]) == {"npm", "pnpm"}
    assert pm["resolution"] == "pnpm" and pm["resolution_basis"] == "repo evidence"
    files = {s["file"] for s in pm["statements"]}
    assert {"AGENTS.md", "CLAUDE.md"} <= files
    node = by_topic["Conflicting node version"]
    assert set(node["values"]) == {"16", "18", "20"} and node["resolution"] == "20"
    assert by_topic["Conflicting test runner"]["resolution"] == "jest"
    rule = by_topic["'always' vs 'never': default exports"]
    assert rule["needs_human"] and rule["resolution"] == "never"
    # A dated decision log entry ("switched from npm to pnpm") is history, not a contradiction.
    assert all(s["file"] != "memory/decisions.md" for s in pm["statements"])


def test_detects_stale_context(data_dir: Path) -> None:
    data = json.loads(t.analyze_context())
    missing = {s["ref"] for s in data["stale"] if s["kind"] == "missing-path"}
    assert {"src/rules/discounts.ts", "docs/architecture.md", "src/legacy/payments.js"} <= missing
    # Paths that exist are not stale.
    assert not missing & {"src/server.ts", "src/db.ts", "src/api/", "scripts/build.sh", ".nvmrc"}
    kinds = {(s["file"], s["kind"]) for s in data["stale"]}
    assert ("AGENTS.md", "old-date") in kinds
    assert ("AGENTS.md", "old-version") in kinds
    assert ("CLAUDE.md", "todo") in kinds
    # "the old folder is gone" mentions a removed path on purpose: not stale.
    assert not any(s["file"] == "memory/decisions.md" and s["kind"] == "missing-path" for s in data["stale"])


def test_detects_duplicates_and_bloat(data_dir: Path) -> None:
    data = json.loads(t.analyze_context())
    dup = next(d for d in data["duplicates"] if d["text"] == "use typescript strict mode everywhere")
    assert dup["kept"] == "AGENTS.md:14" and len(dup["occurrences"]) == 4
    assert [b["file"] for b in data["bloat"]] == [".cursorrules"]


def test_propose_pruned_rewrites_and_removes(data_dir: Path) -> None:
    data = json.loads(t.propose_pruned("AGENTS.md"))
    assert "pnpm install" in data["pruned"] and "npm install`" not in data["pruned"].replace("pnpm", "")
    assert "Node 20" in data["pruned"] and "Node 16" not in data["pruned"]
    assert "src/rules/discounts.ts" not in data["pruned"]
    assert data["tokens_after"] < data["tokens_before"]
    claude = json.loads(t.propose_pruned("CLAUDE.md"))["pruned"]
    assert "Use TypeScript strict mode" not in claude  # lives in AGENTS.md already
    assert "never run npm install" in claude  # a prohibition is not rewritten to pnpm
    with pytest.raises(PathError):
        t.propose_pruned("src/server.ts")


def test_make_diff_writes_virtual_file(data_dir: Path) -> None:
    cmd = t.make_diff(tool_call_id="call_1")
    diff = cmd.update["files"][t.DIFF_FILE]["content"]
    assert "--- a/AGENTS.md" in diff and "+++ b/AGENTS.md" in diff
    assert "-- Always use npm to install dependencies (`npm install`)." in diff
    assert "+- Always use pnpm to install dependencies (`pnpm install`)." in diff
    summary = json.loads(cmd.update["messages"][0].content)
    assert summary["tokens_after"] < summary["tokens_before"]


def test_apply_changes_writes_a_copy_not_the_examples(data_dir: Path) -> None:
    original = (get_settings().examples_dir / t.DEFAULT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    result = json.loads(t.apply_changes())
    assert result["mode"] == "copy"
    target = Path(result["target"])
    assert data_dir.resolve() in target.parents
    assert "pnpm install" in (target / "AGENTS.md").read_text(encoding="utf-8")
    assert (get_settings().examples_dir / t.DEFAULT_ROOT / "AGENTS.md").read_text(
        encoding="utf-8"
    ) == original


def test_apply_changes_in_place_under_data_dir(data_dir: Path) -> None:
    repo = data_dir / "repos" / "mine"
    shutil.copytree(get_settings().examples_dir / t.DEFAULT_ROOT, repo)
    result = json.loads(t.apply_changes(root=str(repo), paths="CLAUDE.md"))
    assert result["mode"] == "in place" and result["written"] == ["CLAUDE.md"]
    assert "strict mode" not in (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert "npm install" in (repo / "AGENTS.md").read_text(encoding="utf-8")


def test_roots_are_confined(data_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "AGENTS.md").write_text("- Use npm.\n", encoding="utf-8")
    with pytest.raises(PathError):
        t.analyze_context(root=str(outside))
    with pytest.raises(PathError):
        t.read_context_file("../../../pyproject.toml")


async def test_end_to_end_approved(data_dir: Path) -> None:
    result = await run_approved(SPEC)
    assert_clean_run(result)
    assert interrupts(result) and interrupts(result)[0]["actions"][0]["name"] == "apply_changes"
    assert tool_calls(result, "task")[0]["args"]["subagent_type"] == "contradiction-finder"
    files = result["snapshot"]["files"]
    assert t.DIFF_FILE in files and "+++ b/AGENTS.md" in files[t.DIFF_FILE]
    assert "Conflicting package manager" in files["/context-report.md"]
    assert "pnpm" in result["final"] and "Applied the pruned files" in result["final"]
    applied = list((data_dir / "context_curator" / "applied").iterdir())
    assert len(applied) == 1


async def test_end_to_end_rejected(data_dir: Path) -> None:
    result = await run_rejected(SPEC)
    assert interrupts(result)
    assert not (data_dir / "context_curator" / "applied").exists()
    assert "No files were changed on disk" in result["final"]
