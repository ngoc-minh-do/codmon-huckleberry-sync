from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING

from .config import ConfigError, load_config

if TYPE_CHECKING:
    from .sync import BackfillResult, SyncResult


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

    backfill = subparsers.add_parser(
        "backfill",
        help="One-time sync of all daily reports from a start date through the end date (default today)",
    )
    backfill.add_argument(
        "--start",
        type=lambda value: date.fromisoformat(value),
        default=None,
        help="First day to backfill YYYY-MM-DD (default: ~18 months ago)",
    )
    backfill.add_argument("--end", type=lambda value: date.fromisoformat(value), default=None)
    backfill.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    backfill.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    backfill.add_argument("--child", default=None)
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

    return asyncio.run(_run_sync_with_notify(cfg, args, command, target))


_KIND_ORDER = {"bottle": 0, "sleep": 1, "solids": 2, "activity": 3, "temperature": 4, "diaper": 5}


async def _run_sync_with_notify(cfg, args, command: str, target: date) -> int:
    from .notify import ERROR_TITLE, OK_TITLE, AppriseNotifier

    notifier = AppriseNotifier(cfg.apprise_url)
    try:
        if command == "backfill":
            from datetime import timedelta

            from .sync import backfill

            start = args.start or date.today() - timedelta(days=550)
            result = await backfill(cfg, start, args.end, dry_run=args.dry_run, child=args.child)
            title, body = OK_TITLE, _format_backfill(cfg, result)
            code = 1 if result.days_failed else 0
        else:
            from .sync import sync_day

            result = await sync_day(cfg, target, dry_run=args.dry_run, child=args.child)
            title, body = OK_TITLE, _format_sync(cfg, result)
            code = 0
        print(_result_json(command, result))
        if cfg.dry_run:
            return 0
        if not code:
            await notifier.send(title=title, body=body, message_type="success")
        return code
    except Exception as exc:
        if not cfg.dry_run:
            await notifier.send(title=ERROR_TITLE, body=f"{type(exc).__name__}: {exc}", message_type="failure")
        raise


def _format_sync(cfg, result: SyncResult) -> str:
    lines = [f"{result.day}  posts={result.posts}"]
    lines.append(
        f"milk={result.milk} sleep={result.sleep} meal={result.meal} "
        f"activity={result.activity} temp={result.temperature} poo={result.poo}"
    )
    if result.translated:
        lines.append(f"memos translated: {result.translated}/{result.activity}")
    if not result.events:
        return "\n".join(lines)
    for event in sorted(result.events, key=lambda e: (e.start, _KIND_ORDER.get(e.kind, 99))):
        when = event.start.astimezone(cfg.timezone).strftime("%H:%M")
        lines.append(f"- {when} {event.kind} {_event_summary(event)}")
    if result.skipped_by_kind:
        skipped = ", ".join(f"{kind}={count}" for kind, count in sorted(result.skipped_by_kind.items()))
        lines.append(f"\nskipped (already in Huckleberry): {skipped}")
    lines.append(f"planned={result.planned} written={result.written}")
    return "\n".join(lines)


def _format_backfill(cfg, result: BackfillResult) -> str:
    lines = [
        f"range {result.start}..{result.end}",
        f"days={result.days} failed={result.days_failed}",
    ]
    if result.written_by_kind:
        breakdown = ", ".join(f"{kind}={count}" for kind, count in sorted(result.written_by_kind.items()))
        lines.append(f"written={result.written}  [{breakdown}]")
    else:
        lines.append(f"written={result.written}")
    return "\n".join(lines)


def _event_summary(event) -> str:
    payload = event.payload or {}
    mode = payload.get("mode")
    if event.kind == "bottle":
        return f"{payload.get('amount_ml')}ml {payload.get('bottle_type', '')}".strip()
    if event.kind == "sleep":
        return f"~{event.end.strftime('%H:%M')}" if event.end else ""
    if event.kind == "solids":
        foods = payload.get("foods", [])
        return ", ".join(str(food) for food in foods[:2]) + (" …" if len(foods) > 2 else "")
    if event.kind == "activity":
        description = str(payload.get("description", ""))
        mode_label = mode or "indoorPlay"
        return f"{mode_label}: {description}"
    if event.kind == "temperature":
        return f"{payload.get('amount')}{payload.get('units')}"
    if event.kind == "diaper":
        return f"{payload.get('mode')} {payload.get('consistency', '')}".strip()
    return str(mode or "")


def _result_json(command: str, result) -> str:
    import json

    if command == "backfill":
        payload = {
            "complete": 1,
            "code": 1 if result.days_failed else 0,
            "command": command,
            "start": result.start.isoformat(),
            "end": result.end.isoformat(),
            "dryRun": result.dry_run,
            "days": result.days,
            "daysFailed": result.days_failed,
            "written": result.written,
            "writtenByKind": dict(result.written_by_kind),
        }
    else:
        payload = {
            "complete": 1,
            "code": 0,
            "command": command,
            "day": result.day.isoformat(),
            "dryRun": result.dry_run,
            "posts": result.posts,
            "milk": result.milk,
            "sleep": result.sleep,
            "meal": result.meal,
            "activity": result.activity,
            "temperature": result.temperature,
            "poo": result.poo,
            "planned": result.planned,
            "translated": result.translated,
            "written": result.written,
            "writtenByKind": dict(result.written_by_kind),
            "skippedByKind": dict(result.skipped_by_kind),
        }
    return json.dumps(payload)


if __name__ == "__main__":
    raise SystemExit(main())
