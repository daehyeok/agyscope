import json
import os
import tempfile
import unittest

import helpers  # noqa: F401
from agyscope import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"XDG_CONFIG_HOME": self.tmp}

    def write(self, data):
        self.write_to(os.path.join(self.tmp, "agyscope"), data)

    def write_to(self, d, data):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_defaults(self):
        s, w = config.load({}, self.env)
        self.assertEqual(w, [])
        self.assertEqual(s["keymap"], "emacs")
        self.assertEqual(s["pager"], ["less", "-R", "+G"])
        self.assertEqual(s["app_dir"], os.path.expanduser("~/.gemini/antigravity-cli"))
        self.assertEqual(set(s), {"app_dir", "keymap", "keys", "pager"})

    def test_config_path_default(self):
        self.assertEqual(config.config_path(self.env), os.path.join(self.tmp, "agyscope", "config.json"))

    def test_env_vars_are_ignored(self):
        env = dict(self.env, AGY_SUBAGENT_KEYMAP="vim", AGY_SUBAGENT_PAGER='["bat"]', AGYSCOPE_KEYMAP="vim")
        s, w = config.load({}, env)
        self.assertEqual(s["keymap"], "emacs")
        self.assertEqual(s["pager"], ["less", "-R", "+G"])
        self.assertEqual(w, [])

    def test_old_config_dir_not_read(self):
        self.write_to(os.path.join(self.tmp, "rex-agy-subagent"), {"keymap": "vim"})
        s, w = config.load({}, self.env)
        self.assertEqual(s["keymap"], "emacs")
        self.assertEqual(w, [])

    def test_integration_keys_do_not_warn(self):
        self.write({"bounds": [0, 0, 1, 1], "auto_open": False, "interactive": False, "command": ["x"]})
        s, w = config.load({}, self.env)
        self.assertEqual(w, [])
        self.assertNotIn("bounds", s)

    def test_extra_specs(self):
        extra = {"auto_open": (True, config.check_bool)}
        self.write({"auto_open": False})
        s, w = config.load({}, self.env, extra=extra)
        self.assertEqual(w, [])
        self.assertIs(s["auto_open"], False)
        self.write({"auto_open": "no"})
        s, w = config.load({}, self.env, extra=extra)
        self.assertIs(s["auto_open"], True)
        self.assertTrue(any("auto_open" in x and "True" in x for x in w), w)

    def test_extra_cannot_override_core(self):
        with self.assertRaises(ValueError):
            config.load({}, self.env, extra={"keymap": ("vim", lambda v: v)})

    def test_check_str_list(self):
        self.assertEqual(config.check_str_list(["a", "b"]), ["a", "b"])
        for bad in ([], "a", [""], [1], None):
            with self.assertRaises(ValueError):
                config.check_str_list(bad)

    def test_check_bool(self):
        self.assertIs(config.check_bool(False), False)
        for bad in ("no", 0, None):
            with self.assertRaises(ValueError):
                config.check_bool(bad)

    def test_file_then_flag(self):
        self.write({"keymap": "vim", "pager": ["less"]})
        s, _ = config.load({}, self.env)
        self.assertEqual((s["keymap"], s["pager"]), ("vim", ["less"]))
        s, _ = config.load({"pager": ["bat"], "keymap": "emacs"}, self.env)
        self.assertEqual((s["pager"], s["keymap"]), (["bat"], "emacs"))

    def test_bad_keymap_falls_back(self):
        self.write({"keymap": "nano"})
        s, w = config.load({}, self.env)
        self.assertEqual(s["keymap"], "emacs")
        self.assertTrue(any("keymap" in x for x in w), w)

    def test_non_list_pager_falls_back(self):
        self.write({"pager": "bat"})
        s, w = config.load({}, self.env)
        self.assertEqual(s["pager"], ["less", "-R", "+G"])
        self.assertTrue(w)

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
        s, _ = config.load({}, {"AGYSCOPE_CONFIG": p})
        self.assertEqual(s["keymap"], "vim")
        s, _ = config.load({}, dict(self.env, AGY_SUBAGENT_CONFIG=p))
        self.assertEqual(s["keymap"], "emacs")

    def test_defaults_not_shared(self):
        s, _ = config.load({}, self.env)
        s["pager"].append("x")
        s2, _ = config.load({}, self.env)
        self.assertEqual(s2["pager"], ["less", "-R", "+G"])


class RepoContractTest(unittest.TestCase):
    def test_ci_workflow(self):
        wf_path = os.path.join(helpers.REPO, ".github", "workflows", "test.yml")
        with open(wf_path, encoding="utf-8") as f:
            wf = f.read()
        self.assertIn('"3.10"', wf)
        self.assertIn('"3.13"', wf)
        self.assertIn('"3.x"', wf)
        self.assertNotIn('"3.9"', wf)
        self.assertIn("tests/run_all.sh py", wf)
        self.assertIn("setup-uv", wf)

    def test_readme_contract(self):
        readme_path = os.path.join(helpers.REPO, "README.md")
        with open(readme_path, encoding="utf-8") as f:
            readme = f.read()
        self.assertTrue(readme.startswith("# agyscope\n"))
        self.assertIn("Python 3.10 or newer", readme)
        self.assertIn("uv tool install --editable", readme)
        self.assertIn("~/.config/agyscope/config.json", readme)
        self.assertIn("AGYSCOPE_CONFIG", readme)
        self.assertIn("## Rex integration", readme)
        self.assertIn("agyscope-rex-hook", readme)
        self.assertIn("integrations/rex/hooks.json", readme)
        self.assertIn('rex.bind("alt+s", "agyscope")', readme)
        self.assertIn("--rex-block", readme)
        self.assertIn("Unofficial", readme)
        self.assertIn("uv tool install git+https://github.com/daehyeok/agyscope", readme)
        self.assertIn('require("plugins.agyscope")', readme)
        for gone in ("AGY_SUBAGENT_", "bin/agy-subagent", "--block ", "agy_subagent", "3.9 or newer",
                     "uv lock --upgrade --project", "hooks/hooks.json", "rex-agy-subagent"):
            self.assertNotIn(gone, readme)

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
