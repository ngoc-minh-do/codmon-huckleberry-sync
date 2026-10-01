from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date

from .config import ConfigError, load_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codmon-sync",
        description="Sync Codmon daily reports into Huckleberry.",
    )
    parser.add_argument(
        "--date",
        type=lambda value: date.fromisoformat(value),
        default=None,
        help="Target date YYYY-MM-DD (default: today in the configured timezone)",
    )
    parser.add_argument("--force", action="store_true", help="Re-sync even if the day is already synced")
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=None,
        help="Print planned events without writing to Huckleberry",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Write to Huckleberry even if DRY_RUN is true in the environment",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    sync = subparsers.add_parser("sync", help="Fetch today's Codmon report and sync it into Huckleberry")
    sync.add_argument("--date", type=lambda value: date.fromisoformat(value), default=None)
    sync.add_argument("--force", action="store_true")
    sync.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    sync.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    sync.add_argument(
        "--child",
        default=None,
        help="Child to sync (name, kana, or id). Default: first child on each side.",
    )

    introspect = subparsers.add_parser(
        "introspect",
        help="Log into Codmon and dump the current page + API responses for parser calibration",
    )
    introspect.add_argument("--date", type=lambda value: date.fromisoformat(value), default=None)
    introspect.add_argument("--output", default=None, help="Subdirectory under DATA_DIR for artifacts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(dry_run=args.dry_run)
    except ConfigError as exc:
        parser.error(str(exc))

    target = args.date or date.today()
    command = args.command or "sync"

    if command == "introspect":
        from .sync import introspect

        report = asyncio.run(introspect(cfg, args.date, args.output))
        print(f"Captured {len(report.posts)} posts; artifacts under {cfg.data_dir}")
        return 0

    from .sync import sync_day

    return asyncio.run(sync_day(cfg, target, force=args.force, dry_run=args.dry_run, child=args.child))


if __name__ == "__main__":
    raise SystemExit(main())
