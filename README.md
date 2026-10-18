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

5. View history:

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
- `--ntfy-topic`: ntfy topic for update/rollback notifications (unset = silent)
- No config file yet; everything is CLI flags. Do not commit your `freshdock.db`.

## Limitations (Run A)

Run A deliberately leaves out certain features that may come in later runs:
- Compose-label policies (label-driven configuration)
- SMTP/webhook notifiers (only ntfy is included)
- Dry-run mode
- PyPI publishing (packaging metadata ships, but no release workflow yet)

## Testing

Run the full test suite:

```bash
python -m unittest discover -v
```
