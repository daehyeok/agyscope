"""agyscope: read-only viewer for the subagents of an Antigravity agent (agy) session.

    agyscope [--pid PID] [--conversation ID] [settings...]
                                  Textual panel (refreshes every second)
    agyscope --once ...           print the list once as plain text
    agyscope --view CHILD ...     print one subagent's view, fully unfolded (CHILD may be a prefix)

Which conversation: --pid (the agent process; followed through the presence lock it holds
open, found with lsof or /proc), then the Rex block pointer for --rex-block, then
--conversation. The data directory comes from that lock (or the pointer) when there is one;
--app-dir and the other settings are the fallback.

Settings come from flags, then the config file ($AGYSCOPE_CONFIG or
${XDG_CONFIG_HOME:-~/.config}/agyscope/config.json):
    --app-dir DIR          agent data directory (default ~/.gemini/antigravity-cli)
    --keymap emacs|vim     panel keys (default emacs)
    --keys-json JSON       extra keys: {"down": ["J"], ...}
    --pager-json JSON      pager argv: ["bat", "--paging=always"]

Rex integration:
    --rex-block BLOCK      follow the Rex block pointer and focus BLOCK after `close`
"""

import argparse
import collections
import json
import sys
import traceback

from . import config, discovery, resolve, textview

Setup = collections.namedtuple("Setup", ("args", "settings", "warnings", "resolver", "tree", "on_close"))


def parse(argv):
    p = argparse.ArgumentParser(prog="agyscope", add_help=True, description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pid", type=int)
    p.add_argument("--rex-block", metavar="BLOCK")
    p.add_argument("--conversation")
    p.add_argument("--once", action="store_true")
    p.add_argument("--view", metavar="CHILD")
    p.add_argument("--app-dir")
    p.add_argument("--keymap")
    p.add_argument("--keys-json")
    p.add_argument("--pager-json")
    return p.parse_args(argv)


def flags_of(args):
    warnings = []
    flags = {"app_dir": args.app_dir, "keymap": args.keymap}
    for name, raw in (("keys", args.keys_json), ("pager", args.pager_json)):
        if raw is None:
            continue
        try:
            flags[name] = json.loads(raw)
        except ValueError:
            warnings.append("flag --%s-json must be JSON; ignored" % name)
    return flags, warnings


def setup(argv, env=None):
    """Parsed args, settings and the resolver. Rex code is imported only for --rex-block."""
    args = parse(argv)
    flags, warnings = flags_of(args)
    settings, more = config.load(flags, env=env)
    all_warnings = list(warnings) + list(more)
    fallback = on_close = None
    if args.rex_block:
        from .integrations.rex import focus, pointer

        def fallback():
            return pointer.read_pointer_full(args.rex_block, env)

        def on_close():
            return focus.focus_block(args.rex_block, env)
    resolver = resolve.Resolver(settings["app_dir"], pid=args.pid, conversation=args.conversation,
                                fallback=fallback)
    tree = discovery.Tree(settings["app_dir"])
    return Setup(args, settings, all_warnings, resolver, tree, on_close)


def run_text(setup):
    args = setup.args
    warnings = setup.warnings
    resolver = setup.resolver
    tree = setup.tree

    for w in warnings:
        sys.stderr.write("agyscope: %s\n" % w)
    root = resolver.current()
    if not root:
        print("No conversation found for this pane.")
        return 1
    tree.set_root(root, resolver.app_dir)
    groups = tree.rows()
    if args.view:
        for row in discovery.flat(groups):
            if row["id"].startswith(args.view):
                print("\n".join(textview.plain(tree, row)))
                return 0
        sys.stderr.write("agyscope: no subagent %s under %s\n" % (args.view, root))
        return 1
    if not discovery.flat(groups):
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
