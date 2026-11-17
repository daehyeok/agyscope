# agyscope

[![test](https://github.com/daehyeok/agyscope/actions/workflows/test.yml/badge.svg)](https://github.com/daehyeok/agyscope/actions/workflows/test.yml)

`agyscope` is an unofficial, read-only viewer for the subagents of an Antigravity agent
CLI (`agy`) session. It runs in any terminal.

It shows a full-width subagent list with each subagent's state, model, age, and last
step. Press `Enter` on any subagent to open a full-screen view of its live steps, where
you can fold turns and tool calls, scroll line-by-line, or open the full transcript in
a pager. Press `Esc` or `q` to return to the list. agyscope only reads files. It never
sends anything to an agent or a terminal.

> **Unofficial.** This project is not affiliated with or endorsed by Google. See
> [Disclaimer](#disclaimer).

![demo](https://raw.githubusercontent.com/daehyeok/agyscope/main/docs/demo.gif)

agyscope rebuilds the tree from the agent's transcripts, so it always shows every
subagent of the session, including ones that finished before you opened it. Closing
it loses nothing. Reopen it and everything is back.

Running agents in [Rex](#rex-integration) panes? The optional Rex integration opens
agyscope in a floating pane next to the agent, from a key or automatically when the
first subagent starts.

## Install

Requirements, on every host that runs the agent:

- [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer (a uv-managed Python is fine);
- `less`, or the pager you configure;
- `lsof` to follow an agent process. On Linux it is optional: without it agyscope reads
  `/proc/<pid>/fd`.

Install the `agyscope` and `agyscope-rex-hook` commands from PyPI with uv:

```sh
uv tool install agyscope
```

Upgrade, pin a version, or uninstall:

```sh
uv tool upgrade agyscope
uv tool install agyscope==0.2.1
uv tool uninstall agyscope
```

(`pipx install agyscope` also works.)

uv puts the commands in its tool bin directory (normally `~/.local/bin`) and installs
Textual into the tool's own environment, using the host's own index configuration
(`uv.toml`, `UV_INDEX_URL`, ...). Nothing else is installed globally.

### Development / from source

To install directly from GitHub or from an editable local clone (edits to the clone
take effect at once):

```sh
uv tool install git+https://github.com/daehyeok/agyscope
```

```sh
git clone https://github.com/daehyeok/agyscope ~/src/agyscope
uv tool install --editable ~/src/agyscope
```

## Usage

```sh
agyscope --pid 1234                     # follow the agent process 1234
agyscope --conversation <id>            # show one conversation
agyscope --once --pid 1234              # print the list once as plain text
agyscope --once --conversation <id>     # same, for a conversation id
agyscope --view <child-id-prefix> --conversation <id>   # one subagent, fully unfolded
```

Which conversation is shown:

1. `--pid PID`: the agent keeps `<data dir>/presence/<conversation>.lock` open, and the
   open files come from `lsof`, or from `/proc/<pid>/fd` when `lsof` is missing. The
   directory holding `presence/` is the data directory, so no setting is needed.
   agyscope re-checks the pid every 3 seconds, so resuming another conversation in the
   same process switches the view. A dead pid keeps the last answer.
2. With the [Rex integration](#rex-integration), the conversation the hook last recorded
   for the pane (`--rex-block`).
3. `--conversation ID`.

From the root conversation it follows every `invoke_subagent` result to the child
transcripts, recursively.

`--once` and `--view` print text and exit; they don't start the Textual UI. The exit
code is 0 on success, 1 when no conversation was found (or after `ctrl+c`), and 2 when
agyscope crashed. After a crash on a terminal it waits for `Enter`, so the traceback
stays readable.

Settings flags: `--app-dir DIR`, `--keymap emacs|vim`, `--keys-json JSON`,
`--pager-json JSON`, `--theme NAME`, `--marquee` / `--no-marquee`
(see [Configuration](#configuration)). `agyscope --help` lists them all.

## Configuration

For each setting, the first source that sets it wins:

1. command-line flags (`--app-dir`, `--keymap`, `--keys-json`, `--pager-json`, `--theme`,
   `--marquee` / `--no-marquee`);
2. the config file `${XDG_CONFIG_HOME:-~/.config}/agyscope/config.toml` (normally
   `~/.config/agyscope/config.toml`), or the file named by `AGYSCOPE_CONFIG`;
3. the defaults.

> **Migrating from 0.2.0:** config.json is no longer read; convert it to config.toml.
> If `config.json` sits next to a missing `config.toml`, agyscope warns on the status line
> and starts with the defaults.

| Setting | Default | Description |
|---|---|---|
| `app_dir` | `"~/.gemini/antigravity-cli"` | Fallback data directory of the agent. It holds `brain/<conversation>/…/transcript.jsonl` and `presence/`. |
| `keymap` | `"emacs"` | Key preset: `"emacs"` or `"vim"`. |
| `keys` | `{}` | Extra keys per action, added to the preset, e.g. `keys = { down = ["J"], close = "C-x" }`. |
| `pager` | `["less", "-R", "+G"]` | Pager argv for the pager action. The file path replaces a `{file}` argument, or is appended. |
| `theme` | `"textual-dark"` | Theme name (Textual built-in or a file in `~/.config/agyscope/themes/`). |
| `marquee` | `true` | Horizontally scroll the highlighted row's **Last step** cell when it overflows. |
| `columns` | `{role = 24, state = 8, model = 22, age = 5}` | Initial list column widths in cells (`role` 8–80, `state` 4–20, `model` 6–60, `age` 3–10). |

The data directory is usually found without `app_dir`: from the presence lock the agent
process holds open (`--pid`), or from the transcript path the Rex hook records. Those
win over `app_dir`, which is only used when neither is available, for example with
`--conversation`.

The same file also holds the [Rex hook settings](#hook-settings) (`bounds`,
`auto_open`, `interactive`, `command`); agyscope itself ignores them.

An invalid value shows a warning on the bottom line (with `--once`/`--view`, on stderr),
and agyscope uses the default for that setting. A config mistake never stops it from
starting.

See [examples/config.toml](https://github.com/daehyeok/agyscope/blob/main/examples/config.toml) for a commented template with every
setting. Example `~/.config/agyscope/config.toml`:

```toml
keymap = "emacs"
pager = ["bat", "--paging=always", "--style=plain"]
theme = "catppuccin-mocha"

[keys]
down = ["J"]
up = ["K"]

[columns]
role = 28
```

## Themes

Set `theme = "<name>"` in `config.toml` or pass `--theme <name>`. Built-in Textual themes
include `textual-dark`, `textual-light`, `nord`, `gruvbox`, `dracula`, `tokyo-night`,
`monokai`, `flexoki`, `solarized-light`, `solarized-dark`, `rose-pine`, `rose-pine-moon`,
`rose-pine-dawn`, `atom-one-dark`, `atom-one-light`, `catppuccin-mocha`,
`catppuccin-latte`, `catppuccin-frappe`, `catppuccin-macchiato`, `ansi-dark`, and
`ansi-light`.

User themes live in `~/.config/agyscope/themes/*.toml` (or `themes/` beside
`AGYSCOPE_CONFIG`). Each file defines a Textual theme: `primary` is required, `name`
defaults to the file stem, and any of `secondary`, `warning`, `error`, `success`,
`accent`, `foreground`, `background`, `surface`, `panel`, `boost`, `dark`,
`luminosity_spread`, `text_alpha`, `ansi`, and `[variables]` are accepted. A user theme
with the same name as a built-in replaces it. Invalid theme files warn and are skipped.

Subagent state colors follow the active theme (`working`/`running` → `$warning`,
`done` → `$success`, `error`/`failed`/`warn` → `$error`, `waiting` → `$primary`,
`msg`/`cmd` → `$accent`, `quiet`/`dim` → `$text-muted`). Any state or step style can be
overridden in `[variables]` with `agyscope-<state>` (such as `agyscope-waiting`).

To use the shipped Catppuccin Mocha palette and state colors
([examples/themes/catppuccin-mocha.toml](https://github.com/daehyeok/agyscope/blob/main/examples/themes/catppuccin-mocha.toml)):

```sh
mkdir -p ~/.config/agyscope/themes
curl -fsSL https://raw.githubusercontent.com/daehyeok/agyscope/main/examples/themes/catppuccin-mocha.toml \
  -o ~/.config/agyscope/themes/catppuccin-mocha.toml
```

Then set `theme = "catppuccin-mocha"` in `~/.config/agyscope/config.toml`.

## Keys

agyscope is read-only, and keys not listed here do nothing. Keys in the "always" column
work in every preset. On the list screen, clicking a subtree's `▶`/`▼` icon folds or
unfolds it. On the view screen, the mouse wheel scrolls steps freely line-by-line, and
clicking an item's header toggles its fold.

| Action | Always | emacs (default) | vim |
|---|---|---|---|
| `up` / `down` | Up / Down | `C-p` / `C-n` | `k` / `j` |
| `page_up` / `page_down` | PgUp / PgDn | `M-v` / `C-v` | `C-u` `C-b` / `C-d` `C-f` |
| `first` / `last` (follow) | Home / End | `M-<` / `M->` | `g` / `G` |
| `open` (list: view subagent or fold group; view: fold/unfold) | Enter, Right | `Tab` | `Space` |
| `fold_all` | | `M-a` | `a` |
| `pager` (open full transcript in pager) | | `M-o`, `o` | `o` |
| `back` (view: return to list; list: collapse/parent) | Left, Esc | `C-g` | `Esc` |
| `close` (list: exit; view: return to list) | | `q` | `q` |

Note: Textual reports `Esc` followed by `<` or `>` as plain `<` and `>`, so `M-<` and
`M->` are bound to `<` and `>` as well (either key works). In the view screen, `q`
(`close`) returns to the list just like `back`; in the list screen, `q` exits agyscope
(in a Rex pane it also focuses the agent pane). `C-c` and `C-q` exit from either screen
without returning focus to the agent pane.

Key names for `keys` are single characters (`J`, `<`), `C-x` for Control, `M-x` for
Meta/Alt, and `Up Down Left Right PgUp PgDn Home End Enter Esc Tab Space`.

## Two screens: Subagent list and step view

**List screen.** Subagents are shown newest first, with nested subagents indented. A
whole tree is under **Working** while any member is `working`, `waiting` or `quiet`, and
under **Completed** otherwise. These are the states:

| State | Meaning |
|---|---|
| `working` | running |
| `waiting` | ended its turn after starting background work (a background command or a subagent) that hasn't reported back |
| `done` | ended its turn |
| `quiet` | not finished, and its transcript hasn't changed for 5 minutes |
| `error` | its last step failed or is an error message (a failed tool call alone is not an error) |

**Columns.** The list formats each subagent row into aligned columns:

- **Subagent** (24 cells from the tree's left edge, including tree guides and the fold
  icon): long roles end in `…` so the remaining columns line up at every depth.
- **State** (8 cells, colored).
- **Model** (22 cells, ending in `…` if longer).
- **Age** (5 cells, right-aligned).
- **Last step** (remaining width, ending in `…` if longer).

When the window is narrower than 73 columns (with default widths), **Last step** is
dropped and **Model** shrinks to fit (down to 8 cells). Below 48 columns, **Model** is
dropped too; the full model name is always shown in the view screen's header. Group rows
(`Working` and `Completed`) show only the group name.

Initial widths can be set under `[columns]` in `config.toml`. You can also drag the
column header borders horizontally with the mouse to resize columns for the current
session (dragged widths are not saved back to the config file).

**Marquee.** When the highlighted row's **Last step** is wider than its cell, it pauses
for 1.5 s, scrolls sideways a cell at a time until the end is visible, pauses for 1.5 s,
and jumps back to the start. Set `marquee = false` (or pass `--no-marquee`) to disable it.

**View screen.** Shows the selected subagent's live steps full screen, grouped under
the message that started each turn:

```
▼ Message from parent
  ▶ Analyzed BUILD #L1-674
  ▼ * Running run tests
      $ make test
      ok  pkg/a  0.12s
  ▶ Reply: Plan written.
```

Steps fold and unfold independently. Running steps are unfolded, and background commands
stream their logs live. Scrolling is line-granular: mouse wheel, page keys, or line scrolls
move through long outputs freely. When following (`⏬ follow`), new steps scroll into view
automatically; scrolling up pauses follow (`⏸ paused`), and `last` (or `End` / `G` / `M->`)
resumes following. The pager action opens the whole view, fully unfolded, in your pager.

**Navigation and preserved state.** Both screens refresh every second. On the list
screen, `open` (`Enter`, `Right`, emacs `Tab`, vim `Space`) opens the selected subagent's
view—including parent subagents that have children—or folds/unfolds a group row.
Subtrees can be folded by clicking their `▶`/`▼` icon or with `fold_all` (`M-a` / `a`),
and `back` collapses an expanded subtree or moves to its parent. The list keeps its
cursor and folds while a view is open, and each subagent remembers its own scroll
position, folds, and follow state across reopens (opening a subagent for the first time
starts in follow mode). If the subagent being viewed disappears (for example, when the
conversation switches), agyscope returns to the list automatically.

## Rex integration

For agents running in Rex terminal panes, the repository is also a Rex
plugin. It opens agyscope in a floating pane in the agent pane's window:

```
 your key --> action "agyscope" (Rex config, Lua)
                |
                +-- panel in this window?  minimized -> restore and focus
                |                          visible   -> minimize
                +-- no panel: find the agent process in the focused pane
                     none  -> small message pane "No agent in this pane"
                     found -> open a floating pane ON THE PANE'S HOST running
                              agyscope --pid <pid> --rex-block <pane>

 agyscope-rex-hook (optional, PostToolUse on invoke_subagent)
   records the pane's root conversation and opens the same panel without focus
   when the first subagent starts; the data directory comes from the payload's
   transcript path
```

`--rex-block` makes agyscope fall back to the conversation the hook recorded for that
pane, and return focus to the agent pane after `close` (`q`). Without it agyscope loads
no Rex code.

Remote panes are supported: the panel runs on the host where the agent runs and reads
that host's transcripts. [Install](#install) `agyscope` (`uv tool install agyscope`) on
every host whose panes run the agent—that provides both the `agyscope` and
`agyscope-rex-hook` Python commands. Rex itself only needs the Lua plugin.

### Lua plugin (Rex client)

Requires Rex with Lua config (`init.lua`). Clone the repository into your Rex config
directory (the clone only provides the Lua plugin; `agyscope` and `agyscope-rex-hook`
come from the PyPI [install](#install)):

```sh
git clone https://github.com/daehyeok/agyscope ~/.config/rex/plugins/agyscope
```

Load it from `~/.config/rex/init.lua` and bind any key you like. The plugin binds
none itself:

```lua
require("plugins.agyscope").setup{}

rex.bind("alt+s", "agyscope")            -- a single key
-- rex.bind("ctrl+b>a>s", "agyscope")    -- or a prefix-style sequence
```

If your config doesn't search `~/.config/rex/?/init.lua`, add the plugin's `lua/`
directory to `package.path` yourself and use `require("agyscope").setup{}`:

```lua
local dir = os.getenv("HOME") .. "/.config/rex/plugins/agyscope/lua/"
package.path = dir .. "?.lua;" .. dir .. "?/init.lua;" .. package.path
require("agyscope").setup{}
```

Check the config with `rex config check`, then reload it with `rex config reload`.

`setup(opts)` accepts these options. Unknown options and wrong types raise an error
that `rex config check` reports.

| Option | Default | Description |
|---|---|---|
| `action` | `"agyscope"` | Name of the registered action; bind your key to it. |
| `title` | `"Show/Hide Agent Subagent Panel"` | Title in Rex's action palette. |
| `agent_apps` | `{ "agy", "antigravity" }` | Program names that count as the agent. They are matched against Rex's program status, then the pane's foreground process name, path or `argv0`. |
| `command` | `{ "agyscope" }` | Panel command on the pane's host, as an argv list. It is looked up on `PATH` plus `~/.local/bin`; a leading `~/` is expanded on that host. |
| `bounds` | `{ 0.6, 0.04, 0.38, 0.42 }` | Panel position and size `{ x, y, w, h }`, as fractions of the window (top-right). |
| `panel` | `{}` | agyscope settings passed as flags: `app_dir`, `keymap`, `keys`, `pager` (see [Configuration](#configuration)). They override the host's config file for panels this action opens. `theme`, `marquee`, and `columns` are set only in `config.toml` (not via `panel`). |
| `interactive` | `true` | Run the panel through your shell with its interactive startup files (`~/.zshrc`, `~/.bashrc`), so it finds what your terminals find. See [The panel's PATH](#the-panels-path). |

Example:

```lua
require("plugins.agyscope").setup{
  bounds = { 0.55, 0.05, 0.43, 0.5 },
  panel  = { keymap = "vim", pager = { "bat", "--paging=always", "--style=plain" } },
}
```

### The hook (optional, each host that runs the agent)

`agyscope-rex-hook` is installed alongside `agyscope` by `uv tool install agyscope`. It
needs an agent CLI version that runs `PostToolUse` hooks from `~/.gemini/config/hooks.json`;
the key binding works without it. Merge
[integrations/rex/hooks.json](https://github.com/daehyeok/agyscope/blob/main/integrations/rex/hooks.json)
into `~/.gemini/config/hooks.json`:

```json
{
  "agyscope": {
    "PostToolUse": [
      { "matcher": "invoke_subagent",
        "hooks": [ { "type": "command", "command": "~/.local/bin/agyscope-rex-hook", "timeout": 5 } ] }
    ]
  }
}
```

The hook always answers `{}` at once and exits 0. It never changes or delays the
agent. Outside Rex it does nothing.

### Hook settings

The hook reads these from the same config file as agyscope
(`~/.config/agyscope/config.toml`, or `AGYSCOPE_CONFIG`):

| Setting | Default | Description |
|---|---|---|
| `bounds` | `[0.6, 0.04, 0.38, 0.42]` | Panel position and size for panels the hook opens. |
| `auto_open` | `true` | Whether the hook opens the panel on the first subagent. |
| `interactive` | `true` | Whether panels the hook opens run through your shell with its interactive startup files. See [The panel's PATH](#the-panels-path). |
| `command` | `["agyscope"]` | Panel command (argv list). A bare name is resolved next to the hook's own executable when it exists there, so the default works without `PATH`. |

Because the settings live in one file per host, panels opened by the hook and by the
key look the same.

### The panel's PATH

Rex starts the panel through your login shell on the pane's host. By default
(`interactive` is true for both the plugin and the hook) that shell also reads its
interactive startup files, such as `~/.zshrc`, so the panel sees the same `PATH` as
your terminals. The plugin also looks in `~/.local/bin`, where `uv tool install` puts
`agyscope`; it never guesses other install directories.

If your rc files are slow, print output or ask questions, set `interactive = false`
in `setup{}` and `interactive = false` in `config.toml`. Then make sure `agyscope`
is on the login `PATH` or in `~/.local/bin`, or set `command` to its full path.

## Limits

- The process-to-conversation lookup relies on the agent holding
  `<data dir>/presence/<conversation>.lock` open, and on reading the process's open
  files (`lsof`, or `/proc` on Linux; both only see your own processes). In Rex, if that
  fails, the panel falls back to the conversation the hook last recorded for the pane,
  so the hook becomes necessary.
- The agent runs the hook for every `invoke_subagent`, including those a subagent
  makes. Without a `rootConversationId` in the payload, the hook records a conversation
  only when it holds a presence lock, which only root conversations do. If the payload
  has no transcript path either, the hook can't check and records `conversationId`.
- A panel opened by the hook follows the conversation the hook last recorded for that
  pane. A panel opened by the key follows the agent process.
- Only transcripts in the Antigravity format are understood. Unknown steps are
  skipped.

## Development

```sh
uv run --python 3.13 pytest     # Python tests (pytest, including the Textual UI)
sh tests/run_all.sh             # all tests: Python (via uv) and Lua
sh tests/run_all.sh py          # Python tests only
sh tests/run_all.sh lua         # Lua tests only
pre-commit install              # optional: lint and test on every commit
```

`uv.lock` is not committed: uv resolves from your own index and keeps a local lock.
Fixtures are synthetic; `uv run python tests/py/fixtures/make_fixtures.py` regenerates
`tests/py/fixtures/app/`.

The [pre-commit](https://pre-commit.com) hooks check whitespace and YAML, run shellcheck and the
[StyLua](https://github.com/JohnnyMorganz/StyLua) format check (`stylua --check .`; fix with `stylua .`)
when they're installed, and run the tests. CI runs the Python tests with uv on Python 3.11,
3.13 and the latest 3.x, the Lua tests, and the same hooks with both tools required.

`tests/lua/manual_toggle.lua` runs the Rex action for a given pane
(`AGY_BLOCK=… AGY_SESSION=… rex do tests/lua/manual_toggle.lua`).

## Disclaimer

This is an independent, unofficial project. It is not affiliated with, endorsed by,
or supported by Google LLC or the makers of Rex. It reads files the agent CLI writes
for its own use; those formats are undocumented and may change without notice, which
can break agyscope at any time. Use it at your own risk.

## Trademarks

Antigravity, Gemini and Google are trademarks of Google LLC. Rex is a trademark of
its respective owner. Other names may be trademarks of their respective owners. They
are used here only to describe what this software works with; no endorsement is
implied.

## License

MIT, see [LICENSE](https://github.com/daehyeok/agyscope/blob/main/LICENSE).
