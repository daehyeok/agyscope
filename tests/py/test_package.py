import unittest

import agyscope


class PackageTest(unittest.TestCase):
    def test_version(self):
        self.assertEqual(agyscope.__version__, "0.2.1")


if __name__ == "__main__":
    unittest.main()
