# codmon-huckleberry-sync

Syncs the nursery's daily report (milk, meals, naps, activities) from
[Codmon](https://parents.codmon.com/) into the [Huckleberry](https://huckleberry.com/) baby tracking app.

Codmon exposes no public parent API, but its parent web app is a SPA that
talks to an internal JSON API (`ps-api.codmon.com`) using session cookies from a
plain email/password `POST /login`. This project uses that API directly with
`aiohttp` — **no browser required** (a Playwright fallback scraper still exists
for forensics via `CODMON_TRANSPORT=browser`). Huckleberry has no official API
either — writes go through the reverse-engineered
[`huckleberry-api`](https://github.com/Woyken/py-huckleberry-api) library
(Firebase Firestore, the same transport the Huckleberry app uses).

Both integrations are unofficial and can break if either service changes.

## How it works

1. **Fetch** — log in to `ps-api.codmon.com` (`POST /api/v2/parent/login`),
   resolve the nursery `service_id` from `/children`, then pull the day's
   連絡帳 (daily report) posts from `/timeline`.
2. **Parse** — the report body is structured JSON: `meal` (e.g.
   `給食：完食 おかわり ミルク160cc`), `sleepings` (e.g. `12:25~14:15`),
   `memo` (HTML activity text), `tempratures`.
3. **Map** — events are translated into Huckleberry operations:
   - milk → `log_bottle` (Formula / Breast Milk, ml)
   - naps → `log_sleep`
   - meals → `log_solids`
- activities → `log_activity` (`outdoorPlay` when 散歩/公園 appears)
    - temperatures → `log_temperature` (disable with `SYNC_TEMPERATURE=false`)
    - activity memos can be translated Japanese→English via a local LLM
      (`TRANSLATE_ACTIVITY=true`, see below) — off by default
4. **Write** — executed against Huckleberry via `huckleberry-api` unless in
   dry-run mode.
5. **Dedupe** — before writing, the live Huckleberry history
   (`feed`/`sleep`/`activities`/`health` interval subcollections) is checked;
   an event is skipped when a same-type event already exists within
   `DEDUP_WINDOW_MINUTES` (default 15) of its planned time. Every run re-reads
   the report and relies on this live check, so syncs stay idempotent even if
   a previous run failed partway.

Growth measurements (身長/体重/頭囲) live on the separate 成長記録 page
(`/api/v2/parent/member_growths/`) and are not part of the daily report, so
they are synced by a dedicated `sync-growth` command instead of `sync`.
Each measurement is written at the date and time the nursery recorded it
(`insert_datetime`; Codmon's `record_date` is only a month label), and a
record is skipped if Huckleberry already has a growth entry that calendar day
(chest circumference 胸囲 is dropped — Huckleberry has no field for it).

## Local setup

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.14.

```bash
uv sync
cp .env.example .env
# fill in your credentials
```

### Check what would be synced (dry run)

```bash
uv run codmon-sync sync --date 2026-10-01        # dry run by default (DRY_RUN=true)
uv run codmon-sync sync --date 2026-10-01 --no-dry-run
```

Without `--date`, today is used (in `TZ`, default `Asia/Tokyo`).

### Sync growth records (measurements)

```bash
uv run codmon-sync sync-growth                  # dry run over the last ~3 years
uv run codmon-sync sync-growth --no-dry-run
uv run codmon-sync sync-growth --start 2026-04-01 --end 2026-09-30
```

`sync-growth` reads the nursery's 成長記録 (height/weight/head-circuit
measurements) for the date range and writes one Huckleberry `growth` entry per
measurement date. Like `sync`, it is dry-run by default and dedupes against
Huckleberry's existing entries.

### Introspection (when your nursery's report layout differs)

`codmon-sync introspect` shows the full parsed report body (`api_report.txt`);
with `CODMON_TRANSPORT=browser` it dumps the rendered pages, screenshots and the
raw API traffic for forensics.

## Docker

Prefer not to manage a Python virtualenv? Run it as a container:

```bash
docker build -t codmon-huckleberry-sync:latest .
```

The image runs the same `sync` command by default. `DATA_DIR` only holds
introspection artifacts, so it can be left unset or mounted anywhere:

```bash
docker run --rm \
  --env-file .env \
  codmon-huckleberry-sync:latest sync
```

Set `DRY_RUN=false` (env or `-e`) once you want real writes. For periodic
scheduling, point any scheduler at that `docker run` command.

## Environment variables

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `CODMON_EMAIL` | yes | — | Codmon parent login email |
| `CODMON_PASSWORD` | yes | — | Codmon parent login password |
| `HUCKLEBERRY_EMAIL` | yes | — | Huckleberry login email |
| `HUCKLEBERRY_PASSWORD` | yes | — | Huckleberry login password |
| `CODMON_TRANSPORT` | no | `api` | `api` (direct JSON) or `browser` (Playwright forensics) |
| `SYNC_TEMPERATURE` | no | `true` | Whether to write reported temperatures |
| `SYNC_DIAPER` | no | `true` | Whether to write evacuations as diaper entries (`mode=both`) |
| `SYNC_BATH` | no | `true` | Whether to write water-play days (沐浴`有`) as a `bath` activity |
| `BATH_TIME` | no | `09:30` | Default time for the water-play activity |
| `CHILD` | no | first child | Child name/kana/id to sync; shared token matched on both sides |
| `DEDUP_WINDOW_MINUTES` | no | `15` | Skip an event if a same-type Huckleberry event exists within this window |
| `TRANSLATE_ACTIVITY` | no | `false` | Translate the daily memo into English via `LLM_*` before writing the activity |
| `LLM_BASE_URL` | only with `TRANSLATE_ACTIVITY` | — | OpenAI-compatible chat-completions endpoint base URL |
| `LLM_MODEL` | no | `chat-default` | Model name served by the endpoint |
| `LLM_API_KEY` | no | — | Optional Bearer token for the endpoint |
| `LLM_TIMEOUT` | no | `180` | Max seconds to wait for a translation response |
| `APPRISE_URL` | no | — | Apprise webhook; posts a success/failure notification after each real sync |
| `TZ` | no | `Asia/Tokyo` | Standard IANA timezone used for event timestamps and "today" |
| `DRY_RUN` | no | `true` | Plan only; do not write to Huckleberry |
| `DATA_DIR` | no | `data` | Introspection output directory |
| `HEADLESS` | no | `true` | Run headless Chromium |

## Disclaimer / risk

- Uses your own account credentials to read Codmon and write Huckleberry; both
  are reverse-engineered, non-official integrations. Use at your own risk.
- Huckleberry writes create permanent entries. Start with dry-run mode and
  verify planned events before enabling real sync.
- Credentials are read from the environment; keep `.env` out of version control.