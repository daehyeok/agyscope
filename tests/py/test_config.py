import os
import re
import tempfile
import tomllib
import unittest

import helpers
from agyscope import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = {"XDG_CONFIG_HOME": self.tmp}

    def write(self, data):
        self.write_to(os.path.join(self.tmp, "agyscope"), data)

    def write_to(self, d, data):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else helpers.toml_dumps(data))

    def test_defaults(self):
        s, w = config.load({}, self.env)
        self.assertEqual(w, [])
        self.assertEqual(s["keymap"], "emacs")
        self.assertEqual(s["pager"], ["less", "-R", "+G"])
        self.assertEqual(s["app_dir"], os.path.expanduser("~/.gemini/antigravity-cli"))
        self.assertEqual(s["theme"], "textual-dark")
        self.assertIs(s["marquee"], True)
        self.assertEqual(s["columns"], config.COLUMNS)
        self.assertIs(s["live"], True)
        self.assertIs(s["main_rows"], True)
        self.assertEqual(s["tree_fold"], "unfold")
        self.assertEqual(set(s), {"app_dir", "columns", "keymap", "keys", "marquee", "pager", "theme", "live",
                                  "main_rows", "tree_fold"})

    def test_config_path_default(self):
        self.assertEqual(config.config_path(self.env), os.path.join(self.tmp, "agyscope", "config.toml"))

    def test_themes_dir(self):
        self.assertEqual(config.themes_dir(self.env), os.path.join(self.tmp, "agyscope", "themes"))
        self.assertEqual(config.themes_dir({"AGYSCOPE_CONFIG": "/x/y/c.toml"}), os.path.join("/x/y", "themes"))

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
        self.write("keymap = \n")
        s, w = config.load({}, self.env)
        self.assertEqual(s["keymap"], "emacs")
        self.assertEqual(len(w), 1)
        self.assertTrue(w[0].startswith("config "), w)
        self.write({"colour": "red"})
        _, w = config.load({}, self.env)
        self.assertIn("unknown setting", w[0])

    def test_legacy_json_warns(self):
        d = os.path.join(self.tmp, "agyscope")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
            f.write('{"keymap": "vim"}\n')
        s, w = config.load({}, self.env)
        self.assertEqual(s["keymap"], "emacs")
        self.assertEqual(w, [config.LEGACY_WARNING])

    def test_legacy_json_ignored_when_toml_exists(self):
        d = os.path.join(self.tmp, "agyscope")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
            f.write('{"keymap": "vim"}\n')
        self.write({"keymap": "emacs"})
        s, w = config.load({}, self.env)
        self.assertEqual(s["keymap"], "emacs")
        self.assertEqual(w, [])

    def test_legacy_check_skipped_with_env_override(self):
        with open(os.path.join(self.tmp, "config.json"), "w", encoding="utf-8") as f:
            f.write('{"keymap": "vim"}\n')
        missing = os.path.join(self.tmp, "custom.toml")
        _, w = config.load({}, {"AGYSCOPE_CONFIG": missing})
        self.assertEqual(w, [])

    def test_toml_dumps_round_trips(self):
        d = {"keymap": "vim", "auto_open": False, "bounds": [0, 0.5, 1, 1], "keys": {"down": ["J"]}}
        self.assertEqual(tomllib.loads(helpers.toml_dumps(d)), d)

    def test_tree_fold(self):
        self.write({"tree_fold": "fold"})
        s, w = config.load({}, self.env)
        self.assertEqual((s["tree_fold"], w), ("fold", []))
        s, _ = config.load({"tree_fold": "unfold"}, self.env)  # a flag beats the file
        self.assertEqual(s["tree_fold"], "unfold")
        self.write({"tree_fold": "open"})
        s, w = config.load({}, self.env)
        self.assertEqual(s["tree_fold"], "unfold")
        self.assertEqual(len(w), 1)
        self.assertIn("tree_fold", w[0])

    def test_bad_keys(self):
        _, w = config.load({"keys": {"jump": "x"}}, self.env)
        self.assertIn("unknown action", w[0])
        _, w = config.load({"keys": {"down": "C-"}}, self.env)
        self.assertIn("bad key", w[0])

    def test_config_path_override(self):
        p = os.path.join(self.tmp, "other.toml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(helpers.toml_dumps({"keymap": "vim"}))
        s, _ = config.load({}, {"AGYSCOPE_CONFIG": p})
        self.assertEqual(s["keymap"], "vim")
        s, _ = config.load({}, dict(self.env, AGY_SUBAGENT_CONFIG=p))
        self.assertEqual(s["keymap"], "emacs")

    def test_defaults_not_shared(self):
        s, _ = config.load({}, self.env)
        s["pager"].append("x")
        s["columns"]["role"] = 99
        s2, _ = config.load({}, self.env)
        self.assertEqual(s2["pager"], ["less", "-R", "+G"])
        self.assertEqual(s2["columns"], config.COLUMNS)

    def test_columns_partial_merge(self):
        self.write({"columns": {"role": 30}})
        s, w = config.load({}, self.env)
        self.assertEqual(w, [])
        self.assertEqual(s["columns"], {**config.COLUMNS, "role": 30})

    def test_columns_invalid_all_default(self):
        for bad in ("bad", {"bogus": 5}, {"role": 7}, {"role": 81}, {"age": 2}, {"age": 11},
                    {"state": True}, {"model": "x"}, {"role": 30, "age": 99}):
            self.write({"columns": bad})
            s, w = config.load({}, self.env)
            self.assertEqual(s["columns"], config.COLUMNS, bad)
            self.assertEqual(len(w), 1, (bad, w))

    def test_columns_inclusive_bounds(self):
        for col, (lo, hi) in config.COLUMN_LIMITS.items():
            for bound in (lo, hi):
                self.write({"columns": {col: bound}})
                s, w = config.load({}, self.env)
                self.assertEqual(w, [], (col, bound))
                self.assertEqual(s["columns"][col], bound, (col, bound))

    def test_theme_and_marquee(self):
        self.write({"theme": "nord", "marquee": False})
        s, w = config.load({}, self.env)
        self.assertEqual(w, [])
        self.assertEqual((s["theme"], s["marquee"]), ("nord", False))

        s, w = config.load({"theme": "dracula", "marquee": True}, self.env)
        self.assertEqual(w, [])
        self.assertEqual((s["theme"], s["marquee"]), ("dracula", True))

        self.write({"theme": "", "marquee": "no"})
        s, w = config.load({}, self.env)
        self.assertEqual(s["theme"], "textual-dark")
        self.assertIs(s["marquee"], True)
        self.assertEqual(len(w), 2, w)


    def test_live_and_main_rows(self):
        self.write({"live": False, "main_rows": False})
        s, w = config.load({}, self.env)
        self.assertEqual(w, [])
        self.assertEqual((s["live"], s["main_rows"]), (False, False))
        s, w = config.load({"live": True}, self.env)
        self.assertIs(s["live"], True)
        self.write({"live": "no"})
        s, w = config.load({}, self.env)
        self.assertIs(s["live"], True)
        self.assertEqual(len(w), 1, w)


class RepoContractTest(unittest.TestCase):
    def test_ci_workflow(self):
        wf_path = os.path.join(helpers.REPO, ".github", "workflows", "test.yml")
        with open(wf_path, encoding="utf-8") as f:
            wf = f.read()
        self.assertIn('"3.11"', wf)
        self.assertIn('"3.13"', wf)
        self.assertIn('"3.x"', wf)
        self.assertNotIn('"3.10"', wf)
        self.assertNotIn('"3.9"', wf)
        self.assertIn("tests/run_all.sh py", wf)
        self.assertIn("setup-uv", wf)

    def test_readme_contract(self):
        readme_path = os.path.join(helpers.REPO, "README.md")
        with open(readme_path, encoding="utf-8") as f:
            readme = f.read()
        self.assertTrue(readme.startswith("# agyscope\n"))
        self.assertIn("Python 3.11 or newer", readme)
        self.assertIn("uv tool install --editable", readme)
        self.assertIn("~/.config/agyscope/config.toml", readme)
        self.assertIn("AGYSCOPE_CONFIG", readme)
        self.assertIn("## Rex integration", readme)
        self.assertIn("agyscope-rex-hook", readme)
        self.assertIn("integrations/rex/hooks.json", readme)
        self.assertIn('rex.bind("alt+s", "agyscope")', readme)
        self.assertIn("--rex-block", readme)
        self.assertIn("Unofficial", readme)
        self.assertIn("uv tool install agyscope", readme)
        self.assertIn("uv tool upgrade agyscope", readme)
        self.assertIn("uv tool uninstall agyscope", readme)
        self.assertIn("pipx install agyscope", readme)
        self.assertIn("uv tool install git+https://github.com/daehyeok/agyscope", readme)
        self.assertIn('require("plugins.agyscope")', readme)
        self.assertIn('keys = { down = ["J"], close = "C-x" }', readme)
        for required in ("## Themes", "examples/themes/catppuccin-mocha.toml", "~/.config/agyscope/themes/",
                         "theme", "marquee", "[columns]", "--theme", "--no-marquee",
                         "config.json is no longer read",
                         "https://github.com/daehyeok/agyscope/blob/main/examples/config.toml",
                         "https://github.com/daehyeok/agyscope/blob/main/integrations/rex/hooks.json",
                         "https://github.com/daehyeok/agyscope/blob/main/LICENSE"):
            self.assertIn(required, readme)
        for gone in ("AGY_SUBAGENT_", "bin/agy-subagent", "--block ", "agy_subagent", "3.9 or newer",
                     "3.10 or newer", "uv lock --upgrade --project", "hooks/hooks.json", "rex-agy-subagent",
                     "agyscope/config.json", '"keymap": "emacs"', '{"down": ["J"], "close": "C-x"}'):
            self.assertNotIn(gone, readme)
        for target in re.findall(r"\]\(([^)]+)\)", readme):
            self.assertTrue(
                target.startswith(("https://", "#")),
                f"README link/image must be absolute https:// or #anchor for PyPI rendering: {target}",
            )

    def test_changelog(self):
        cl_path = os.path.join(helpers.REPO, "CHANGELOG.md")
        self.assertTrue(os.path.isfile(cl_path), "CHANGELOG.md must exist")
        with open(cl_path, encoding="utf-8") as f:
            cl = f.read()
        self.assertTrue(cl.startswith("# Changelog"))
        for heading in ("## [0.2.1]", "## [0.2.0]", "## [0.1.0]", "BREAKING"):
            self.assertIn(heading, cl)
        sec_021 = cl[cl.index("## [0.2.1]"):cl.index("## [0.2.0]")]
        self.assertIn("config.toml", sec_021)
        self.assertIn("Python 3.11", sec_021)
        self.assertIn("uv tool install agyscope", sec_021)

    def test_demo_gif_referenced_and_valid(self):
        readme_path = os.path.join(helpers.REPO, "README.md")
        with open(readme_path, encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("![demo](https://raw.githubusercontent.com/daehyeok/agyscope/main/docs/demo.gif)", readme)
        self.assertNotIn("![demo](docs/demo.gif)", readme)
        self.assertNotIn("TODO: demo GIF", readme)

        gif_path = os.path.join(helpers.REPO, "docs", "demo.gif")
        self.assertTrue(os.path.isfile(gif_path), "docs/demo.gif must exist")
        with open(gif_path, "rb") as f:
            header = f.read(6)
        self.assertIn(header, (b"GIF87a", b"GIF89a"))
        self.assertGreater(os.path.getsize(gif_path), 20_000)

    def test_packaging_hygiene(self):
        import tomllib

        readme_path = os.path.join(helpers.REPO, "README.md")
        with open(readme_path, encoding="utf-8") as f:
            readme = f.read()
        self.assertNotIn("docs/demo.gif` is stale", readme)

        gitignore_path = os.path.join(helpers.REPO, ".gitignore")
        with open(gitignore_path, encoding="utf-8") as f:
            gitignore = f.read().splitlines()
        for entry in ("dist/", "build/", "*.egg-info/", ".pytest_cache/"):
            self.assertIn(entry, gitignore)

        pyproject_path = os.path.join(helpers.REPO, "pyproject.toml")
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        sdist_exclude = (
            pyproject.get("tool", {})
            .get("hatch", {})
            .get("build", {})
            .get("targets", {})
            .get("sdist", {})
            .get("exclude", [])
        )
        for excluded in ("docs/demo.gif", ".superpowers/"):
            self.assertIn(excluded, sdist_exclude)


if __name__ == "__main__":
    unittest.main()
