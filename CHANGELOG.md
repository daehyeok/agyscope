# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-11-16

### Changed

- **BREAKING:** Switched configuration from `config.json` to TOML (`config.toml`).
  `config.json` is no longer read; agyscope shows a warning if `config.json` exists next to
  a missing `config.toml`.
- **BREAKING:** Minimum supported Python version raised from 3.10 to **Python 3.11**
  (uses stdlib `tomllib` with no new dependencies).
- Subagent state and step colors now follow the active Textual theme.

### Added

- Published to PyPI; install with `uv tool install agyscope`.
- Theme support via `theme` in `config.toml` and `--theme NAME`, plus user themes loaded
  from `~/.config/agyscope/themes/*.toml` with `agyscope-<state>` variable overrides.
- Shipped `examples/themes/catppuccin-mocha.toml` and `examples/config.toml`.
- Resizable list columns: configurable initial widths via `[columns]` in `config.toml` and
  session-only mouse dragging of column header borders.
- Horizontal marquee scrolling for the highlighted subagent's **Last step** cell when it
  overflows (`marquee` in `config.toml`, `--marquee` / `--no-marquee`).

## [0.2.0] - 2026-11-16

### Changed

- Renamed project from `rex-agy-subagent` to `agyscope` and restructured it as an
  installable Python package (`agyscope` and `agyscope-rex-hook`).
- Replaced the earlier UI with a full-screen Textual panel featuring subagent list and
  live step view screens, `emacs`/`vim` key presets, folding, and pager support.

## [0.1.0] - 2026-11-15

### Added

- Initial `rex-agy-subagent` release: subagent viewer panel and `PostToolUse` hook
  integration for Rex terminal panes.
