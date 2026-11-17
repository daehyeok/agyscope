"""Conversation links: attachments and detachments kept as files in agyscope's state directory.

    <state>/links/<parent>/<child>     <child> is attached under <parent> (empty file)
    <state>/links/_top/<conv>          <conv> is on the top-level watch list; line 1 may
                                       hold the data directory to read it from
    <state>/hidden/<parent>/<child>    <child> is detached (hidden) under <parent>
    <state>/hidden/_top/<conv>         <conv> is hidden as a root conversation

attach() writes the link and removes the matching hidden entry; detach() does the
reverse, so attaching again undoes a detach. One file per edge keeps every write atomic
(temp file + rename) without locking, whatever number of panels and commands run at once.
Reads are cached by directory mtime, so a 1 s refresh costs a few stat calls.
Standard library only.
"""

import os

TOP = "_top"
LINKED, HIDDEN = "linked", "hidden"


class LinkError(ValueError):
    pass


def check_id(conv):
    """conv if it is usable as a conversation id in a path, else raises LinkError."""
    if not isinstance(conv, str) or not conv or "/" in conv or "\\" in conv or conv.startswith(".") \
            or conv == TOP or "\0" in conv:
        raise LinkError("bad conversation id %r" % (conv,))
    return conv


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


class LinkStore:
    def __init__(self, root):
        self.root = root
        self._cache = {}  # directory -> (mtime_ns, [(name, mtime_ns)])

    def _dir(self, kind, parent):
        return os.path.join(self.root, "links" if kind == LINKED else "hidden", TOP if parent is None else parent)

    def _makedirs(self, path):
        os.makedirs(path, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def _write(self, path, text=""):
        self._makedirs(os.path.dirname(path))
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)

    def _bump_after_siblings(self, path):
        """Gives path an mtime later than every sibling's, so mtime order is attach order.

        Filesystems with coarse timestamps (ext4, tmpfs: one kernel tick) give two writes
        in quick succession the same mtime, which would leave the order to the name.
        """
        d, name = os.path.split(path)
        newest = None
        try:
            names = os.listdir(d)
        except OSError:
            names = []
        for n in names:
            if n == name or n.startswith(".") or n.endswith(".tmp"):
                continue
            m = _mtime(os.path.join(d, n))
            if m is not None and (newest is None or m > newest):
                newest = m
        own = _mtime(path)
        if newest is not None and own is not None and own <= newest:
            try:
                os.utime(path, ns=(newest + 1, newest + 1))
            except OSError:
                pass
        self._cache.pop(d, None)

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False

    def _list(self, path):
        """[(name, mtime_ns)] of the entries in path, oldest first; [] when it doesn't exist."""
        mt = _mtime(path)
        if mt is None:
            self._cache.pop(path, None)
            return []
        hit = self._cache.get(path)
        if hit is not None and hit[0] == mt:
            return hit[1]
        out = []
        try:
            names = os.listdir(path)
        except OSError:
            names = []
        for n in names:
            if n.startswith(".") or n.endswith(".tmp"):
                continue
            m = _mtime(os.path.join(path, n))
            if m is not None:
                out.append((n, m))
        out.sort(key=lambda e: (e[1], e[0]))
        self._cache[path] = (mt, out)
        return out

    # writes

    def attach(self, child, parent=None, app_dir=None):
        """Attaches child under parent (None: the top level). True if anything changed."""
        check_id(child)
        if parent is not None:
            check_id(parent)
            if parent == child:
                raise LinkError("can't attach %s under itself" % child)
        link = os.path.join(self._dir(LINKED, parent), child)
        text = (app_dir + "\n") if (parent is None and app_dir) else ""
        changed = self._remove(os.path.join(self._dir(HIDDEN, parent), child))
        try:
            with open(link, encoding="utf-8") as f:
                same = f.read() == text
        except OSError:
            same = False
        if not same:
            self._write(link, text)
            self._bump_after_siblings(link)
            changed = True
        return changed

    def detach(self, child, parent=None):
        """Removes child's link under parent and hides it there. True if anything changed."""
        check_id(child)
        if parent is not None:
            check_id(parent)
        changed = self._remove(os.path.join(self._dir(LINKED, parent), child))
        hidden = os.path.join(self._dir(HIDDEN, parent), child)
        if not os.path.exists(hidden):
            self._write(hidden)
            changed = True
        return changed

    # reads

    def children(self, parent):
        """Ids attached under parent, in attach order."""
        return [n for n, _ in self._list(self._dir(LINKED, parent))]

    def hidden(self, parent=None):
        """Ids hidden under parent (None: hidden root conversations)."""
        return {n for n, _ in self._list(self._dir(HIDDEN, parent))}

    def top(self):
        """[(conv, app_dir or None)] on the top-level watch list, in attach order."""
        d = self._dir(LINKED, None)
        out = []
        for n, _ in self._list(d):
            try:
                with open(os.path.join(d, n), encoding="utf-8") as f:
                    app_dir = f.readline().strip()
            except OSError:
                continue
            out.append((n, app_dir or None))
        return out

    def parents(self):
        """{parent: [child, ...]} of every child link (not the top level)."""
        base = os.path.join(self.root, "links")
        return {p: self.children(p) for p, _ in self._list(base) if p != TOP}

    def parents_of(self, child):
        return [p for p, kids in self.parents().items() if child in kids]

    def entries(self, main=None):
        """[(parent or None, conv, kind)] of every link and hidden entry; only main's with main."""
        out = []
        for kind in (LINKED, HIDDEN):
            base = os.path.join(self.root, "links" if kind == LINKED else "hidden")
            for p, _ in self._list(base):
                parent = None if p == TOP else p
                if main is not None and parent != main:
                    continue
                for n, _ in self._list(os.path.join(base, p)):
                    out.append((parent, n, kind))
        return out
