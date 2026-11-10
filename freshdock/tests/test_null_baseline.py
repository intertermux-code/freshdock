import os
import tempfile
import unittest

from freshdock.db import Database
from freshdock.engine import MockDockerClient
from freshdock.registry import MockRegistryClient
from freshdock.updater import Updater


class TestNullBaseline(unittest.TestCase):
    """Tests for NULL baseline digest handling.

    A missing baseline (last_digest=NULL) means "unknown", not "changed".
    The update decision resolves the running image's manifest digest through
    the image inspect endpoint (RepoDigests) and compares it to the registry's
    manifest digest. Container inspect's top-level Image is the image config
    digest, which never equals a registry manifest digest, so it must not be
    compared directly.
    """

    def _make_db(self, container="c1", image="nginx:latest", last_digest=None):
        handle = tempfile.NamedTemporaryFile(delete=False)
        db_path = handle.name
        handle.close()
        db = Database(db_path)
        db.watch(container=container, image=image, last_digest=last_digest)
        db.close()
        return db_path

    def _run_updater(self, db_path, engine, registry):
        db = Database(db_path)
        updater = Updater(db, engine, registry=registry)
        updater.run_once()
        db.close()
        return db_path

    def _check_result(self, db_path, expected_digest, expected_history_statuses, expected_recreations):
        db = Database(db_path)
        watched = db.list_watched()[0]
        history = db.get_history(limit=10)
        db.close()
        os.unlink(db_path)

        self.assertEqual(watched["last_digest"], expected_digest)
        actual_statuses = [h["status"] for h in history]
        self.assertEqual(actual_statuses, expected_history_statuses)
        return watched, history

    def test_null_baseline_container_already_current_no_recreate(self):
        """NULL baseline + container at latest manifest digest -> no recreate, baseline recorded."""
        db_path = self._make_db(last_digest=None)

        engine = MockDockerClient()
        engine.set_container("c1", "nginx:latest", health_status="healthy")
        # Real Docker: Config.Image is the image name, top-level Image is the
        # config digest (different from the registry manifest digest).
        engine._containers["c1"]["Config"]["Image"] = "nginx:latest"
        engine._containers["c1"]["Image"] = "sha256:configdigest111"
        engine.set_next_health("healthy")
        # The image's RepoDigests carry the registry manifest digest.
        engine.set_image("nginx:latest", "sha256:manifestdigest222")

        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:manifestdigest222")

        self._run_updater(db_path, engine, registry)
        watched, history = self._check_result(
            db_path,
            expected_digest="sha256:manifestdigest222",
            expected_history_statuses=["baseline"],
            expected_recreations=0,
        )
        self.assertEqual(len(engine.created_with), 0, "container should not be recreated")

    def test_null_baseline_container_stale_updates(self):
        """NULL baseline + container at older manifest digest -> update happens."""
        db_path = self._make_db(last_digest=None)

        engine = MockDockerClient()
        engine.set_container("c1", "nginx:latest", health_status="healthy")
        engine._containers["c1"]["Config"]["Image"] = "nginx:latest"
        engine._containers["c1"]["Image"] = "sha256:oldconfig"
        engine.set_next_health("healthy")
        engine.set_image("nginx:latest", "sha256:oldmanifest")

        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:new")

        self._run_updater(db_path, engine, registry)
        watched, history = self._check_result(
            db_path,
            expected_digest="sha256:new",
            expected_history_statuses=["updated"],
            expected_recreations=1,
        )
        self.assertEqual(len(engine.created_with), 1, "container should be recreated")
        self.assertEqual(engine.created_with[0][0], "nginx@sha256:new")

    def test_recorded_baseline_same_digest_no_update(self):
        """Recorded baseline + same digest -> no update (regression guard)."""
        db_path = self._make_db(last_digest="sha256:current")

        engine = MockDockerClient()
        engine.set_container("c1", "nginx:latest", health_status="healthy")
        engine._containers["c1"]["Config"]["Image"] = "nginx:latest"
        engine._containers["c1"]["Image"] = "sha256:currentconfig"
        engine.set_next_health("healthy")

        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:current")

        self._run_updater(db_path, engine, registry)
        watched, history = self._check_result(
            db_path,
            expected_digest="sha256:current",
            expected_history_statuses=[],
            expected_recreations=0,
        )
        self.assertEqual(len(engine.created_with), 0)

    def test_recorded_baseline_digest_updates(self):
        """Recorded baseline + different digest -> update (regression guard)."""
        db_path = self._make_db(last_digest="sha256:old")

        engine = MockDockerClient()
        engine.set_container("c1", "nginx:latest", health_status="healthy")
        engine._containers["c1"]["Config"]["Image"] = "nginx:latest"
        engine._containers["c1"]["Image"] = "sha256:oldconfig"
        engine.set_next_health("healthy")

        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:new")

        self._run_updater(db_path, engine, registry)
        watched, history = self._check_result(
            db_path,
            expected_digest="sha256:new",
            expected_history_statuses=["updated"],
            expected_recreations=1,
        )
        self.assertEqual(len(engine.created_with), 1)
        self.assertEqual(engine.created_with[0][0], "nginx@sha256:new")

    def test_null_baseline_container_gone_falls_back_to_image(self):
        """NULL baseline + container gone (DockerAPIError) -> update skipped, no baseline recorded."""
        db_path = self._make_db(last_digest=None)

        engine = MockDockerClient()
        # Don't set container - inspect will raise DockerAPIError
        engine.set_next_health("healthy")

        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:new")

        self._run_updater(db_path, engine, registry)
        db = Database(db_path)
        watched = db.list_watched()[0]
        history = db.get_history(limit=10)
        db.close()
        os.unlink(db_path)

        # Container gone: update is skipped, baseline not recorded
        self.assertIsNone(watched["last_digest"])
        self.assertEqual(history, [])
        self.assertEqual(len(engine.created_with), 0)

    def test_null_baseline_manifest_cache_no_false_early_return(self):
        """NULL baseline + matching manifest cache -> does NOT early return, checks running digest."""
        db_path = self._make_db(last_digest=None)

        # Pre-populate manifest cache
        db = Database(db_path)
        db.cache_manifest("nginx:latest", "sha256:latest", '"sha256:latest"')
        db.close()

        engine = MockDockerClient()
        engine.set_container("c1", "nginx:latest", health_status="healthy")
        engine._containers["c1"]["Config"]["Image"] = "nginx:latest"
        engine._containers["c1"]["Image"] = "sha256:configdigest111"
        engine.set_next_health("healthy")
        engine.set_image("nginx:latest", "sha256:latest")

        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:latest")

        self._run_updater(db_path, engine, registry)
        watched, history = self._check_result(
            db_path,
            expected_digest="sha256:latest",
            expected_history_statuses=["baseline"],
            expected_recreations=0,
        )
        self.assertEqual(len(engine.created_with), 0, "manifest cache should not cause early return for NULL baseline")

    def test_null_baseline_container_created_by_freshdock_resolves_via_repo_digests(self):
        """NULL baseline + container freshdock created (Image = pinned ref) -> baseline via RepoDigests, no recreate."""
        db_path = self._make_db(last_digest=None)

        engine = MockDockerClient()
        engine.set_container("c1", "nginx@sha256:new", health_status="healthy")
        engine._containers["c1"]["Config"]["Image"] = "nginx@sha256:new"
        engine._containers["c1"]["Image"] = "nginx@sha256:new"
        engine.set_next_health("healthy")
        engine.set_image("nginx@sha256:new", "sha256:new")

        registry = MockRegistryClient()
        registry.set_digest("nginx@sha256:new", "sha256:new")

        self._run_updater(db_path, engine, registry)
        watched, history = self._check_result(
            db_path,
            expected_digest="sha256:new",
            expected_history_statuses=["baseline"],
            expected_recreations=0,
        )
        self.assertEqual(len(engine.created_with), 0, "container should not be recreated")


if __name__ == "__main__":
    unittest.main()
