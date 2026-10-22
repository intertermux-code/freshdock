# freshdock

freshdock is a lightweight Docker auto-updater focused on safety and predictability. It fills the space left by container update tools by keeping things simple: watch containers/images, check registries for new digests, update containers with health-gated rollout, and rollback on failure.

## What it is and isn't

- **What it is**: A minimal updater with stdlib-only Python code that talks to the Docker Engine API via Unix socket, polls registries with token-aware manifest checks and caching, performs health-gated updates with rollback, and records state in SQLite.
- **Not watchtower**: This is not a fork or rebrand of Watchtower; it's a fresh implementation with its own design.
- **vs WUD**: WUD focuses on notifications and per-container configuration; freshdock aims for a simpler health-gated update flow.
- **vs community forks**: freshdock avoids unnecessary dependencies and keeps the update path explicit and testable.

## Quick Start

1. Install Python 3.9+.
2. Clone the repo and run from source:

```bash
python -m freshdock --help
```

3. Watch a container and run once:

```bash
python -m freshdock watch --container <container_name>
python -m freshdock run --once
```

4. Run as a daemon with interval:

```bash
python -m freshdock run --interval 300
```

5. See what is watched, and stop watching something:

```bash
python -m freshdock list
python -m freshdock remove --container <container_name>
```

6. View history:

```bash
python -m freshdock history
```

## Rollback guarantee

When a digest change is detected, freshdock inspects the running container,
pulls the new image pinned to its digest (`image@sha256:...`), and recreates
the container with the same name and runtime config (env, command, volumes,
port bindings, restart policy). It then waits for the container's healthcheck
(`--health-timeout`, default 60s; containers without a healthcheck count as
healthy once running). If the new container reports unhealthy or the wait
times out, freshdock removes it and recreates the container pinned to the
previous known-good digest — not the floating tag, so a broken registry
state can't sabotage the restore. Every update, rollback, and digest
observation is recorded in SQLite for auditability.

## Configuration

- `--socket`: Docker Engine unix socket (default: `/var/run/docker.sock`)
- `--db`: SQLite database path (default: `freshdock.db`)
- `--interval`: seconds between checks in daemon mode (default: `300`)
- `--health-timeout`: seconds to wait for healthy after an update (default: `60`)
- `--ntfy-topic`: ntfy topic for update/rollback notifications; without it, the
  default notifier is a single configured webhook/discord URL, otherwise
  unlabelled containers are silent unless `--ntfy-topic` is set
- `--webhook-url`: webhook URL for update notifications (env: `FRESHDOCK_WEBHOOK_URL`)
- `--discord-url`: Discord incoming webhook URL for update notifications (env: `FRESHDOCK_DISCORD_URL`)
- `--dry-run`: detect and report changes without pulling, recreating, notifying, or writing state
- No config file yet; everything is CLI flags or environment variables. Do not commit your `freshdock.db`.

## Notifiers

Three notifiers are available; pick them with flags or environment variables.
Without any notifier configured, `run` is silent.

- **ntfy** (`--ntfy-topic` / env: none): POSTs `title` and `message` to
  `https://ntfy.sh/<topic>`.
- **webhook** (`--webhook-url` / env: `FRESHDOCK_WEBHOOK_URL`): POSTs a JSON
  payload `{"title": ..., "body": ..., "fields": {...}}` to the configured URL.
  Example payload:

  ```json
  {"title": "freshdock: updated", "body": "web updated to sha256:abcd1234...", "fields": {}}
  ```

- **discord** (`--discord-url` / env: `FRESHDOCK_DISCORD_URL`): POSTs a minimal
  rich embed (title, description, fields) to a Discord incoming webhook URL.
  Example payload:

  ```json
  {"embeds": [{"title": "freshdock: updated", "description": "web updated to sha256:abcd1234...", "fields": []}]}
  ```

All notifiers use a 10s request timeout, a single attempt (no retry), and log
failures to stderr without aborting the run.

## Compose-label policies

freshdock reads Docker container labels from the Engine API container
inspection:

- `com.freshdock.enable`: set to `"false"` to exclude a container from
  watching (default: watched).
- `com.freshdock.notify`: override the default notifier for this container
  (e.g. `"webhook"`). The name must match a configured notifier; an unknown
  name falls back to the default with a stderr warning.

Label values must be strings (quote them in Compose files).

Example Compose file:

```yaml
services:
  web:
    image: nginx:latest
    labels:
      com.freshdock.enable: "true"
      com.freshdock.notify: "webhook"
  worker:
    image: redis:latest
    labels:
      com.freshdock.enable: "false"
```

Here `web` is watched and notified via the webhook notifier, while `worker` is
excluded from watching entirely.

## Dry-run mode

`--dry-run` works with both `--once` and `--interval`. The full detection
pipeline runs (digest checks against the registry) but nothing is pulled, no
container is recreated, no notifier is called, and no state is written. The
plan is printed to stdout:

```
freshdock [dry-run] web: nginx:latest sha256:old -> sha256:new
  plan: recreate web from nginx@sha256:new
  preserve: env=2; volumes=/data; ports=80/tcp; port bindings=0.0.0.0:80->80/tcp
  notify: freshdock: updated / web updated to sha256:abcd... (notifier: webhook)
  rollback target: nginx@sha256:old if unhealthy
```

Exit codes are unchanged: 0 on success, 1 on Docker API error, 2 on usage
error.

## Limitations

- PyPI publishing (packaging metadata ships, but no release workflow yet)

## Testing

Run the full test suite:

```bash
python -m unittest discover -v
```
