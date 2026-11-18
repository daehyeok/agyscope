import re
import unittest

import agyscope


class PackageTest(unittest.TestCase):
    def test_version(self):
        # __version__ is the single version source (pyproject reads it via hatch);
        # releases are plain X.Y.Z with no pre-release suffix.
        self.assertRegex(agyscope.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
