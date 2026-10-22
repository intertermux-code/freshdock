import io
import os
import tempfile
import unittest

from freshdock.db import Database
from freshdock.engine import MockDockerClient
from freshdock.notifier import MockNotifier
from freshdock.registry import MockRegistryClient
from freshdock.updater import Updater


class _RecordingEngine(MockDockerClient):
    def __init__(self):
        super().__init__()
        self.pulled = []

    def pull(self, image):
        self.pulled.append(image)
        return {"status": "downloaded"}


class TestDryRun(unittest.TestCase):
    def _fixture(self, labels=None):
        handle = tempfile.NamedTemporaryFile(delete=False)
        db_path = handle.name
        handle.close()
        db = Database(db_path)
        db.watch(container="c1", image="nginx:latest", last_digest="sha256:old")
        db.close()

        engine = _RecordingEngine()
        engine.set_container("c1", "nginx:latest", health_status="healthy", labels=labels or {})
        engine.set_next_health("healthy")
        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:new")
        notifier = MockNotifier()
        db = Database(db_path)
        updater = Updater(
            db,
            engine,
            registry=registry,
            notifier=notifier,
            notifiers={"ntfy": notifier},
            dry_run=True,
        )
        return db_path, engine, registry, notifier, updater

    def _close(self, db_path, updater):
        updater.db.close()
        db = Database(db_path)
        history = db.get_history(limit=10)
        watched = db.list_watched()
        cached = db.get_manifest_cache("nginx:latest")
        db.close()
        os.unlink(db_path)
        return history, watched, cached

    def test_dry_run_emits_plan_without_side_effects(self):
        db_path, engine, registry, notifier, updater = self._fixture()
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            updater.run_once()
        history, watched, cached = self._close(db_path, updater)

        output = out.getvalue()
        self.assertIn("freshdock [dry-run] c1: nginx:latest sha256:old -> sha256:new", output)
        self.assertIn("plan: recreate c1 from nginx@sha256:new", output)
        self.assertIn("preserve:", output)
        self.assertIn("notify: freshdock: updated / c1 updated to sha256:new (notifier: default)", output)
        self.assertIn("rollback target: nginx@sha256:old if unhealthy", output)

        self.assertEqual(engine.pulled, [])
        self.assertEqual(engine.created_with, [])
        self.assertEqual(notifier.sent, [])
        self.assertEqual(history, [])
        self.assertEqual(watched[0]["last_digest"], "sha256:old")
        self.assertIsNone(cached)

    def test_dry_run_reports_labelled_notifier(self):
        db_path, engine, registry, default_notifier, updater = self._fixture(
            labels={"com.freshdock.notify": "webhook"}
        )
        webhook = MockNotifier()
        updater.notifiers["webhook"] = webhook
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            updater.run_once()
        self._close(db_path, updater)

        self.assertIn("(notifier: webhook)", out.getvalue())
        self.assertEqual(webhook.sent, [])
        self.assertEqual(default_notifier.sent, [])

    def test_dry_run_image_entry_reports_digest_without_writing(self):
        handle = tempfile.NamedTemporaryFile(delete=False)
        db_path = handle.name
        handle.close()
        db = Database(db_path)
        db.watch(image="nginx:latest", last_digest="sha256:old")
        db.close()

        db = Database(db_path)
        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:new")
        updater = Updater(db, MockDockerClient(), registry=registry, dry_run=True)
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            updater.run_once()
        updater.db.close()

        self.assertIn("freshdock [dry-run] nginx:latest: digest sha256:old -> sha256:new (would record)", out.getvalue())

        db = Database(db_path)
        self.assertEqual(db.get_history(limit=10), [])
        self.assertEqual(db.list_watched()[0]["last_digest"], "sha256:old")
        self.assertIsNone(db.get_manifest_cache("nginx:latest"))
        db.close()
        os.unlink(db_path)
