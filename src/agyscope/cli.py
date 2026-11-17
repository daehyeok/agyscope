"""agyscope: read-only viewer for the subagents of Antigravity agent (agy) sessions.

    agyscope [--pid PID] [--conversation ID]... [settings...]
                                  Textual panel (refreshes every second)
    agyscope --once ...           print the list once as plain text
    agyscope --view ID ...        print one conversation's or subagent's view, fully unfolded
                                  (ID may be a prefix)
    agyscope attach CONV [--under MAIN | --top] [--app-dir DIR]
    agyscope detach CONV [--under MAIN | --top] [--app-dir DIR]
    agyscope links [MAIN]         edit / list conversation links (agyscope attach --help)

Which conversations: the pane's first (--pid: the agent process, followed through the
presence lock it holds open, found with lsof or /proc; then the Rex block pointer for
--rex-block; then the first --conversation), then the other --conversation ids, then the
top-level attachments, then (with live on) every running agent. The data directory comes
from the lock (or the pointer) when there is one; --app-dir is the fallback.

Settings come from flags, then the TOML config file ($AGYSCOPE_CONFIG or
${XDG_CONFIG_HOME:-~/.config}/agyscope/config.toml):
    --app-dir DIR              agent data directory (default ~/.gemini/antigravity-cli)
    --keymap emacs|vim         panel keys (default emacs)
    --keys-json JSON           extra keys: {"down": ["J"], ...}
    --pager-json JSON          pager argv: ["bat", "--paging=always"]
    --theme NAME               Textual or user theme name (default textual-dark)
    --marquee / --no-marquee   scroll the highlighted row's Last step (default on)
    --live / --no-live         also list every running agent (default on)
    --main-rows / --no-main-rows   show each conversation's own row (default on; key m toggles)

Rex integration:
    --rex-block BLOCK      follow the Rex block pointer and focus BLOCK after `close`
"""

import argparse
import collections
import json
import os
import sys
import traceback

import anyio

from . import config, discovery, links, poke, sources, state, textview

Setup = collections.namedtuple("Setup", ("args", "settings", "warnings", "collector", "forest", "on_close",
                                       "themes_dir"))


def parse(argv):
    p = argparse.ArgumentParser(prog="agyscope", add_help=True, description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pid", type=int)
    p.add_argument("--rex-block", metavar="BLOCK")
    p.add_argument("--conversation", action="append")
    p.add_argument("--once", action="store_true")
    p.add_argument("--view", metavar="CHILD")
    p.add_argument("--app-dir")
    p.add_argument("--keymap")
    p.add_argument("--keys-json")
    p.add_argument("--pager-json")
    p.add_argument("--theme")
    p.add_argument("--marquee", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--live", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--main-rows", action=argparse.BooleanOptionalAction, default=None)
    return p.parse_args(argv)


def flags_of(args):
    warnings = []
    flags = {"app_dir": args.app_dir, "keymap": args.keymap, "theme": args.theme, "marquee": args.marquee,
             "live": args.live, "main_rows": args.main_rows}
    for name, raw in (("keys", args.keys_json), ("pager", args.pager_json)):
        if raw is None:
            continue
        try:
            flags[name] = json.loads(raw)
        except ValueError:
            warnings.append("flag --%s-json must be JSON; ignored" % name)
    return flags, warnings


def setup(argv, env=None):
    """Parsed args, settings, the source collector and the forest. Rex code is imported only for --rex-block."""
    args = parse(argv)
    flags, warnings = flags_of(args)
    env_map = os.environ if env is None else env
    settings, more = config.load(flags, env=env)
    all_warnings = list(warnings) + list(more)
    fallback = on_close = None
    if args.rex_block:
        from .integrations.rex import focus, pointer

        def fallback():
            return pointer.read_pointer_full(args.rex_block, env)

        def on_close():
            return focus.focus_block(args.rex_block, env)
    store = links.LinkStore(state.state_dir(env_map))
    collector = sources.Collector(settings["app_dir"], pid=args.pid, pane_fallback=fallback,
                                  conversations=args.conversation or [], links=store, live=settings["live"])
    forest = discovery.Forest(settings["app_dir"], links=store)
    collector.services.append(poke.service(poke.socket_path(store.root), shown=lambda: forest.shown))
    themes_dir = config.themes_dir(env_map)
    return Setup(args, settings, all_warnings, collector, forest, on_close, themes_dir)


def run_text(setup):
    args = setup.args
    for w in setup.warnings:
        sys.stderr.write("agyscope: %s\n" % w)
    roots = anyio.run(setup.collector.collect)
    if not roots:
        print("No conversation found for this pane.")
        return 1
    forest = setup.forest
    forest.update(roots)
    groups = forest.rows(show_main=setup.settings["main_rows"])
    if args.view:
        for row in discovery.flat(groups):
            if row["id"].startswith(args.view):
                print("\n".join(textview.plain(forest, row)))
                return 0
        sys.stderr.write("agyscope: no conversation or subagent %s\n" % args.view)
        return 1
    if not any(not r["main"] for r in discovery.flat(groups)):
        print("No subagents yet.")
    for name, rows in groups:
        if rows:
            print(name)
            for row in rows:
                print(discovery.row_text(row))
    return 0


def _pause():
    """On a terminal, keep the pane open so the traceback stays readable."""
    try:
        isatty = getattr(sys.stdin, "isatty", None)
        if isatty is None or not isatty():
            return
        print("agyscope: crashed; press Enter to close")
        input()
    except (EOFError, KeyboardInterrupt, OSError, ValueError):
        pass


def main(argv=None):
    """Console entry point: 0 ok/closed, 1 nothing found or ctrl+c, 2 crashed.

    --once and --view print text without importing Textual. A crash prints the traceback and,
    on a terminal, waits for Enter, so a Rex pane doesn't close before it can be read.
    """
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in ("attach", "detach", "links"):
        from . import commands
        return commands.run(argv)
    try:
        args = parse(argv)
        if args.once or args.view:
            return run_text(setup(argv))
        from . import app
        rc = app.run(setup(argv))
    except Exception:
        traceback.print_exc()
        _pause()
        return 2
    if rc == 2:
        _pause()
    return rc
