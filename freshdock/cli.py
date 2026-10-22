import argparse
import json
import os
import sys

from .config import DEFAULT_DB_PATH
from .db import Database
from .engine import DockerEngine, DockerAPIError
from .notifier import NtfyNotifier, WebhookNotifier, DiscordNotifier


def _get_db(args):
    db_path = args.db if hasattr(args, "db") and args.db else DEFAULT_DB_PATH
    return Database(db_path)


def _default_notifier(notifiers):
    """Resolve the default notifier from the configured notifiers dict.

    1. ntfy if --ntfy-topic was given, else
    2. the single configured webhook/discord notifier if exactly one, else
    3. None (multiple configured but no ntfy: unlabelled containers are silent
       unless --ntfy-topic is set).
    """
    if "ntfy" in notifiers:
        return notifiers["ntfy"]
    others = [name for name in ("webhook", "discord") if name in notifiers]
    if len(others) == 1:
        return notifiers[others[0]]
    if others:
        print(
            f"freshdock: notifiers {', '.join(others)} configured without "
            f"--ntfy-topic; unlabelled containers will not be notified unless "
            f"--ntfy-topic is set",
            file=sys.stderr,
        )
    return None


def cmd_watch(args):
    db = _get_db(args)
    container = args.container
    image = args.image
    if not container and not image:
        print("Error: either --container or --image is required", file=sys.stderr)
        sys.exit(1)
    try:
        db.watch(container=container, image=image)
    finally:
        db.close()
    print("Watched")


def cmd_list(args):
    db = _get_db(args)
    try:
        entries = db.list_watched()
    finally:
        db.close()
    if not entries:
        print("No watched entries")
        return
    for e in entries:
        parts = []
        if e.get("container"):
            parts.append(f"container={e['container']}")
        if e.get("image"):
            parts.append(f"image={e['image']}")
        if e.get("last_digest"):
            parts.append(f"digest={e['last_digest']}")
        print(" ".join(parts))


def cmd_remove(args):
    db = _get_db(args)
    try:
        db.remove(container=args.container, image=args.image)
    finally:
        db.close()
    print("Removed")


def cmd_history(args):
    db = _get_db(args)
    try:
        events = db.get_history(limit=args.limit)
    finally:
        db.close()
    if not events:
        print("No history")
        return
    for ev in events:
        print(json.dumps(ev))


def cmd_run(args):
    from .updater import Updater

    db = _get_db(args)
    engine = DockerEngine(unix_socket=args.socket)

    notifiers = {}
    if args.ntfy_topic:
        notifiers["ntfy"] = NtfyNotifier(args.ntfy_topic)
    webhook_url = args.webhook_url or os.environ.get("FRESHDOCK_WEBHOOK_URL")
    if webhook_url:
        notifiers["webhook"] = WebhookNotifier(webhook_url)
    discord_url = args.discord_url or os.environ.get("FRESHDOCK_DISCORD_URL")
    if discord_url:
        notifiers["discord"] = DiscordNotifier(discord_url)

    updater = Updater(db, engine, interval=args.interval,
                      health_timeout=args.health_timeout, notifier=_default_notifier(notifiers),
                      notifiers=notifiers, dry_run=args.dry_run)
    try:
        if args.once:
            updater.run_once()
        else:
            updater.run_forever()
    except DockerAPIError as e:
        print(f"Error: {e}", file=sys.stderr)
        db.close()
        sys.exit(1)
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="freshdock")
    sub = parser.add_subparsers(dest="cmd")

    p_watch = sub.add_parser("watch", help="Watch a container or image")
    p_watch.add_argument("--container", type=str, help="Container name/ID")
    p_watch.add_argument("--image", type=str, help="Image reference")
    p_watch.add_argument("--db", type=str, help="Path to SQLite DB")
    p_watch.set_defaults(func=cmd_watch)

    p_list = sub.add_parser("list", help="List watched entries")
    p_list.add_argument("--db", type=str, help="Path to SQLite DB")
    p_list.set_defaults(func=cmd_list)

    p_remove = sub.add_parser("remove", help="Remove a watched entry")
    p_remove.add_argument("--container", type=str, help="Container name/ID")
    p_remove.add_argument("--image", type=str, help="Image reference")
    p_remove.add_argument("--db", type=str, help="Path to SQLite DB")
    p_remove.set_defaults(func=cmd_remove)

    p_history = sub.add_parser("history", help="Show update history")
    p_history.add_argument("--limit", type=int, default=20)
    p_history.add_argument("--db", type=str, help="Path to SQLite DB")
    p_history.set_defaults(func=cmd_history)

    p_run = sub.add_parser("run", help="Run checks")
    p_run.add_argument("--once", action="store_true", help="Check once and exit")
    p_run.add_argument("--interval", type=int, default=300,
                       help="Seconds between checks in daemon mode (default: 300)")
    p_run.add_argument("--health-timeout", type=int, default=60,
                       help="Seconds to wait for a container to report healthy after an update (default: 60)")
    p_run.add_argument("--ntfy-topic", type=str, default=None,
                       help="ntfy topic for update/rollback notifications (e.g. myserver-freshdock)")
    p_run.add_argument("--webhook-url", type=str, default=None,
                       help="webhook URL for update notifications (env: FRESHDOCK_WEBHOOK_URL)")
    p_run.add_argument("--discord-url", type=str, default=None,
                       help="Discord incoming webhook URL for update notifications (env: FRESHDOCK_DISCORD_URL)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="dry run: detect and report changes without pulling, recreating, notifying, or writing state")
    p_run.add_argument("--socket", type=str, default="/var/run/docker.sock",
                       help="Docker Engine unix socket (default: /var/run/docker.sock)")
    p_run.add_argument("--db", type=str, help="Path to SQLite DB")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return
    args.func(args)
