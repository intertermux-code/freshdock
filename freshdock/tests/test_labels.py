import io
import os
import tempfile
import unittest

from freshdock.db import Database
from freshdock.engine import MockDockerClient
from freshdock.notifier import MockNotifier
from freshdock.registry import MockRegistryClient
from freshdock.updater import Updater


class TestLabelPolicies(unittest.TestCase):
    def _fixture(self, labels, notifier=None, notifiers=None):
        handle = tempfile.NamedTemporaryFile(delete=False)
        db_path = handle.name
        handle.close()
        db = Database(db_path)
        db.watch(container="c1", image="nginx:latest", last_digest="sha256:old")
        db.close()

        engine = MockDockerClient()
        engine.set_container("c1", "nginx:latest", health_status="healthy", labels=labels)
        engine.set_next_health("healthy")
        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:new")

        default_notifier = notifier if notifier is not None else MockNotifier()
        notifiers = dict(notifiers or {})
        notifiers.setdefault("ntfy", default_notifier)
        db = Database(db_path)
        updater = Updater(
            db, engine, registry=registry, notifier=default_notifier, notifiers=notifiers
        )
        return db_path, engine, registry, default_notifier, updater, notifiers

    def _close(self, db_path, updater):
        updater.db.close()
        db = Database(db_path)
        history = db.get_history(limit=10)
        watched = db.list_watched()
        db.close()
        os.unlink(db_path)
        return history, watched

    def test_container_without_labels_is_watched(self):
        db_path, engine, registry, default_notifier, updater, notifiers = self._fixture({})
        updater.run_once()
        history, watched = self._close(db_path, updater)
        self.assertTrue(any(h["status"] == "updated" for h in history))
        self.assertEqual(len(default_notifier.sent), 1)

    def test_enable_false_excludes_container(self):
        db_path, engine, registry, default_notifier, updater, notifiers = self._fixture(
            {"com.freshdock.enable": "false"}
        )
        updater.run_once()
        history, watched = self._close(db_path, updater)
        self.assertEqual(history, [])
        self.assertEqual(watched[0]["last_digest"], "sha256:old")
        self.assertEqual(engine.created_with, [])
        self.assertEqual(default_notifier.sent, [])
        # exclusion happens before any registry call
        self.assertEqual(registry._calls, 0)

    def test_enable_true_still_watched(self):
        db_path, engine, registry, default_notifier, updater, notifiers = self._fixture(
            {"com.freshdock.enable": "true"}
        )
        updater.run_once()
        history, watched = self._close(db_path, updater)
        self.assertTrue(any(h["status"] == "updated" for h in history))

    def test_notify_override_sends_via_named_notifier(self):
        default_notifier = MockNotifier()
        webhook = MockNotifier()
        db_path, engine, registry, _, updater, _ = self._fixture(
            {"com.freshdock.notify": "webhook"},
            notifier=default_notifier,
            notifiers={"ntfy": default_notifier, "webhook": webhook},
        )
        updater.run_once()
        history, watched = self._close(db_path, updater)
        self.assertEqual(len(webhook.sent), 1)
        self.assertEqual(webhook.sent[0]["title"], "freshdock: updated")
        self.assertTrue("sha256:new" in webhook.sent[0]["message"])
        self.assertEqual(len(default_notifier.sent), 0)
        created = [image for image, _ in engine.created_with]
        self.assertIn("nginx@sha256:new", created)
        self.assertTrue(any(h["status"] == "updated" for h in history))

    def test_unknown_notify_falls_back_to_default_with_warning(self):
        default_notifier = MockNotifier()
        db_path, engine, registry, _, updater, _ = self._fixture(
            {"com.freshdock.notify": "nope"},
            notifier=default_notifier,
            notifiers={"ntfy": default_notifier},
        )
        err = io.StringIO()
        with unittest.mock.patch("sys.stderr", err):
            updater.run_once()
        self._close(db_path, updater)
        self.assertEqual(len(default_notifier.sent), 1)
        self.assertIn("nope", err.getvalue())
        self.assertIn("falling back to default", err.getvalue())
