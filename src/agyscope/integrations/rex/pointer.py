"""The Rex block pointer: $XDG_STATE_HOME/agyscope/blocks/<block>.

The hook rewrites it on every subagent start with that pane's latest root conversation
(line 1) and, when it knew it, the agent's data directory (line 2). Standard library only.
"""

import os

from ...state import state_dir  # noqa: F401  (re-exported; the hook and tests use pointer.state_dir)


def pointer_path(block, env=None):
    return os.path.join(state_dir(env), "blocks", block.replace("/", "_"))


def read_pointer_full(block, env=None):
    """(conversation, app_dir or None), or (None, None)."""
    try:
        with open(pointer_path(block, env), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None, None
    conv = lines[0].strip() if lines else ""
    app_dir = lines[1].strip() if len(lines) > 1 else ""
    return (conv or None), (app_dir or None) if conv else None


def read_pointer(block, env=None):
    return read_pointer_full(block, env)[0]


def write_pointer(block, conv_id, env=None, app_dir=None):
    """Atomically records conv_id (and app_dir, if known) as the block's latest root conversation."""
    path = pointer_path(block, env)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(conv_id + "\n")
        if app_dir:
            f.write(app_dir + "\n")
    os.replace(tmp, path)
