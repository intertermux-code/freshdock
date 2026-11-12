import contextlib
import io
import os
import tempfile
import unittest

from freshdock import cli


def _runs_as_root():
    # root ignores permission bits, so an "unwritable" directory is still
    # writable and the precondition of that test cannot be established.
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


class TestDbPathUsage(unittest.TestCase):
    """A bad --db path must fail cleanly: exit 2, path named, no traceback,
    and no database file left behind."""

    def _invoke(self, argv):
        """Run the CLI in an empty scratch directory.

        Returns (exit code or None, stderr, stdout, files left behind).
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
            return code, err.getvalue(), out.getvalue(), files
        finally:
            os.chdir(previous)

    def _assert_clean_usage_error(self, code, err, out, files, path):
        self.assertEqual(code, 2, f"expected exit 2, got {code}")
        self.assertIn(path, err, f"expected path {path!r} in stderr: {err!r}")
        self.assertNotIn("Traceback", err)
        self.assertNotIn("Traceback", out)
        self.assertFalse(
            any(f.endswith(".db") for f in files),
            f"unexpected database file left behind in cwd: {files!r}",
        )

    def test_missing_parent_dir_exits_2_cleanly(self):
        code, err, out, files = self._invoke(
            ["watch", "--container", "web", "--db", "missing_dir/freshdock.db"]
        )
        self._assert_clean_usage_error(code, err, out, files, "missing_dir/freshdock.db")

    def test_path_is_a_directory_exits_2_cleanly(self):
        previous = os.getcwd()
        tmp = tempfile.mkdtemp()
        os.chdir(tmp)
        try:
            os.mkdir("a_dir")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = None
                try:
                    cli.main(["watch", "--container", "web", "--db", "a_dir"])
                except SystemExit as e:
                    code = e.code
            files = sorted(os.listdir(tmp))
            err_text, out_text = err.getvalue(), out.getvalue()
        finally:
            os.chdir(previous)
        self._assert_clean_usage_error(code, err_text, out_text, files, "a_dir")

    @unittest.skipIf(_runs_as_root(), "root bypasses permission bits, so the directory is still writable")
    def test_unwritable_location_exits_2_cleanly(self):
        previous = os.getcwd()
        tmp = tempfile.mkdtemp()
        os.chdir(tmp)
        try:
            os.mkdir("ro_dir")
            os.chmod("ro_dir", 0o555)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = None
                try:
                    cli.main(["watch", "--container", "web",
                              "--db", "ro_dir/freshdock.db"])
                except SystemExit as e:
                    code = e.code
            files = sorted(os.listdir(tmp))
            err_text, out_text = err.getvalue(), out.getvalue()
        finally:
            os.chmod("ro_dir", 0o755)
            os.chdir(previous)
        self._assert_clean_usage_error(code, err_text, out_text, files,
                                       "ro_dir/freshdock.db")

    def test_valid_default_db_unchanged(self):
        code, _, out, files = self._invoke(["watch", "--image", "nginx:latest"])
        self.assertIsNone(code)
        self.assertEqual(out.strip(), "Watched")
        self.assertEqual(files, ["freshdock.db"])

    def test_valid_explicit_db_unchanged(self):
        previous = os.getcwd()
        tmp = tempfile.mkdtemp()
        os.chdir(tmp)
        try:
            os.mkdir("state")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = None
                try:
                    cli.main(["watch", "--container", "web", "--db", "state/fresh.db"])
                except SystemExit as e:
                    code = e.code
            files = sorted(os.listdir(tmp))
            state_files = sorted(os.listdir("state"))
        finally:
            os.chdir(previous)
        self.assertIsNone(code)
        self.assertEqual(out.getvalue().strip(), "Watched")
        self.assertEqual(files, ["state"])
        self.assertEqual(state_files, ["fresh.db"])


if __name__ == "__main__":
    unittest.main()
