"""User theme loading and theme-driven state style mapping."""

import os
import re
import tempfile
import unittest

import helpers
from agyscope import config

try:
    import rich.color
    from rich.console import Console
    from rich.style import Style
    from rich.text import Text
    from textual.app import App
    HAVE_TEXTUAL = True
except ImportError:
    HAVE_TEXTUAL = False

if HAVE_TEXTUAL:
    from agyscope import themes


@unittest.skipUnless(HAVE_TEXTUAL, "textual not installed")
class ThemesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def write_theme(self, filename, content):
        path = os.path.join(self.tmp, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_load_valid_and_name_from_stem(self):
        self.write_theme("a.toml", 'primary = "#ff0000"\n')
        loaded, warnings = themes.load_user_themes(self.tmp)
        self.assertEqual(warnings, [])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].name, "a")
        self.assertEqual(loaded[0].primary, "#ff0000")

    def test_explicit_name(self):
        self.write_theme("a.toml", 'name = "zz"\nprimary = "#ff0000"\n')
        loaded, warnings = themes.load_user_themes(self.tmp)
        self.assertEqual(warnings, [])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].name, "zz")

    def test_bad_files_skipped(self):
        bad_cases = [
            ("no_primary.toml", 'secondary = "#112233"\n'),
            ("unknown_key.toml", 'primary = "#ff0000"\ncolour = "#112233"\n'),
            ("bad_dark.toml", 'primary = "#ff0000"\ndark = "yes"\n'),
            ("bad_vars.toml", 'primary = "#ff0000"\n[variables]\nx = 1\n'),
            ("broken.toml", 'primary = \n'),
            ("bad_color.toml", 'primary = "not-a-color"\n'),
            ("empty_secondary.toml", 'primary = "#ff0000"\nsecondary = ""\n'),
        ]
        paths = [self.write_theme(name, body) for name, body in bad_cases]
        self.write_theme("good.toml", 'primary = "#00ff00"\n')
        loaded, warnings = themes.load_user_themes(self.tmp)
        self.assertEqual([t.name for t in loaded], ["good"])
        self.assertEqual(len(warnings), len(bad_cases))
        for path in paths:
            self.assertTrue(any(path in w for w in warnings), (path, warnings))

    def test_missing_dir(self):
        loaded, warnings = themes.load_user_themes(os.path.join(self.tmp, "missing"))
        self.assertEqual((loaded, warnings), ([], []))

    def test_styles_from_mapping(self):
        variables = {
            "warning": "#111111",
            "success": "#222222",
            "error": "#333333",
            "primary": "#444444",
            "accent": "#555555",
            "secondary": "#666666",
            "foreground": "#ffffff",
            "background": "#000000",
            "text-muted": "auto 60%",
        }
        styles = themes.styles_from(variables)
        self.assertEqual(set(styles), set(themes.FALLBACK_STYLES))
        self.assertEqual(styles["working"].color, rich.color.Color.parse("#111111"))
        self.assertEqual(styles["done"].color, rich.color.Color.parse("#222222"))
        self.assertEqual(styles["failed"].color, rich.color.Color.parse("#333333"))
        self.assertEqual(styles["waiting"].color, rich.color.Color.parse("#444444"))
        self.assertEqual(styles["cmd"].color, rich.color.Color.parse("#555555"))
        self.assertEqual(styles["dim"].color.triplet, rich.color.Color.parse("#999999").triplet)
        self.assertTrue(styles["cmd"].bold)
        self.assertIsNone(styles["step"].color)
        self.assertTrue(styles["step"].bold)
        self.assertEqual(styles["workflow"].color, rich.color.Color.parse("#666666"))
        self.assertFalse(styles["workflow"].bold)

    def test_workflow_override_and_fallback(self):
        styles = themes.styles_from({"secondary": "#666666", "agyscope-workflow": "#ff8800"})
        self.assertEqual(styles["workflow"].color, rich.color.Color.parse("#ff8800"))
        self.assertEqual(themes.styles_from({})["workflow"].color, rich.color.Color.parse("bright_blue"))

    def test_agyscope_override(self):
        variables = {
            "primary": "#444444",
            "accent": "#555555",
            "agyscope-waiting": "#89b4fa",
        }
        styles = themes.styles_from(variables)
        self.assertEqual(styles["waiting"].color, rich.color.Color.parse("#89b4fa"))
        self.assertEqual(styles["cmd"].color, rich.color.Color.parse("#555555"))

    def test_unparseable_falls_back(self):
        styles = themes.styles_from({"warning": "nonsense"})
        self.assertEqual(styles["working"], themes.FALLBACK_STYLES["working"])

    def _assert_theme_renders(self, theme_name):
        app = App()
        app.theme = theme_name
        styles = themes.styles_from(app.get_css_variables())
        self.assertEqual(set(styles), set(themes.FALLBACK_STYLES))
        console = Console()
        for style in styles.values():
            self.assertIsInstance(style, Style)
            list(Text("x", style).render(console))

    def test_ansi_theme_variables(self):
        self._assert_theme_renders("ansi-dark")

    def test_textual_dark_variables(self):
        self._assert_theme_renders("textual-dark")

    def test_example_theme_loads(self):
        loaded, warnings = themes.load_user_themes(os.path.join(helpers.REPO, "examples", "themes"))
        self.assertEqual(warnings, [])
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].name, "catppuccin-mocha")
        self.assertEqual(loaded[0].background, "#1e1e2e")

    def test_example_config_is_valid(self):
        example = os.path.join(helpers.REPO, "examples", "config.toml")
        with open(example, encoding="utf-8") as f:
            text = f.read()
        uncommented = re.sub(r"(?m)^# (?=[a-z_]+ = |\[)", "", text)
        path = os.path.join(self.tmp, "config.toml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(uncommented)
        s, w = config.load({}, {"AGYSCOPE_CONFIG": path})
        self.assertEqual(w, [])
        defaults, _ = config.load({}, {"AGYSCOPE_CONFIG": os.path.join(self.tmp, "missing.toml")})
        for k, v in defaults.items():
            if k == "theme":
                self.assertEqual(s[k], "catppuccin-mocha")
            else:
                self.assertEqual(s[k], v, k)


if __name__ == "__main__":
    unittest.main()
