from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.mail import EmailMessage, mail_admins
from django.core.management.base import BaseCommand

from aiarena.utils.waste_highlighter import HighlightRule, render_report, run_rules, to_json_dict


def _get_env_label() -> str:
    return getattr(settings, "ENV_NAME", "dev" if getattr(settings, "DEBUG", False) else "prod")


def _setting_path(name: str) -> Optional[str]:
    v = getattr(settings, name, None)
    if not v:
        return None
    try:
        return str(Path(v))
    except Exception:
        return str(v)


def _default_rules_from_settings() -> List[HighlightRule]:
    """
    Best-effort defaults that only activate if the path exists in settings.
    Maintainers can override entirely via settings.WASTE_HIGHLIGHTER_RULES.
    """
    candidates = [
        ("media", _setting_path("MEDIA_ROOT")),
        ("private_media", _setting_path("PRIVATE_MEDIA_ROOT")),
        ("replays", _setting_path("REPLAY_ROOT")),
        ("bot_zips", _setting_path("BOT_ZIP_ROOT")),
        ("bot_data", _setting_path("BOT_DATA_ROOT")),
        ("uploads", _setting_path("UPLOAD_ROOT")),
        ("storage", _setting_path("STORAGE_ROOT")),
    ]

    rules: List[HighlightRule] = []
    for name, p in candidates:
        if not p:
            continue
        rules.append(
            HighlightRule(
                name=f"default:{name}",
                roots=[p],
                include_globs=[],
                exclude_globs=["**/.git/**", "**/__pycache__/**", "**/*.tmp", "**/*.log"],
                min_age_days=int(getattr(settings, "WASTE_HIGHLIGHTER_MIN_AGE_DAYS", 30)),
                max_depth=None,
                follow_symlinks=False,
                top_n=int(getattr(settings, "WASTE_HIGHLIGHTER_TOP_N", 20)),
            )
        )
    return rules


def _rules_from_settings() -> Optional[List[HighlightRule]]:
    raw = getattr(settings, "WASTE_HIGHLIGHTER_RULES", None)
    if not raw:
        return None

    rules: List[HighlightRule] = []
    for item in raw:
        rules.append(
            HighlightRule(
                name=str(item.get("name")),
                roots=list(item.get("roots") or []),
                include_globs=list(item.get("include_globs") or []),
                exclude_globs=list(item.get("exclude_globs") or []),
                min_age_days=int(item.get("min_age_days", 30)),
                max_depth=item.get("max_depth", None),
                follow_symlinks=bool(item.get("follow_symlinks", False)),
                top_n=int(item.get("top_n", 20)),
            )
        )
    return rules


def _resolve_recipients() -> List[str]:
    """
    Internal-only: either explicit WASTE_HIGHLIGHTER_RECIPIENTS or settings.ADMINS.
    """
    explicit = getattr(settings, "WASTE_HIGHLIGHTER_RECIPIENTS", None)
    if explicit:
        return [str(x).strip() for x in explicit if str(x).strip()]

    admins = getattr(settings, "ADMINS", None) or []
    emails = [email for (_name, email) in admins if email]
    return [e.strip() for e in emails if e.strip()]


class Command(BaseCommand):
    help = "Read-only Waste Highlighter: scan storage roots and print a report. Optional internal email."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
        parser.add_argument("--email", action="store_true", help="Email report to internal recipients only")
        parser.add_argument("--min-age-days", type=int, default=None, help="Override min age days for ALL rules")
        parser.add_argument("--top-n", type=int, default=None, help="Override Top-N largest list for ALL rules")
        parser.add_argument("--env", type=str, default=None, help="Override env label in subject/header")

    def handle(self, *args, **opts):
        env = opts.get("env") or _get_env_label()
        now = time.time()

        rules = _rules_from_settings() or _default_rules_from_settings()

        # CLI overrides
        min_age_days = opts.get("min_age_days")
        top_n = opts.get("top_n")
        if min_age_days is not None or top_n is not None:
            patched: List[HighlightRule] = []
            for r in rules:
                patched.append(
                    HighlightRule(
                        name=r.name,
                        roots=r.roots,
                        include_globs=r.include_globs,
                        exclude_globs=r.exclude_globs,
                        min_age_days=int(min_age_days) if min_age_days is not None else r.min_age_days,
                        max_depth=r.max_depth,
                        follow_symlinks=r.follow_symlinks,
                        top_n=int(top_n) if top_n is not None else r.top_n,
                    )
                )
            rules = patched

        report = run_rules(rules, now_epoch=now)

        if opts.get("json"):
            payload: Dict[str, Any] = to_json_dict(report, env_label=env)
            out = json.dumps(payload, indent=2, sort_keys=True)
        else:
            out = render_report(report, env_label=env)

        self.stdout.write(out)

        if opts.get("email"):
            recipients = _resolve_recipients()
            if not recipients:
                raise RuntimeError("No internal recipients configured. Set ADMINS or WASTE_HIGHLIGHTER_RECIPIENTS.")

            ts_utc = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now))
            subject = f"Waste Highlighter Report - {env} - {ts_utc}"

            explicit = getattr(settings, "WASTE_HIGHLIGHTER_RECIPIENTS", None)
            if explicit:
                EmailMessage(subject=subject, body=out, to=recipients).send(fail_silently=False)
            else:
                mail_admins(subject=subject, message=out, fail_silently=False)
