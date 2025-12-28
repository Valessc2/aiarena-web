from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple


@dataclass(frozen=True)
class WasteRule:
    """
    Server-side cleanup rule (safe allow-list).

    Safety model:
      - root MUST be inside allowed_root (resolved path check)
      - deletes ONLY files matching configured filename patterns
      - top-level files only (no recursion) for V1
      - deterministic ordering
    """
    name: str
    allowed_root: Path
    root: Path
    patterns: tuple[str, ...]          # e.g. ("*.png", "*.svg", "*.json")
    keep_newest_n: int                 # keep newest N files
    max_total_bytes: int               # cap total bytes for remaining files
    max_age_days: int                  # delete files older than this many days
    max_single_file_bytes: int = 0     # optional guardrail
    top_level_only: bool = True


def _is_within_root(p: Path, root: Path) -> bool:
    try:
        p_res = p.resolve()
        r_res = root.resolve()
        return str(p_res).startswith(str(r_res) + os.sep) or p_res == r_res
    except Exception:
        return False


def _list_candidates(rule: WasteRule) -> list[tuple[Path, os.stat_result]]:
    root = rule.root
    out: list[tuple[Path, os.stat_result]] = []
    if not root.exists():
        return out

    try:
        entries = list(root.iterdir())
    except Exception:
        return out

    for p in entries:
        try:
            if p.is_dir():
                if rule.top_level_only:
                    continue
                continue
            if not p.is_file():
                continue

            # match patterns against filename (Path.match matches from rightmost parts)
            if not any(Path(p.name).match(pat) for pat in rule.patterns):
                continue

            st = p.stat()
            out.append((p, st))
        except Exception:
            continue

    # deterministic: oldest first, tie-break by name
    out.sort(key=lambda t: (t[1].st_mtime, t[0].name))
    return out


def _sum_sizes(files: Iterable[tuple[Path, os.stat_result]]) -> int:
    total = 0
    for _p, st in files:
        total += int(st.st_size)
    return total


def enforce(rule: WasteRule, *, apply: bool = False) -> dict:
    """
    Enforce WasteRule. If apply=False => dry-run (no deletion).
    Never raises.
    """
    stats = {
        "rule": rule.name,
        "apply": apply,
        "candidates": 0,
        "deleted": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "errors": 0,
    }

    try:
        allowed_root = rule.allowed_root.resolve()
        root = rule.root.resolve()

        # HARD safety: refuse anything not under allowed_root
        if not _is_within_root(root, allowed_root):
            stats["errors"] += 1
            return stats

        files = _list_candidates(rule)
        stats["candidates"] = len(files)
        stats["bytes_before"] = _sum_sizes(files)

        now = time.time()
        deletions: list[Path] = []

        # 1) age-based deletions
        if rule.max_age_days > 0:
            cutoff = now - (rule.max_age_days * 24 * 60 * 60)
            for p, st in files:
                if st.st_mtime < cutoff:
                    deletions.append(p)

        # 2) single-file size guard
        if rule.max_single_file_bytes and rule.max_single_file_bytes > 0:
            for p, st in files:
                if st.st_size > rule.max_single_file_bytes:
                    deletions.append(p)

        delset = set(deletions)
        remaining = [(p, st) for (p, st) in files if p not in delset]

        # 3) keep newest N (delete oldest)
        if rule.keep_newest_n >= 0 and len(remaining) > rule.keep_newest_n:
            over = len(remaining) - rule.keep_newest_n
            for i in range(over):
                delset.add(remaining[i][0])
            remaining = remaining[over:]

        # 4) max total bytes (delete oldest until under cap)
        if rule.max_total_bytes > 0:
            total = _sum_sizes(remaining)
            if total > rule.max_total_bytes:
                for p, st in remaining:
                    if total <= rule.max_total_bytes:
                        break
                    delset.add(p)
                    total -= int(st.st_size)

        # deterministic delete order
        to_delete = sorted(delset, key=lambda p: p.name)

        if apply:
            deleted = 0
            for p in to_delete:
                try:
                    p.unlink()
                    deleted += 1
                except FileNotFoundError:
                    pass
                except Exception:
                    stats["errors"] += 1
            stats["deleted"] = deleted
            stats["bytes_after"] = _sum_sizes(_list_candidates(rule))
        else:
            stats["deleted"] = len(to_delete)
            # estimate bytes_after
            orig_map = {p: st for (p, st) in files}
            est = stats["bytes_before"]
            for p in to_delete:
                st = orig_map.get(p)
                if st:
                    est -= int(st.st_size)
            stats["bytes_after"] = max(0, est)

        return stats

    except Exception:
        stats["errors"] += 1
        return stats
