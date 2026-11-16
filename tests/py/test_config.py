import json
import os
import tempfile
import unittest

import helpers  # noqa: F401
from agy_subagent_py import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"XDG_CONFIG_HOME": self.tmp}

    def write(self, data):
        d = os.path.join(self.tmp, "rex-agy-subagent")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_defaults(self):
        s, w = config.load({}, self.env)
        self.assertEqual(w, [])
        self.assertEqual(s["keymap"], "emacs")
        self.assertEqual(s["pager"], ["less", "-R", "+G"])
        self.assertTrue(s["auto_open"])
        self.assertIs(s["interactive"], True)
        self.assertEqual(s["app_dir"], os.path.expanduser("~/.gemini/antigravity-cli"))

    def test_interactive_override(self):
        self.write({"interactive": False})
        s, w = config.load({}, self.env)
        self.assertEqual(w, [])
        self.assertIs(s["interactive"], False)
        s, w = config.load({}, dict(self.env, AGY_SUBAGENT_INTERACTIVE="yes"))
        self.assertEqual(w, [])
        self.assertIs(s["interactive"], True)
        s, w = config.load({}, dict(self.env, AGY_SUBAGENT_INTERACTIVE="off"))
        self.assertIs(s["interactive"], False)
        s, w = config.load({}, dict(self.env, AGY_SUBAGENT_INTERACTIVE="maybe"))
        self.assertIs(s["interactive"], True)  # a bad env value means the default, as for auto_open
        self.assertTrue(any("AGY_SUBAGENT_INTERACTIVE" in x for x in w), w)

    def test_interactive_bad_type_falls_back(self):
        self.write({"interactive": "no"})
        s, w = config.load({}, self.env)
        self.assertIs(s["interactive"], True)
        self.assertTrue(any("interactive" in x for x in w), w)

    def test_file_then_env_then_flag(self):
        self.write({"keymap": "vim", "pager": ["less"], "auto_open": False})
        s, _ = config.load({}, self.env)
        self.assertEqual((s["keymap"], s["pager"], s["auto_open"]), ("vim", ["less"], False))
        env = dict(self.env, AGY_SUBAGENT_PAGER='["bat", "--paging=always"]', AGY_SUBAGENT_AUTO_OPEN="1")
        s, _ = config.load({}, env)
        self.assertEqual(s["pager"], ["bat", "--paging=always"])
        self.assertTrue(s["auto_open"])
        self.assertEqual(s["keymap"], "vim")
        s, _ = config.load({"pager": ["bat"], "keymap": "emacs"}, env)
        self.assertEqual((s["pager"], s["keymap"]), (["bat"], "emacs"))

    def test_json_env_object(self):
        s, w = config.load({}, dict(self.env, AGY_SUBAGENT_KEYS='{"down": "J"}'))
        self.assertEqual(w, [])
        self.assertEqual(s["keys"], {"down": ["J"]})

    def test_bad_keymap_falls_back(self):
        self.write({"keymap": "nano"})
        s, w = config.load({}, self.env)
        self.assertEqual(s["keymap"], "emacs")
        self.assertTrue(any("keymap" in x for x in w), w)

    def test_non_list_pager_falls_back(self):
        s, w = config.load({}, dict(self.env, AGY_SUBAGENT_PAGER='"bat"'))
        self.assertEqual(s["pager"], ["less", "-R", "+G"])
        self.assertTrue(w)

    def test_bad_json_env(self):
        s, w = config.load({}, dict(self.env, AGY_SUBAGENT_PAGER="bat -p"))
        self.assertEqual(s["pager"], ["less", "-R", "+G"])
        self.assertIn("must be JSON", w[0])

    def test_broken_file_and_unknown_setting(self):
        self.write("{not json")
        s, w = config.load({}, self.env)
        self.assertEqual(s["keymap"], "emacs")
        self.assertEqual(len(w), 1)
        self.write({"colour": "red"})
        _, w = config.load({}, self.env)
        self.assertIn("unknown setting", w[0])

    def test_bad_keys(self):
        _, w = config.load({"keys": {"jump": "x"}}, self.env)
        self.assertIn("unknown action", w[0])
        _, w = config.load({"keys": {"down": "C-"}}, self.env)
        self.assertIn("bad key", w[0])

    def test_config_path_override(self):
        p = os.path.join(self.tmp, "other.json")
        with open(p, "w") as f:
            json.dump({"keymap": "vim"}, f)
        s, _ = config.load({}, {"AGY_SUBAGENT_CONFIG": p})
        self.assertEqual(s["keymap"], "vim")

    def test_defaults_not_shared(self):
        s, _ = config.load({}, self.env)
        s["pager"].append("x")
        s2, _ = config.load({}, self.env)
        self.assertEqual(s2["pager"], ["less", "-R", "+G"])


class RepoContractTest(unittest.TestCase):
    def test_ci_workflow_and_readme_python_floor(self):
        wf_path = os.path.join(helpers.REPO, ".github", "workflows", "test.yml")
        with open(wf_path, encoding="utf-8") as f:
            wf = f.read()
        self.assertIn('"3.9"', wf)
        self.assertIn('"3.13"', wf)
        self.assertIn('"3.x"', wf)
        self.assertNotIn('"3.8"', wf)
        self.assertNotIn("Python 3.8", wf)

        readme_path = os.path.join(helpers.REPO, "README.md")
        with open(readme_path, encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("`python3` 3.9 or newer", readme)
        self.assertNotIn("`python3` 3.8", readme)
        self.assertIn("Planner", readme)
        self.assertIn("Reviewer", readme)

    def test_demo_gif_referenced_and_valid(self):
        readme_path = os.path.join(helpers.REPO, "README.md")
        with open(readme_path, encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("![demo](docs/demo.gif)", readme)
        self.assertNotIn("TODO: demo GIF", readme)

        gif_path = os.path.join(helpers.REPO, "docs", "demo.gif")
        self.assertTrue(os.path.isfile(gif_path), "docs/demo.gif must exist")
        with open(gif_path, "rb") as f:
            header = f.read(6)
        self.assertIn(header, (b"GIF87a", b"GIF89a"))
        self.assertGreater(os.path.getsize(gif_path), 20_000)


if __name__ == "__main__":
    unittest.main()
