"""Command-line parsing, setup and non-interactive text output for agy-subagent."""

import argparse
import collections
import json
import sys

from . import config, discovery, resolve, textview

Setup = collections.namedtuple("Setup", ("args", "settings", "warnings", "resolver", "tree"))


def parse(argv):
    p = argparse.ArgumentParser(prog="agy-subagent", add_help=True)
    p.add_argument("--pid", type=int)
    p.add_argument("--block")
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
    args = parse(argv)
    flags, warnings = flags_of(args)
    settings, more = config.load(flags, env=env)
    all_warnings = list(warnings) + list(more)
    resolver = resolve.Resolver(settings["app_dir"], pid=args.pid, block=args.block,
                                conversation=args.conversation)
    tree = discovery.Tree(settings["app_dir"])
    return Setup(args, settings, all_warnings, resolver, tree)


def run_text(setup):
    args = setup.args
    warnings = setup.warnings
    resolver = setup.resolver
    tree = setup.tree

    for w in warnings:
        sys.stderr.write("agy-subagent: %s\n" % w)
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
        sys.stderr.write("agy-subagent: no subagent %s under %s\n" % (args.view, root))
        return 1
    if not discovery.flat(groups):
        print("No subagents yet.")
    for name, rows in groups:
        if rows:
            print(name)
            for row in rows:
                print(discovery.row_text(row))
    return 0
