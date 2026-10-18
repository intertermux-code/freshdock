import argparse
import json
import sys

from .config import DEFAULT_DB_PATH
from .db import Database
from .engine import DockerEngine, DockerAPIError
from .notifier import NtfyNotifier


def _get_db(args):
    db_path = args.db if hasattr(args, "db") and args.db else DEFAULT_DB_PATH
    return Database(db_path)


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
    notifier = NtfyNotifier(args.ntfy_topic) if args.ntfy_topic else None
    updater = Updater(db, engine, interval=args.interval,
                      health_timeout=args.health_timeout, notifier=notifier)
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
    p_run.add_argument("--socket", type=str, default="/var/run/docker.sock",
                       help="Docker Engine unix socket (default: /var/run/docker.sock)")
    p_run.add_argument("--db", type=str, help="Path to SQLite DB")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return
    args.func(args)
