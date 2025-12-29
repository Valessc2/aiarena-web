from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from aiarena.utils.waste_manager import WasteRule, enforce


class Command(BaseCommand):
    help = "Disk cleanup helper for media folders using aiarena.utils.waste_manager rules."

    def add_arguments(self, parser):
        parser.add_argument("--allowed-root", required=True, help="Safety root. Root must be inside this.")
        parser.add_argument("--root", required=True, help="Folder to clean (must be within allowed-root).")
        parser.add_argument(
            "--patterns",
            required=True,
            help='Comma-separated globs, e.g. "*.png,*.svg,*.webp,*.json"',
        )
        parser.add_argument("--keep-newest", type=int, required=True, help="Keep newest N files (per rule).")
        parser.add_argument("--max-total-bytes", type=int, required=True, help="Target cap in bytes.")
        parser.add_argument("--max-age-days", type=int, required=True, help="Delete if older than this many days.")
        parser.add_argument("--apply", action="store_true", help="Actually delete. Omit for dry-run.")

    def handle(self, *args, **opts):
        allowed_root = Path(opts["allowed_root"]).resolve()
        root = Path(opts["root"]).resolve()

        if not allowed_root.exists() or not allowed_root.is_dir():
            raise CommandError(f"--allowed-root does not exist or is not a directory: {allowed_root}")
        if not root.exists() or not root.is_dir():
            raise CommandError(f"--root does not exist or is not a directory: {root}")

        # Ensure root is within allowed_root
        try:
            root.relative_to(allowed_root)
        except ValueError:
            raise CommandError(f"--root must be within --allowed-root. root={root} allowed_root={allowed_root}")

        patterns = tuple(p.strip() for p in str(opts["patterns"]).split(",") if p.strip())
        if not patterns:
            raise CommandError("--patterns produced no globs")

        rule = WasteRule(
            name=f"cli:{root.name}",
            allowed_root=allowed_root,
            root=root,
            patterns=patterns,
            keep_newest_n=int(opts["keep_newest"]),
            max_total_bytes=int(opts["max_total_bytes"]),
            max_age_days=int(opts["max_age_days"]),
        )

        result = enforce(rule, apply=bool(opts["apply"]))

        # Dry-run: make output unambiguous (do not claim deletions happened).

        # In dry-run, 'would_delete' holds the count; 'deleted' stays 0.

        if not bool(opts["apply"]) and "deleted" in result:
            result["would_delete"] = int(result.get("deleted", 0))

            result["deleted"] = 0
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
