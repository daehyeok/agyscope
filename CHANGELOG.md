# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-11-17

### Added

- Several conversations in one panel: the pane's conversation first, then `--conversation`
  ids (now repeatable), top-level attachments and, with the new `live` setting (default
  on; `--no-live`), every running agent on the host, found from held presence locks. A
  conversation whose agent exited stays listed for 10 minutes.
- A row for each conversation itself (title, state, model, age, last step) that opens its
  own steps; `m` hides or shows these rows (`main_rows` setting, `--no-main-rows`).
- `agyscope attach`, `agyscope detach` and `agyscope links`: persistent conversation links
  under `$XDG_STATE_HOME/agyscope/`. Without `--under`/`--top` the parent comes from
  `$AGYSCOPE_MAIN` or the agent's `$ANTIGRAVITY_CONVERSATION_ID`. Ids may be prefixes;
  cycles are rejected. Running panels are poked over `run/<pid>.sock` and update at once.
- In the panel: `a`/`A` (vim `i`/`I`) open a fuzzy attach picker (top level / under the
  highlighted conversation), `d` (vim `x`) detaches the highlighted row.
- Workflow agents named in a conversation's task logs (`[agent:<type>] ... (<id>)`) are
  listed under it once they finish. Linked rows are marked `~`.
- Role names show the agent kind: workflow/swarm agents use the theme's `secondary` color
  (theme variable `agyscope-workflow`), main rows are bold, subagents are unchanged.

### Changed

- **BREAKING:** with `live` on by default, every pane's panel also lists the other running
  agents; set `live = false` (or Rex `panel = { live = false }`) for the old behavior.
- Following the pane's agent no longer resets the panel: only the pane's conversation is
  replaced; other conversations keep their folds and an open view.
- Source collection (`lsof`, `/proc`) runs in the background with AnyIO (new dependency)
  instead of on the UI thread; a hung `lsof` is killed after 2 seconds.

### Fixed

- The list now uses the theme's state colors from the start in a real terminal; before, it
  kept the fallback colors until the theme was switched.


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
