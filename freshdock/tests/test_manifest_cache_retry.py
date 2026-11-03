import os
import tempfile
import unittest

from freshdock.db import Database
from freshdock.engine import DockerAPIError, MockDockerClient
from freshdock.registry import MockRegistryClient
from freshdock.updater import Updater


class TestManifestCacheRetry(unittest.TestCase):
    def _setup(self, fail_create=False):
        fd, db_path = tempfile.mkstemp()
        os.close(fd)
        os.unlink(db_path)
        db = Database(db_path)
        db.watch(container="c1", image="nginx:latest", last_digest="sha256:old")
        db.close()

        engine = MockDockerClient()
        engine.set_container("c1", "nginx:latest", health_status="healthy")
        if fail_create:
            engine.create_container = lambda image, **kw: (_ for _ in ()).throw(DockerAPIError("boom"))
        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:new")
        engine.set_next_health("healthy")

        db = Database(db_path)
        updater = Updater(db, engine, registry=registry)
        return db_path, db, updater

    def test_failed_create_leaves_pending(self):
        db_path, db, updater = self._setup(fail_create=True)
        updater.run_once()
        db.close()
        db = Database(db_path)
        cached = db.get_manifest_cache("nginx:latest")
        watched = db.list_watched()[0]
        db.close()
        self.assertEqual(watched["last_digest"], "sha256:old")
        self.assertNotEqual(cached["digest"] if cached else None, "sha256:new")
        # second run still sees update pending -> attempts create again
        # easier: set engine to a working one
        engine2 = MockDockerClient()
        engine2.set_container("c1", "nginx:latest", health_status="healthy")
        engine2.set_next_health("healthy")
        db = Database(db_path)
        registry = MockRegistryClient()
        registry.set_digest("nginx:latest", "sha256:new")
        updater2 = Updater(db, engine2, registry=registry)
        updater2.run_once()
        db.close()
        db = Database(db_path)
        cached = db.get_manifest_cache("nginx:latest")
        watched = db.list_watched()[0]
        db.close()
        self.assertEqual(watched["last_digest"], "sha256:new")
        self.assertEqual(cached["digest"], "sha256:new")
        os.unlink(db_path)

    def test_success_advances_cache(self):
        db_path, db, updater = self._setup()
        updater.run_once()
        db.close()
        db = Database(db_path)
        cached = db.get_manifest_cache("nginx:latest")
        watched = db.list_watched()[0]
        db.close()
        self.assertEqual(watched["last_digest"], "sha256:new")
        self.assertEqual(cached["digest"], "sha256:new")
        os.unlink(db_path)

    def test_rollback_leaves_old_digest(self):
        db_path, db, updater = self._setup()
        # make health fail -> rollback
        updater_engine = updater.engine
        updater_engine.set_next_health("unhealthy")
        updater.run_once()
        db.close()
        db = Database(db_path)
        cached = db.get_manifest_cache("nginx:latest")
        watched = db.list_watched()[0]
        db.close()
        self.assertEqual(watched["last_digest"], "sha256:old")
        self.assertTrue(cached is None or cached["digest"] != "sha256:new")
        os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
