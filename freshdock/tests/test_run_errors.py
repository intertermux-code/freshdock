import argparse
import contextlib
import io
import os
import tempfile
import unittest

from freshdock import cli
from freshdock.db import Database
from freshdock.engine import DockerAPIError, MockDockerClient
from freshdock.registry import MockRegistryClient
from freshdock.updater import Updater


class DockerAPIRegistry(MockRegistryClient):
    """Registry client whose manifest check raises a Docker API error."""

    def get_manifest(self, image, accept=None):
        raise DockerAPIError(f"docker API GET /v2/{image}/manifests/latest -> HTTP 502: bad gateway")


class BoomEngine(MockDockerClient):
    """Engine that blows up inspecting a container with a non-Docker error."""

    def inspect_container(self, container_id):
        raise ValueError("boom")


class TestRunDockerAPIError(unittest.TestCase):
    """Docker API errors escaping the run path must exit 1 cleanly;

    per-entry failures stay logged and non-fatal; success still exits 0.
    """

    def _make_db(self, tmp, container=None, image="nginx:latest",
                 last_digest="sha256:old"):
        db_path = os.path.join(tmp, "freshdock.db")
        db = Database(db_path)
        if container is not None:
            db.watch(container=container, image=image, last_digest=last_digest)
        db.close()
        return db_path

    def _invoke(self, argv):
        previous = os.getcwd()
        tmp = tempfile.mkdtemp()
        os.chdir(tmp)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = None
                try:
                    cli.main(argv)
                except SystemExit as e:
                    code = e.code
            return code, err.getvalue(), out.getvalue()
        finally:
            os.chdir(previous)

    def test_docker_api_error_from_run_propagates_instead_of_being_swallowed(self):
        """A Docker API error raised in _check_entry must escape run_once()

        instead of being swallowed and logged by the blanket except in
        _check_all. Fails on pristine HEAD (swallowed -> exit 0).
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._make_db(tmp, container="c1", image="nginx:latest")
            db = Database(db_path)
            engine = MockDockerClient()
            engine.set_container("c1", "nginx:latest")
            registry = DockerAPIRegistry()
            updater = Updater(db, engine, registry=registry)
            try:
                with self.assertRaises(DockerAPIError):
                    updater.run_once()
            finally:
                db.close()

    def test_per_entry_failure_remains_logged_not_fatal(self):
        """A non-DockerAPIError failure in one entry is logged, not fatal;

        run_once() completes and no exception propagates.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._make_db(tmp, container="c1", image="nginx:latest")
            db = Database(db_path)
            engine = BoomEngine()
            engine.set_container("c1", "nginx:latest")
            registry = MockRegistryClient()
            registry.set_digest("nginx:latest", "sha256:old")
            updater = Updater(db, engine, registry=registry)
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err):
                    updater.run_once()
            finally:
                db.close()
            self.assertIn("check failed for c1: boom", err.getvalue())

    def test_successful_run_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._make_db(tmp)
            code, err, _ = self._invoke(["run", "--once", "--db", db_path])
            self.assertIsNone(code, f"expected exit 0, got {code}")
            self.assertNotIn("Error", err)

    def test_docker_api_error_from_run_exits_one_clean_message(self):
        """cli.py maps a Docker API error from the run path to exit 1 with a
        clean one-line stderr message and no traceback."""
        import freshdock.updater as updater_module

        previous_engine = cli.DockerEngine
        previous_updater = updater_module.Updater

        class SilentEngine:
            def __init__(self, *a, **k):
                pass

        class FailingUpdater:
            def __init__(self, *a, **k):
                pass

            def run_once(self):
                raise DockerAPIError(
                    "docker API GET /containers/c1/json -> HTTP 500: "
                    "container c1 not found"
                )

        cli.DockerEngine = SilentEngine
        updater_module.Updater = FailingUpdater
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_path = self._make_db(tmp, container="c1", image="nginx:latest")
                ns = argparse.Namespace(
                    cmd="run", once=True, interval=300, health_timeout=60,
                    ntfy_topic=None, webhook_url=None, discord_url=None,
                    dry_run=False, socket="/var/run/docker.sock", db=db_path,
                )
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as ctx:
                        cli.cmd_run(ns)
                self.assertEqual(ctx.exception.code, 1)
                self.assertIn(
                    "Error: docker API GET /containers/c1/json -> HTTP 500: "
                    "container c1 not found",
                    err.getvalue(),
                )
                self.assertNotIn("Traceback", err.getvalue())
        finally:
            cli.DockerEngine = previous_engine
            updater_module.Updater = previous_updater


if __name__ == "__main__":
    unittest.main()
