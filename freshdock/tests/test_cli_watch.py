import contextlib
import io
import os
import tempfile
import unittest

from freshdock import cli
from freshdock.db import Database


class TestWatchUsage(unittest.TestCase):
    """A rejected `watch` must fail like any other usage error, without
    touching the store."""

    def _invoke(self, argv):
        """Run the CLI in an empty scratch directory.

        Returns (exit code or None, stderr, stdout, files left behind,
        watched entries recorded in the default store).
        """
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
            files = sorted(os.listdir(tmp))
            return code, err.getvalue(), out.getvalue(), files, self._entries(files)
        finally:
            os.chdir(previous)

    def _entries(self, files):
        if "freshdock.db" not in files:
            return None
        db = Database("freshdock.db")
        try:
            return [(e["container"], e["image"]) for e in db.list_watched()]
        finally:
            db.close()

    def test_missing_target_exits_2(self):
        code, err, _, _, _ = self._invoke(["watch"])
        self.assertEqual(code, 2)
        self.assertIn("one of --container or --image is required", err)
        self.assertIn("usage: freshdock watch", err)

    def test_missing_target_creates_no_database(self):
        _, _, _, files, entries = self._invoke(["watch"])
        self.assertEqual(files, [])
        self.assertIsNone(entries)

    def test_rejected_invocation_leaves_no_database_at_an_explicit_path(self):
        code, _, _, files, _ = self._invoke(["watch", "--db", "state/fresh.db"])
        self.assertEqual(code, 2)
        self.assertEqual(files, [])

    def test_watch_by_image_is_unchanged(self):
        code, _, out, files, entries = self._invoke(["watch", "--image", "nginx:latest"])
        self.assertIsNone(code)
        self.assertEqual(out.strip(), "Watched")
        self.assertEqual(files, ["freshdock.db"])
        self.assertEqual(entries, [(None, "nginx:latest")])

    def test_watch_by_container_is_unchanged(self):
        code, _, out, _, entries = self._invoke(["watch", "--container", "web"])
        self.assertIsNone(code)
        self.assertEqual(out.strip(), "Watched")
        self.assertEqual(entries, [("web", None)])


if __name__ == "__main__":
    unittest.main()
