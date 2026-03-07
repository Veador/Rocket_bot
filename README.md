# Rocket.Chat HC Bot PoC

Minimal Python bot PoC for Rocket.Chat.

## What the bot does

- Connects to Rocket.Chat realtime websocket (DDP)
- Listens for new messages in configured rooms
- Ignores bot's own messages by default (configurable)
- Processes only root command messages (thread replies are ignored)
- Processes only messages starting with `!hc`
- Supports `!hc help`
- Supports `!hc version <alias>`
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

- No command set beyond `!hc help` and `!hc version <alias>`
- No Docker setup
- No tests
- No metrics/monitoring
- No retries/circuit breaker for `healthcheck URL` requests
- No advanced permission model

## Project structure

```text
app/
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

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
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
- `commands.hc_help`: help command text (default `!hc help`)
- `help.template`: help reply template with placeholders `{{commands}}` and `{{aliases}}`
- `environments`: alias -> environment mapping (`name` + `url`)
- `database.sqlite_path`: SQLite file path
- `logging.level`: log level

If you run the bot using your own Rocket.Chat account for local PoC testing, set
`rocketchat.ignore_own_messages: false` so your own `!hc version <alias>` messages are processed.

Environment config example:

```yaml
environments:
  t4:
    name: "Test"
    url: "https://example.com/healthcheck"
  test4:
    name: "Test"
    url: "https://example.com/healthcheck"
```

Legacy backward-compatible form is still supported:

```yaml
environments:
  t4: "https://example.com/healthcheck"
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
!hc help
```

If alias is unknown, bot replies:

```text
Unknown environment alias: <alias>
```

Help command reply example:

```text
Available commands:
!hc help
!hc version <alias>

Available aliases:
<list of aliases>
```

Successful reply format:

```text
Environment: Test
Branch: branch
Commit: hash
[Healthcheck link](https://example.com/healthcheck)
```

If tag exists:

```text
Environment: Test
Branch: branch
Commit: hash
Tag: v1.2.3
[Healthcheck link](https://example.com/healthcheck)
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

## Known PoC limitations

- Realtime subscription currently depends on configured `room_filters`
- Strict command matching only (no fuzzy parsing)
- No message deduplication persistence across process restarts
- Minimal error recovery; failures are logged and loop continues
- No broad schema migration framework (only minimal legacy `commit` -> `commit_hash` compatibility)
