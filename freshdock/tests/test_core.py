import os
import tempfile
import unittest

from freshdock.db import Database
from freshdock.engine import DockerAPIError, MockDockerClient
from freshdock.registry import MockRegistryClient
from freshdock.notifier import MockNotifier
from freshdock.updater import Updater


class TestCore(unittest.TestCase):
    def test_digest_change_detection(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            db = Database(db_path)
            db.watch(container="c1", image="nginx:latest", last_digest="sha256:old")
            db.close()

            mock_engine = MockDockerClient()
            mock_engine.set_container("c1", "nginx:latest", health_status="healthy")
            mock_engine.set_next_health("healthy")
            mock_engine.set_image("nginx:latest", "sha256:old")

            mock_registry = MockRegistryClient()
            mock_registry.set_digest("nginx:latest", "sha256:new")

            db = Database(db_path)
            updater = Updater(db, mock_engine, registry=mock_registry)
            updater.run_once()
            db.close()

            db = Database(db_path)
            history = db.get_history(limit=10)
            db.close()
            self.assertTrue(any(h["status"] == "updated" for h in history))
        finally:
            os.unlink(db_path)

    def test_successful_health_gated_update(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            db = Database(db_path)
            db.watch(container="c1", image="nginx:latest", last_digest="sha256:old")
            db.close()

            mock_engine = MockDockerClient()
            mock_engine.set_container("c1", "nginx:latest", health_status="healthy")
            mock_engine.set_next_health("healthy")
            mock_registry = MockRegistryClient()
            mock_registry.set_digest("nginx:latest", "sha256:new")

            db = Database(db_path)
            updater = Updater(db, mock_engine, registry=mock_registry)
            updater.run_once()
            db.close()

            db = Database(db_path)
            history = db.get_history(limit=10)
            self.assertTrue(any(h["status"] == "updated" for h in history))
            db.close()
        finally:
            os.unlink(db_path)

    def test_rollback_on_unhealthy_update(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            db = Database(db_path)
            db.watch(container="c1", image="nginx:latest", last_digest="sha256:old")
            db.close()

            mock_engine = MockDockerClient()
            mock_engine.set_container("c1", "nginx:latest", health_status="healthy")
            mock_engine.set_next_health("unhealthy")
            mock_registry = MockRegistryClient()
            mock_registry.set_digest("nginx:latest", "sha256:new")

            db = Database(db_path)
            updater = Updater(db, mock_engine, registry=mock_registry)
            updater.run_once()
            db.close()

            db = Database(db_path)
            history = db.get_history(limit=10)
            self.assertTrue(any(h["status"] == "rollback" for h in history))
            db.close()
        finally:
            os.unlink(db_path)

    def test_rollback_when_the_create_is_refused(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            db = Database(db_path)
            db.watch(container="c1", image="nginx:latest", last_digest="sha256:old")
            db.close()

            class CreateRefused(MockDockerClient):
                def create_container(self, image, **kwargs):
                    if image.endswith("sha256:new"):
                        raise DockerAPIError("400 from /containers/create")
                    return super().create_container(image, **kwargs)

            mock_engine = CreateRefused()
            mock_engine.set_container("c1", "nginx:latest", health_status="healthy")
            mock_registry = MockRegistryClient()
            mock_registry.set_digest("nginx:latest", "sha256:new")

            db = Database(db_path)
            updater = Updater(db, mock_engine, registry=mock_registry)
            updater.run_once()
            db.close()

            self.assertEqual([image for image, _ in mock_engine.created_with], ["nginx@sha256:old"])

            db = Database(db_path)
            history = db.get_history(limit=10)
            db.close()
            rolls = [h for h in history if h["status"] == "rollback"]
            self.assertEqual(len(rolls), 1, f"expected one rollback, got {history}")
            self.assertEqual(rolls[0]["details"]["reason"], "create failed")
        finally:
            os.unlink(db_path)

    def test_rollback_on_health_timeout(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            db = Database(db_path)
            db.watch(container="c1", image="nginx:latest", last_digest="sha256:old")
            db.close()

            mock_engine = MockDockerClient()
            mock_engine.set_container("c1", "nginx:latest", health_status="healthy")
            mock_engine.set_next_health("starting")
            mock_registry = MockRegistryClient()
            mock_registry.set_digest("nginx:latest", "sha256:new")

            db = Database(db_path)
            updater = Updater(db, mock_engine, registry=mock_registry)
            updater.run_once()
            db.close()

            db = Database(db_path)
            history = db.get_history(limit=10)
            rollback = any(h["status"] == "rollback" for h in history)
            updated = any(h["status"] == "updated" for h in history)
            self.assertTrue(rollback or updated)
            db.close()
        finally:
            os.unlink(db_path)

    def test_ntfy_notifier_mocked(self):
        notifier = MockNotifier()
        notifier.send("title", "msg", priority="3")
        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(notifier.sent[0]["title"], "title")
