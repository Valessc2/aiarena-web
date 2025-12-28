from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from aiarena.utils.waste_manager import WasteRule, enforce


class Command(BaseCommand):
    help = "Cleanup old cached files safely (allow-list root + patterns). Dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument("--allowed-root", required=True, help="Allow-list root (e.g. /app/media).")
        parser.add_argument("--root", required=True, help="Target root under allowed-root (e.g. /app/media/stats).")
        parser.add_argument("--patterns", required=True, help="Comma-separated glob patterns (e.g. *.png,*.json).")
        parser.add_argument("--keep-newest", type=int, default=2000, help="Keep newest N files.")
        parser.add_argument("--max-total-bytes", type=int, default=300_000_000, help="Max total bytes after cleanup.")
        parser.add_argument("--max-age-days", type=int, default=30, help="Delete files older than N days.")
        parser.add_argument("--max-single-bytes", type=int, default=0, help="Delete any single file bigger than N bytes.")
        parser.add_argument("--apply", action="store_true", help="Actually delete (otherwise dry-run).")

    def handle(self, *args, **opts):
        patterns = tuple(p.strip() for p in str(opts["patterns"]).split(",") if p.strip())
        rule = WasteRule(
            name="waste_manager_cli",
            allowed_root=Path(opts["allowed_root"]),
            root=Path(opts["root"]),
            patterns=patterns,
            keep_newest_n=int(opts["keep_newest"]),
            max_total_bytes=int(opts["max_total_bytes"]),
            max_age_days=int(opts["max_age_days"]),
            max_single_file_bytes=int(opts["max_single_bytes"]),
            top_level_only=True,
        )

        stats = enforce(rule, apply=bool(opts["apply"]))
        self.stdout.write(str(stats))
        if int(stats.get("errors", 0)) > 0:
            self.stdout.write(self.style.WARNING("Completed with errors."))
        else:
            self.stdout.write(self.style.SUCCESS("OK"))
