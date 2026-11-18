"""agyscope attach / detach / links: edit the conversation link store from the command line.

    agyscope attach CONV [--under MAIN | --top] [--app-dir DIR]
    agyscope detach CONV [--under MAIN | --top] [--app-dir DIR]
    agyscope links [MAIN]

The parent is the first of: --under MAIN, --top (the top-level watch list),
$AGYSCOPE_MAIN, $ANTIGRAVITY_CONVERSATION_ID (set by the agent for the commands it runs),
else the top level. The data directory is the first of: --app-dir, $ANTIGRAVITY_APP_DATA_DIR,
the configured app_dir. CONV and MAIN may be unique prefixes of a conversation under
<data dir>/brain/. After a change, running panels are poked (see agyscope.poke).

Exit codes: 0 done, 2 invalid input (bad or ambiguous id, a cycle, unknown flag).
Only agyscope's own state directory is written.
"""

import argparse
import os
import sys

from . import config, links, poke, state
from .agentdb import AgentDB

COMMANDS = ("attach", "detach", "links")


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def parser(cmd):
    p = _Parser(prog="agyscope " + cmd, add_help=True, description=__doc__,
                formatter_class=argparse.RawDescriptionHelpFormatter)
    if cmd == "links":
        p.add_argument("main", nargs="?")
        p.add_argument("--app-dir")
        return p
    p.add_argument("conv")
    where = p.add_mutually_exclusive_group()
    where.add_argument("--under", metavar="MAIN")
    where.add_argument("--top", action="store_true")
    p.add_argument("--app-dir")
    return p


def resolve_id(app_dir, prefix, must_exist=True):
    """Full conversation id for a unique prefix under app_dir/brain, or raises UsageError."""
    links.check_id(prefix)
    brain = os.path.join(app_dir, "brain")
    if os.path.isdir(os.path.join(brain, prefix)):
        return prefix
    try:
        names = [n for n in os.listdir(brain) if n.startswith(prefix) and not n.startswith(".")]
    except OSError:
        names = []
    if len(names) == 1:
        return names[0]
    if len(names) > 1:
        raise UsageError("%r matches %d conversations: %s" % (prefix, len(names), ", ".join(sorted(names)[:5])))
    if must_exist:
        raise UsageError("no conversation %r under %s" % (prefix, brain))
    return prefix


def choose_app_dir(args, env):
    if args.app_dir:
        return os.path.expanduser(args.app_dir), "--app-dir"
    if env.get("ANTIGRAVITY_APP_DATA_DIR"):
        return env["ANTIGRAVITY_APP_DATA_DIR"], "ANTIGRAVITY_APP_DATA_DIR"
    settings, _ = config.load({}, env=env)
    return settings["app_dir"], "config"


def choose_parent(args, env):
    """(parent prefix or None, where it came from)."""
    if args.under:
        return args.under, "--under"
    if args.top:
        return None, "--top"
    for var in ("AGYSCOPE_MAIN", "ANTIGRAVITY_CONVERSATION_ID"):
        if env.get(var):
            return env[var], var
    return None, "default"


def ancestors(store, db, conv, limit=1000):
    """Every conversation above conv through child links and the agent's parent records."""
    seen, queue = set(), [conv]
    while queue and len(seen) < limit:
        cur = queue.pop()
        for p in store.parents_of(cur) + ([db.parent_of(cur)] if db.parent_of(cur) else []):
            if p not in seen:
                seen.add(p)
                queue.append(p)
    return seen


def _panels_line(store_root, parent):
    reached = poke.notify(store_root)
    if not reached:
        return "no running panel"
    if parent is None:
        n = len(reached)
    else:
        n = sum(1 for shows in reached if parent in shows)
        if n == 0:
            return "no running panel shows %s" % parent
    return "shown in %d panel%s" % (n, "" if n == 1 else "s")


def run(argv, env=None, out=None, err=None):
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    cmd, rest = argv[0], list(argv[1:])
    store = links.LinkStore(state.state_dir(env))
    try:
        args = parser(cmd).parse_args(rest)
        if cmd == "links":
            return _links(args, store, env, out)
        app_dir, app_src = choose_app_dir(args, env)
        parent_raw, parent_src = choose_parent(args, env)
        child = resolve_id(app_dir, args.conv, must_exist=(cmd == "attach"))
        parent = resolve_id(app_dir, parent_raw, must_exist=False) if parent_raw else None
        if cmd == "attach":
            if parent is not None:
                if parent == child or child in ancestors(store, AgentDB(app_dir), parent):
                    raise UsageError("attaching %s under %s would make a cycle" % (child, parent))
                store.attach(child, parent)
            else:
                store.attach(child, None, app_dir=app_dir if app_src != "config" else None)
            verb = "attached"
        else:
            store.detach(child, parent)
            verb = "detached"
    except SystemExit as e:  # --help
        return 0 if not e.code else 2
    except (UsageError, links.LinkError) as e:
        err.write("agyscope %s: %s\n" % (cmd, e))
        return 2
    where = ("under %s (from %s)" % (parent, parent_src)) if parent else \
        ("at the top level" + (" (%s)" % parent_src if parent_src != "default" else ""))
    out.write("%s %s %s; %s\n" % (verb, child, where, _panels_line(store.root, parent)))
    return 0


def _links(args, store, env, out):
    main = args.main
    if main:
        app_dir, _ = choose_app_dir(args, env)
        main = resolve_id(app_dir, main, must_exist=False)
    for parent, conv, kind in store.entries(main):
        out.write("%s %s %s\n" % (parent or "-", conv, kind))
    return 0
