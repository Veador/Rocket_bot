# Rocket.Chat HC Bot

Minimal Python bot for Rocket.Chat.

## What the bot does

- Connects to Rocket.Chat realtime websocket (DDP)
- Listens for new messages in configured rooms
- Ignores bot's own messages by default (configurable)
- Processes only root command messages (thread replies are ignored)
- Processes only messages starting with configured command prefixes (`!hc`, `!help`, `!book`, `!unbook`)
- Supports `!help`
- Supports `!hc version <alias>`
- Supports `!book <alias> <time>` (`Xm`, `Xh`, `Xd`; min `15m`, max `7d`)
- Supports `!book status <alias>`
- Supports `!unbook <alias>`
- Supports `!unbook all`
- Shows remaining booking time in replies as `d h min` (for example `2h 5min`)
- Resolves `<alias>` to configured environment (`name` + `healthcheck URL`)
- Performs HTTP GET to healthcheck URL
- Reads only:
  - `version.branch`
  - `version.commit`
  - `version.tag`
  - top-level `timestamp`
- Replies in a thread under the original command message
- Saves success and error results to SQLite

## What it does not do yet

- No Docker setup
- No tests
- No metrics/monitoring
- No retries/circuit breaker for `healthcheck URL` requests
- No advanced permission model
- No dm support
- Can't compare versions

## Project structure

```text
app/
  booking_service.py # booking business logic by URL key
  booking_time.py    # booking duration parse/format helpers
  main.py          # app wiring and runtime flow
  config.py        # YAML config loading + validation
  commands.py      # command parsing and alias resolution
  health.py        # _hc HTTP request + response parsing
  rc_rest.py       # Rocket.Chat REST thread reply client
  rc_realtime.py   # Rocket.Chat realtime listener (DDP)
  storage.py       # SQLite repository
  models.py        # shared dataclasses

config/
  bot_config.yaml
  bot_config.example.yaml

data/
  bot.db           # created at runtime
```

## Python version

- Python `3.12+`

## Install dependencies

From the repository root:

```bash
# 1) Create virtual environment
python3.12 -m venv .venv

# 2) Activate virtual environment (macOS/Linux)
source .venv/bin/activate

# 3) Install project in editable mode (installs all dependencies from pyproject.toml)
python -m pip install -e .
```

Windows PowerShell activation:

```powershell
.venv\Scripts\Activate.ps1
```

## Configure `bot_config.yaml`

Edit `config/bot_config.yaml`:

- `rocketchat.base_url`: Rocket.Chat base URL
- `rocketchat.user_id`: bot user ID
- `rocketchat.auth_token_env`: env var name for bot auth token
- `rocketchat.ignore_own_messages`: ignore messages from authenticated user (`true` by default)
- `rocketchat.websocket_url`: websocket endpoint (or derivable)
- `rocketchat.room_filters`: room IDs to subscribe to
- `commands.hc_version`: version command text (default `!hc version`)
- `commands.hc_help`: help command text (default `!help`)
- `commands.book`: booking command text (default `!book`)
- `commands.book_status`: booking status command text (default `!book status`)
- `commands.unbook`: unbooking command text (default `!unbook`)
- `commands.unbook_all`: bulk unbooking command text (default `!unbook all`)
- `help.template`: help reply template with placeholders `{{commands}}` and `{{aliases}}`
- `messages.*`: booking-related user-facing templates (no hardcoded texts)
- `environments`: alias -> environment mapping (`name` + `url`)
- `database.sqlite_path`: SQLite file path
- `logging.level`: log level

If you run the bot using your own Rocket.Chat account for local testing, set
`rocketchat.ignore_own_messages: false` so your own `!hc version <alias>` messages are processed.

Environment config example:

```yaml
environments:
  t4:
    name: "Test"
    url: "https://example.com/_hc"
  test4:
    name: "Test"
    url: "https://example.com/_hc"
```

Legacy backward-compatible form is still supported:

```yaml
environments:
  t4: "https://example.com/_hc"
```

Help template example:

```yaml
help:
  template: |
    Available commands:
    {{commands}}

    Available aliases:
    {{aliases}}
```

Booking templates example:

```yaml
messages:
  booking_success: "{{env_name}} is booked by {{username}} for another {{remaining_time}}."
  booking_busy: "{{env_name}} booked by {{username}}. Remaining time: {{remaining_time}}"
  booking_free: "{{env_name}} is free."
  unbooking_success: "{{env_name}} unbooked by {{username}}."
  unbooking_all_success: "All environments are successfully unbooked. Number of affected envs: {{count}}"
  incorrect_alias: "Incorrect alias: {{env_name}}."
  incorrect_or_missing_time: "Incorrect or missing time."
```

## Template variables

Configurable templates are stored in `config/bot_config.yaml` and support placeholders in
the format `{{variable_name}}`.

Template rendering is centralized and consistent across help/booking messages:
- known variables are replaced from context
- missing variables are left as-is (placeholder text remains unchanged)
- implementation is shared in `app/template_renderer.py`

Currently supported variables:
- `{{env_name}}` — human-readable environment name from config (or fallback alias)
- `{{username}}` — Rocket.Chat username of the relevant user
- `{{remaining_time}}` — human-readable remaining booking duration
- `{{time}}` — human-readable booking duration value (alias for duration output)
- `{{count}}` — affected environments count for `!unbook all` (meaningful for `messages.unbooking_all_success`)
- `{{commands}}` — generated list of supported commands in help template
- `{{aliases}}` — generated list of configured aliases in help template

## Required environment variables

The auth token env var referenced by `rocketchat.auth_token_env` must be set.

Default example:

```bash
export ROCKETCHAT_BOT_AUTH_TOKEN="your-token"
```

## Run

```bash
python -m app.main
```

Optional custom config path:

```bash
python -m app.main config/bot_config.yaml
```

## Command example

```text
!hc version dev1
```

```text
!help
```

```text
!book test1 2h
```

```text
!book status test1
```

```text
!unbook test1
```

```text
!unbook all
```

If alias is unknown, bot replies:

```text
Unknown environment alias: <alias>
```

Help command reply example:

```text
Available commands:
!help
!hc version <alias>
!book <alias> <time>
!book status <alias>
!unbook <alias>
!unbook all

Available aliases:
<list of aliases>
```

Successful reply format:

```text
Environment: Test 1
Branch: branch
Commit: hash
[Healthcheck link](https://example.com/_hc)
```

If tag exists:

```text
Environment: Test 1
Branch: branch
Commit: hash
Tag: v1.2.3
[Healthcheck link](https://example.com/_hc)
```

## SQLite data saved

Table: `hc_version_results`

For each handled command, bot saves:
- `alias`
- `url`
- `branch`
- `commit_hash`
- `tag`
- `hc_timestamp`
- `fetched_at`
- `status` (`success` or `error`)
- `error_message` (nullable)

Persistence behavior:
- History is grouped by exact `healthcheck URL` (not alias).
- If latest row for URL has unchanged state (`branch`, `commit_hash`, `tag`, `status`, `error_message`), bot refreshes that row instead of inserting a new one.
- On unchanged state refresh, bot updates `alias`, `hc_timestamp`, and `fetched_at`.
- If state changed for that URL, bot inserts a new history row.

Table: `booking_current`
- Current booking state by URL (single upserted row per URL).
- Fields: `url`, `username`, `status`, `booked_at`, `booked_until`.

Table: `booking_history`
- Append-only successful booking actions.
- Fields: `username`, `url`, `action_status`, `action_time`.
- Automatic expiration cleanup does not write `unbooked` history rows.

## Known limitations

- Realtime subscription currently depends on configured `room_filters`
- Strict command matching only (no fuzzy parsing)
- No message deduplication persistence across process restarts
- Minimal error recovery; failures are logged and loop continues
- No broad schema migration framework (only minimal legacy `commit` -> `commit_hash` compatibility)
