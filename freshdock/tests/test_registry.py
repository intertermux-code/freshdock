import os
import tempfile
import unittest

from freshdock.db import Database
from freshdock.registry import MockRegistryClient, RateLimitedError


class TestRegistry(unittest.TestCase):
    def test_caching_unchanged_manifests(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            db = Database(db_path)
            mock = MockRegistryClient()
            mock.set_digest("nginx:latest", "sha256:abc")
            resp, digest, etag = mock.get_manifest("nginx:latest")
            db.cache_manifest("nginx:latest", digest, etag)
            cached = db.get_manifest_cache("nginx:latest")
            db.close()
            self.assertIsNotNone(cached)
            self.assertEqual(cached["digest"], "sha256:abc")
        finally:
            os.unlink(db_path)

    def test_rate_limit_triggers_backoff(self):
        mock = MockRegistryClient()
        mock.set_rate_limited(True)
        with self.assertRaises(RateLimitedError):
            mock.get_manifest("nginx:latest")
