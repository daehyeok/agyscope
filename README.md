# rex-agy-subagent

[![test](https://github.com/daehyeok/rex-agy-subagent/actions/workflows/test.yml/badge.svg)](https://github.com/daehyeok/rex-agy-subagent/actions/workflows/test.yml)

A read-only subagent panel for Rex terminal panes running the Antigravity
agent CLI (`agy`).

When an agent in a Rex pane starts subagents, a floating panel opens with a
full-width subagent list showing each subagent's state, model, age, and last
step. Press `Enter` on any subagent to open a full-screen view of its live
steps, where you can fold turns and tool calls, scroll line-by-line, or open
the full transcript in a pager. Press `Esc` or `q` to return to the list. The
panel only reads files. It never sends anything to an agent or a terminal.

> **Unofficial.** This project is not affiliated with or endorsed by Google. See
> [Disclaimer](#disclaimer).

![demo](docs/demo.gif)
> Note: `docs/demo.gif` is stale (showing the previous UI) and will be re-recorded.

**List screen**

```
 Subagents  C-n/C-p move  Enter view  q close
 Subagent              State    Model                  Age  Last step
▼ Working
├─ ▼ Planner           waiting  Gemini 4 Carbon         4m  Plan written.
│   └─ Checker         working  gemini-3.8-flash-tie…   1m  $ make test
▼ Completed
└─ Reviewer            done     Gemini 4 Carbon         3m  No issues found.
```

**View screen** (`Enter` on a subagent; `Esc` or `q` returns to the list)

```
╭─ Checker [working · gemini-3.8-flash-tiered]  ⏬ follow ───────────────────▲╮
│ ▼ Message from parent                                                      █│
│   ▼ * Running run tests                                                    ││
│       $ make test                                                          ││
╰────────────────────────────────────────────────────────────────────────────╯
 Enter fold  M-a all  > follow  o pager  Esc back  q back
```

The panel rebuilds the tree from the agent's transcripts, so it always shows every
subagent of the session, including ones that finished before you opened it. Closing
the panel or the window loses nothing. Reopen it with your key and everything is
back.

## How it works

```
 your key --> action "agy_subagent" (Rex config, Lua)
                |
                +-- panel in this window?  minimized -> restore and focus
                |                          visible   -> minimize
                +-- no panel: find the agent process in the focused pane
                     none  -> small message pane "No agent in this pane"
                     found -> open a floating pane ON THE PANE'S HOST running
                              agy-subagent --pid <pid> --block <pane>

 agy-subagent (Python 3, stdlib launcher)
   checks for uv on PATH; if missing, displays a helpful message and waits for Enter
   launches the Textual app in a plugin-local virtualenv via uv:
     uv run --project <plugin> python -m agy_subagent_py.app ...
   (--once and --view run directly with stdlib Python without uv)
   pid -> conversation: the agent keeps <data dir>/presence/<conversation>.lock open;
          the open files come from lsof, or from /proc/<pid>/fd when lsof is missing
   the data directory is the one holding that presence/ directory, so no setting is needed
   conversation -> root transcript -> every invoke_subagent result -> child transcripts ...
   re-checks the pid every 3 s, so resuming another conversation switches the panel

 agy-subagent-hook (optional, PostToolUse on invoke_subagent)
   opens the same panel without focus when the first subagent starts; the data
   directory comes from the payload's transcript path
```

Remote panes are supported. The panel runs on the host where the agent runs and
reads that host's transcripts.

## Requirements

- **Client:** Rex with Lua config (`init.lua`).
- **Every host whose panes run the agent:** `python3` 3.9 or newer, `uv` on every host,
  `sh`, `less` or the pager you configure, and `lsof`. On Linux, `lsof` is
  optional: without it the panel reads `/proc/<pid>/fd`.
  The first panel open resolves Textual (`textual>=8.2,<9` from `pyproject.toml`) and
  installs it into `<plugin>/.venv`, so it needs network once. uv uses the host's own
  index configuration (`uv.toml`, `UV_INDEX_URL`, pip config, ...), which works both on
  machines with an internal package mirror and on personal machines using PyPI. No
  lockfile is shipped: uv writes a local, gitignored `<plugin>/uv.lock` and reuses it
  afterwards. To refresh Textual, run `uv lock --upgrade --project <plugin>` or delete
  `<plugin>/uv.lock` and `<plugin>/.venv`.
  Nothing is installed globally. (Non-interactive modes `--once` and `--view` use only
  the standard library and do not require `uv`.)
- **Agent:** the Antigravity CLI (`agy`). The hook needs a CLI version that runs
  `PostToolUse` hooks from `~/.gemini/config/hooks.json`; the key binding works without
  the hook.

## Install

### 1. The Rex plugin (client)

Clone the repository into your Rex config directory:

```sh
git clone https://github.com/daehyeok/rex-agy-subagent ~/.config/rex/plugins/rex-agy-subagent
```

Load it from `~/.config/rex/init.lua` and bind any key you like. The plugin binds
none itself:

```lua
require("plugins.rex-agy-subagent").setup{}

rex.bind("alt+s", "agy_subagent")            -- a single key
-- rex.bind("ctrl+b>a>s", "agy_subagent")    -- or a prefix-style sequence
```

If your config doesn't search `~/.config/rex/?/init.lua`, add the plugin's `lua/`
directory to `package.path` yourself and use `require("agy_subagent").setup{}`:

```lua
local dir = os.getenv("HOME") .. "/src/rex-agy-subagent/lua/"
package.path = dir .. "?.lua;" .. dir .. "?/init.lua;" .. package.path
require("agy_subagent").setup{}
```

Check the config with `rex config check`, then reload it with `rex config reload`.

### 2. The panel (each host that runs the agent)

Put `bin/agy-subagent` and `bin/agy-subagent-hook` on that host. The simplest way is
to link them into `~/.local/bin` from a clone:

```sh
git clone https://github.com/daehyeok/rex-agy-subagent ~/.local/share/rex-agy-subagent
ln -s ~/.local/share/rex-agy-subagent/bin/agy-subagent      ~/.local/bin/
ln -s ~/.local/share/rex-agy-subagent/bin/agy-subagent-hook ~/.local/bin/
```

The scripts find their Python modules through the link, so they can live anywhere.
The plugin looks for `agy-subagent` on `PATH`, then in `~/.local/bin`. For another
location, set the `command` option.

### 3. The hook (optional, each host that runs the agent)

Merge [hooks/hooks.json](hooks/hooks.json) into `~/.gemini/config/hooks.json`:

```json
{
  "rex-agy-subagent": {
    "PostToolUse": [
      { "matcher": "invoke_subagent",
        "hooks": [ { "type": "command", "command": "~/.local/bin/agy-subagent-hook", "timeout": 5 } ] }
    ]
  }
}
```

The hook always answers `{}` at once and exits 0. It never changes or delays the
agent. Outside Rex it does nothing.

## Plugin options

`setup(opts)` accepts these options. Unknown options and wrong types raise an error
that `rex config check` reports.

| Option | Default | Description |
|---|---|---|
| `action` | `"agy_subagent"` | Name of the registered action; bind your key to it. |
| `title` | `"Show/Hide Agent Subagent Panel"` | Title in Rex's action palette. |
| `agent_apps` | `{ "agy", "antigravity" }` | Program names that count as the agent. They are matched against Rex's program status, then the pane's foreground process name, path or `argv0`. |
| `command` | `{ "agy-subagent" }` | Panel command on the pane's host, as an argv list. A leading `~/` is expanded on that host. |
| `bounds` | `{ 0.6, 0.04, 0.38, 0.42 }` | Panel position and size `{ x, y, w, h }`, as fractions of the window (top-right). |
| `panel` | `{}` | Panel settings passed as flags: `app_dir`, `keymap`, `keys`, `pager` (see below). They override the host's config file for panels this action opens. |
| `interactive` | `true` | Run the panel through your shell with its interactive startup files (`~/.zshrc`, `~/.bashrc`), so it finds what your terminals find, such as `uv` from Nix, Homebrew or cargo. See [The panel's PATH](#the-panels-path). |

Example:

```lua
require("plugins.rex-agy-subagent").setup{
  bounds = { 0.55, 0.05, 0.43, 0.5 },
  panel  = { keymap = "vim", pager = { "bat", "--paging=always", "--style=plain" } },
}
```

## Panel settings

The panel and the hook read their settings on the host they run on. For each
setting, the first source that sets it wins:

1. flags (`--app-dir`, `--keymap`, `--keys-json`, `--pager-json`), which the plugin
   passes from its `panel` option;
2. environment variables `AGY_SUBAGENT_<NAME>`, for example `AGY_SUBAGENT_KEYMAP=vim`
   or `AGY_SUBAGENT_PAGER='["bat","--paging=always"]'` (lists and objects as JSON);
3. the config file `$XDG_CONFIG_HOME/rex-agy-subagent/config.json`
   (normally `~/.config/rex-agy-subagent/config.json`), or the file named by
   `AGY_SUBAGENT_CONFIG`;
4. the defaults.

The data directory is usually found without `app_dir`: from the presence lock the
agent process holds open (panels opened by the key), or from the hook payload's
transcript path (panels opened by the hook). Those win over `app_dir`, which is only
used when neither is available, for example with `--conversation`.

| Setting | Default | Description |
|---|---|---|
| `app_dir` | `"~/.gemini/antigravity-cli"` | Fallback data directory of the agent. It holds `brain/<conversation>/…/transcript.jsonl` and `presence/`. |
| `keymap` | `"emacs"` | Key preset: `"emacs"` or `"vim"`. |
| `keys` | `{}` | Extra keys per action, added to the preset: `{"down": ["J"], "close": "C-x"}`. |
| `pager` | `["less", "-R", "+G"]` | Pager argv for the pager action. The file path replaces a `{file}` argument, or is appended. |
| `bounds` | `[0.6, 0.04, 0.38, 0.42]` | Panel position and size for panels the hook opens. |
| `auto_open` | `true` | Whether the hook opens the panel on the first subagent. |
| `interactive` | `true` | Whether panels the hook opens run through your shell with its interactive startup files. `AGY_SUBAGENT_INTERACTIVE=0` turns it off. See [The panel's PATH](#the-panels-path). |

An invalid value shows a warning on the panel's bottom line, and the panel uses the
default for that setting. A config mistake never stops the panel. Because the
settings live in one file per host, panels opened by the hook and by the key look the
same.

Example `~/.config/rex-agy-subagent/config.json`:

```json
{
  "keymap": "emacs",
  "keys": { "down": ["J"], "up": ["K"] },
  "pager": ["bat", "--paging=always", "--style=plain"]
}
```

## The panel's PATH

Rex starts the panel through your login shell on the pane's host. By default
(`interactive` is true for both the plugin and the hook) that shell also reads its
interactive startup files, such as `~/.zshrc`, so the panel sees the same `PATH` as
your terminals and finds `uv` wherever you installed it (Nix profile, Homebrew,
cargo, ...). The plugin never guesses install directories; `PATH` comes from your
own shell config.

If your rc files are slow, print output or ask questions, set `interactive = false`
in `setup{}` and `"interactive": false` in the config file. Then make sure `uv` is on
the login `PATH`, for example by adding its directory in `~/.profile` or
`~/.zprofile`.

## Keys

The panel is read-only, and keys not listed here do nothing. Keys in the "always"
column work in every preset. On the list screen, clicking a subtree's `▶`/`▼`
icon folds or unfolds it. On the view screen, the mouse wheel scrolls steps freely
line-by-line, and clicking an item's header toggles its fold.

| Action | Always | emacs (default) | vim |
|---|---|---|---|
| `up` / `down` | Up / Down | `C-p` / `C-n` | `k` / `j` |
| `page_up` / `page_down` | PgUp / PgDn | `M-v` / `C-v` | `C-u` `C-b` / `C-d` `C-f` |
| `first` / `last` (follow) | Home / End | `M-<` / `M->` | `g` / `G` |
| `open` (list: view subagent or fold group; view: fold/unfold) | Enter, Right | `Tab` | `Space` |
| `fold_all` | | `M-a` | `a` |
| `pager` (open full transcript in pager) | | `M-o`, `o` | `o` |
| `back` (view: return to list; list: collapse/parent) | Left, Esc | `C-g` | `Esc` |
| `close` (list: close panel and focus agent; view: return to list) | | `q` | `q` |

Note: Textual reports `Esc` followed by `<` or `>` as plain `<` and `>`, so `M-<` and
`M->` are bound to `<` and `>` as well (either key works). In the view screen, `q`
(`close`) returns to the list just like `back`; in the list screen, `q` exits the
panel and focuses the agent pane. `C-c` and `C-q` exit the panel from either screen
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

When the panel is narrower than 73 columns, **Last step** is dropped and **Model**
shrinks to fit (down to 8 cells). Below 48 columns, **Model** is dropped too; the full
model name is always shown in the view screen's header. Group rows (`Working` and
`Completed`) show only the group name.

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
conversation switches), the panel returns to the list automatically.

## Command line

```sh
agy-subagent --once --pid 1234                # print the list once
agy-subagent --once --conversation <id>       # same, for a conversation id
agy-subagent --view <child-id-prefix> --conversation <id>   # one subagent, fully unfolded
```

## Limits

- The process-to-conversation lookup relies on the agent holding
  `<data dir>/presence/<conversation>.lock` open, and on reading the process's open
  files (`lsof`, or `/proc` on Linux; both only see your own processes). If that
  fails, the panel falls back to the conversation the hook last recorded for the
  pane, so the hook becomes necessary.
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
sh tests/run_all.sh      # all tests: Python stdlib suite, Textual suite (via uv), and Lua
sh tests/run_all.sh py   # stdlib suite only (python3)
sh tests/run_all.sh uv   # Textual suite only (uv run --project)
sh tests/run_all.sh lua  # Lua tests only
pre-commit install       # optional: lint and test on every commit
```

Tests find their modules relative to their own path, so they run from any directory.
Fixtures are synthetic; `python3 tests/py/fixtures/make_fixtures.py` regenerates
`tests/py/fixtures/app/`.

The [pre-commit](https://pre-commit.com) hooks check whitespace and YAML, run shellcheck and the
[StyLua](https://github.com/JohnnyMorganz/StyLua) format check (`stylua --check .`; fix with `stylua .`)
when they're installed, and run the tests. CI runs the Python stdlib tests, Textual tests via uv,
Lua tests, and the same hooks with both tools required.

`tests/lua/manual_toggle.lua` runs the action for a given pane
(`AGY_BLOCK=… AGY_SESSION=… rex do tests/lua/manual_toggle.lua`).

## Disclaimer

This is an independent, unofficial project. It is not affiliated with, endorsed by,
or supported by Google LLC or the makers of Rex. It reads files the agent CLI writes
for its own use; those formats are undocumented and may change without notice, which
can break the panel at any time. Use it at your own risk.

## Trademarks

Antigravity, Gemini and Google are trademarks of Google LLC. Rex is a trademark of
its respective owner. Other names may be trademarks of their respective owners. They
are used here only to describe what this software works with; no endorsement is
implied.

## License

MIT, see [LICENSE](LICENSE).
