"""Safe access to input data (examples and user-provided files) and the agent's state directory."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from nai_agents.core.settings import get_settings


class PathError(ValueError):
    pass


def resolve_input(path: str, subdir: str | None = None) -> Path:
    """Resolve a user/model supplied path to a real file, confined to allowed roots.

    Allowed roots: the examples directory and the app data directory. Relative paths are tried against
    `examples/<subdir>`, then `examples/`, then the data directory. Anything that escapes them is refused,
    so a model can't read `/etc/passwd` or `~/.ssh` through a tool.
    """
    settings = get_settings()
    roots = [settings.examples_dir, settings.state_dir]
    raw = Path(path.strip().removeprefix("examples/") if not Path(path).is_absolute() else path)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        if subdir:
            candidates.append(settings.examples_dir / subdir / raw)
        candidates += [settings.examples_dir / raw, settings.state_dir / raw]
    for cand in candidates:
        real = cand.resolve()
        if any(real == r or r in real.parents for r in roots) and real.exists():
            return real
    raise PathError(f"{path!r} not found under the examples or data directory")


def read_text(path: str, subdir: str | None = None, limit: int = 2_000_000) -> str:
    real = resolve_input(path, subdir)
    if real.stat().st_size > limit:
        raise PathError(f"{path!r} is larger than {limit} bytes")
    return real.read_text(encoding="utf-8", errors="replace")


def read_records(path: str, subdir: str | None = None) -> list[dict[str, Any]]:
    """CSV, JSON (list or {"data": [...]}) or JSON Lines, as a list of dicts."""
    text = read_text(path, subdir)
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return list(csv.DictReader(io.StringIO(text)))
    if suffix in {".jsonl", ".ndjson"}:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, dict):
        for key in ("data", "records", "items", "usage", "events", "results"):
            if isinstance(data.get(key), list):
                return data[key]
        lists = [v for v in data.values() if isinstance(v, list)]
        if len(lists) == 1:  # e.g. {"grants": [...]} or {"requests": [...]}
            return lists[0]
        return [data]
    return list(data)


def state_path(*parts: str) -> Path:
    """A path under the app data directory (created on demand) for simulated stores and outputs."""
    path = get_settings().state_dir.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_json_state(name: str, default: Any) -> Any:
    path = state_path(name)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_state(name: str, value: Any) -> Path:
    path = state_path(name)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def to_json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)
