from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# =========================
# Data models (read-only)
# =========================

@dataclass(frozen=True)
class HighlightRule:
    """
    Read-only scan rule.
    - roots: directories to scan
    - include_globs/exclude_globs: patterns matched against path relative to each root
    - min_age_days: only report files older than this (mtime)
    - max_depth: None => unlimited; 0 => root only; 1 => root + one level, etc.
    - follow_symlinks: default False (safer)
    """
    name: str
    roots: List[str]
    include_globs: Optional[List[str]] = None
    exclude_globs: Optional[List[str]] = None
    min_age_days: int = 30
    max_depth: Optional[int] = None
    follow_symlinks: bool = False
    top_n: int = 20


@dataclass(frozen=True)
class FileHit:
    path: str
    size_bytes: int
    mtime_epoch: float
    age_seconds: float

    @property
    def age_days(self) -> float:
        return self.age_seconds / 86400.0


@dataclass
class RuleReport:
    rule_name: str
    scanned_roots: List[str]
    hits_count: int
    hits_bytes: int
    oldest_mtime_epoch: Optional[float]
    newest_mtime_epoch: Optional[float]
    top_largest: List[FileHit]
    age_buckets: Dict[str, Dict[str, int]]  # label -> {"count": int, "bytes": int}
    errors: List[str]


@dataclass
class WasteReport:
    generated_at_utc_epoch: float
    rules: List[RuleReport]

    @property
    def total_hits_count(self) -> int:
        return sum(r.hits_count for r in self.rules)

    @property
    def total_hits_bytes(self) -> int:
        return sum(r.hits_bytes for r in self.rules)


# =========================
# Core scanning
# =========================

_DEFAULT_BUCKETS_DAYS: List[Tuple[str, int]] = [
    ("<=7d", 7),
    ("<=30d", 30),
    ("<=90d", 90),
    ("<=180d", 180),
    ("<=365d", 365),
    (">365d", 10**9),
]


def scan(rule: HighlightRule, now_epoch: Optional[float] = None) -> RuleReport:
    now = float(time.time() if now_epoch is None else now_epoch)
    min_age_seconds = max(0, int(rule.min_age_days) * 86400)

    include = rule.include_globs or []
    exclude = rule.exclude_globs or []

    hits: List[FileHit] = []
    errors: List[str] = []
    oldest: Optional[float] = None
    newest: Optional[float] = None
    total_bytes = 0

    for root in rule.roots:
        root_path = Path(root).expanduser()
        try:
            if not root_path.exists():
                errors.append(f"[{rule.name}] root missing: {root_path}")
                continue
            if not root_path.is_dir():
                errors.append(f"[{rule.name}] root not a dir: {root_path}")
                continue
        except Exception as e:
            errors.append(f"[{rule.name}] root check error: {root_path} :: {e!r}")
            continue

        for file_path, st in _walk_files(
            root_path,
            max_depth=rule.max_depth,
            follow_symlinks=rule.follow_symlinks,
            errors=errors,
            rule_name=rule.name,
        ):
            try:
                rel = str(file_path.relative_to(root_path)).replace("\\", "/")
            except Exception:
                rel = str(file_path).replace("\\", "/")

            if include and not _match_any(rel, include):
                continue
            if exclude and _match_any(rel, exclude):
                continue

            mtime = float(getattr(st, "st_mtime", 0.0))
            age_seconds = max(0.0, now - mtime)
            if age_seconds < min_age_seconds:
                continue

            size = int(getattr(st, "st_size", 0))
            hits.append(FileHit(path=str(file_path), size_bytes=size, mtime_epoch=mtime, age_seconds=age_seconds))
            total_bytes += size

            oldest = mtime if oldest is None else min(oldest, mtime)
            newest = mtime if newest is None else max(newest, mtime)

    # Deterministic ordering: size desc, then path asc
    hits.sort(key=lambda h: (-h.size_bytes, h.path))
    top_largest = hits[: max(0, int(rule.top_n))]
    age_buckets = _bucketize(hits)

    return RuleReport(
        rule_name=rule.name,
        scanned_roots=[str(Path(r).expanduser()) for r in rule.roots],
        hits_count=len(hits),
        hits_bytes=total_bytes,
        oldest_mtime_epoch=oldest,
        newest_mtime_epoch=newest,
        top_largest=top_largest,
        age_buckets=age_buckets,
        errors=errors,
    )


def run_rules(rules: Iterable[HighlightRule], now_epoch: Optional[float] = None) -> WasteReport:
    now = float(time.time() if now_epoch is None else now_epoch)
    rule_reports = [scan(r, now_epoch=now) for r in rules]
    return WasteReport(generated_at_utc_epoch=now, rules=rule_reports)


# =========================
# Rendering / JSON
# =========================

def render_report(report: WasteReport, env_label: str = "unknown") -> str:
    lines: List[str] = []
    lines.append(f"WASTE HIGHLIGHTER REPORT - env={env_label}")
    lines.append(f"Generated (UTC epoch): {int(report.generated_at_utc_epoch)}")
    lines.append("")
    lines.append(f"TOTAL: files={report.total_hits_count} bytes={_fmt_bytes(report.total_hits_bytes)}")
    lines.append("=" * 72)

    for rr in report.rules:
        lines.append("")
        lines.append(f"[RULE] {rr.rule_name}")
        lines.append("  roots:")
        for r in rr.scanned_roots:
            lines.append(f"    - {r}")
        lines.append(f"  hits: {rr.hits_count} files, {_fmt_bytes(rr.hits_bytes)}")

        if rr.oldest_mtime_epoch is not None and rr.newest_mtime_epoch is not None:
            lines.append(f"  mtime range: oldest={_fmt_utc(rr.oldest_mtime_epoch)} newest={_fmt_utc(rr.newest_mtime_epoch)}")

        lines.append("  age buckets:")
        for label in _bucket_labels_in_order(rr.age_buckets):
            b = rr.age_buckets[label]
            lines.append(f"    - {label:>6}: files={b['count']:>6} bytes={_fmt_bytes(b['bytes'])}")

        lines.append(f"  top {len(rr.top_largest)} largest:")
        for h in rr.top_largest:
            lines.append(f"    - {_fmt_bytes(h.size_bytes):>10}  age={h.age_days:7.1f}d  {h.path}")

        if rr.errors:
            lines.append("  errors:")
            for e in rr.errors:
                lines.append(f"    - {e}")

        lines.append("-" * 72)

    return "\n".join(lines)


def to_json_dict(report: WasteReport, env_label: str = "unknown") -> Dict[str, Any]:
    return {
        "env": env_label,
        "generated_at_utc_epoch": report.generated_at_utc_epoch,
        "total_hits_count": report.total_hits_count,
        "total_hits_bytes": report.total_hits_bytes,
        "rules": [
            {**asdict(rr), "top_largest": [asdict(h) for h in rr.top_largest]}
            for rr in report.rules
        ],
    }


# =========================
# Internals (read-only)
# =========================

def _walk_files(
    root: Path,
    max_depth: Optional[int],
    follow_symlinks: bool,
    errors: List[str],
    rule_name: str,
) -> Iterable[Tuple[Path, os.stat_result]]:
    """
    Yield (file_path, stat) for regular files only.
    Uses os.scandir for performance; never opens file contents.
    """
    root = root.resolve()
    base_depth = len(root.parts)

    def depth_ok(p: Path) -> bool:
        if max_depth is None:
            return True
        rel_depth = max(0, len(p.parts) - base_depth)
        return rel_depth <= max_depth

    stack: List[Path] = [root]
    while stack:
        cur = stack.pop()
        if not depth_ok(cur):
            continue

        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except Exception as e:
            errors.append(f"[{rule_name}] scandir error: {cur} :: {e!r}")
            continue

        entries.sort(key=lambda de: de.name)  # deterministic
        for de in entries:
            try:
                if de.is_symlink() and not follow_symlinks:
                    continue

                if de.is_dir(follow_symlinks=follow_symlinks):
                    stack.append(Path(de.path))
                    continue

                if de.is_file(follow_symlinks=follow_symlinks):
                    st = de.stat(follow_symlinks=follow_symlinks)
                    yield (Path(de.path), st)
            except Exception as e:
                errors.append(f"[{rule_name}] entry error: {de.path} :: {e!r}")
                continue


def _match_any(rel_path: str, patterns: List[str]) -> bool:
    rp = rel_path.replace("\\", "/")
    for pat in patterns:
        p = pat.replace("\\", "/")
        if fnmatch.fnmatch(rp, p):
            return True
    return False


def _bucketize(hits: List[FileHit]) -> Dict[str, Dict[str, int]]:
    buckets: Dict[str, Dict[str, int]] = {lbl: {"count": 0, "bytes": 0} for (lbl, _) in _DEFAULT_BUCKETS_DAYS}
    for h in hits:
        d = h.age_days
        label = ">365d"
        for lbl, maxd in _DEFAULT_BUCKETS_DAYS:
            if d <= float(maxd):
                label = lbl
                break
        buckets[label]["count"] += 1
        buckets[label]["bytes"] += int(h.size_bytes)
    return buckets


def _bucket_labels_in_order(age_buckets: Dict[str, Dict[str, int]]) -> List[str]:
    labels = [lbl for (lbl, _) in _DEFAULT_BUCKETS_DAYS if lbl in age_buckets]
    extras = sorted([k for k in age_buckets.keys() if k not in labels])
    return labels + extras


def _fmt_bytes(n: int) -> str:
    n = int(n)
    if n < 1024:
        return f"{n} B"
    units = ["KB", "MB", "GB", "TB", "PB"]
    f = float(n)
    for u in units:
        f /= 1024.0
        if f < 1024.0:
            return f"{f:,.2f} {u}"
    return f"{f:,.2f} EB"


def _fmt_utc(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(epoch))
