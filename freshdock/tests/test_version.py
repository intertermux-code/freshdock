import io
import os
import re
import unittest
from contextlib import redirect_stdout

from freshdock import __version__
from freshdock.cli import main

PYPROJECT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "pyproject.toml")


class VersionTests(unittest.TestCase):
    def test_version_prints_package_version(self):
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"freshdock {__version__}")

    def test_pyproject_matches_package_version(self):
        with open(PYPROJECT, encoding="utf-8") as f:
            declared = re.search(r'^version = "([^"]+)"', f.read(), re.MULTILINE)
        self.assertIsNotNone(declared, "no version in pyproject.toml")
        self.assertEqual(declared.group(1), __version__)


if __name__ == "__main__":
    unittest.main()
