"""agyscope's own state directory: $XDG_STATE_HOME/agyscope (default ~/.local/state/agyscope).

Everything agyscope writes lives here: Rex block pointers, conversation links and the
panels' poke sockets. Standard library only.
"""

import os


def state_dir(env=None):
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "agyscope")
