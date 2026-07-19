"""Cross-site model identity links ("aka groups").

A link group is a list of "site:name" keys that all belong to one person —
e.g. ["chaturbate:alice", "stripchat:alice", "camsoda:alice_xx"]. Groups are
stored in settings as the additive top-level key `model_links` (list of
lists); `link_ignores` holds dismissed same-name suggestions. Neither key
touches the existing models / saved_models / ranks structures, so configs
from older versions load (and keep working) unchanged.

Pure data helpers only — no app or UI imports. Callers hold whatever lock
guards their shared state; every mutator works in place on the list it is
given.
"""

from typing import List, Optional

Key = str            # "site:name", lowercase
Group = List[Key]


def norm_key(name: str, site: str) -> Key:
    return f"{site}:{name}".strip().lower()


def split_key(key: Key) -> tuple:
    """"site:name" → (name, site)."""
    site, _, name = key.partition(":")
    return name, site


def sanitize(raw) -> List[Group]:
    """Validate a stored model_links value: lowercase, dedupe keys within and
    across groups (first group wins), drop malformed keys and groups that end
    up with fewer than 2 members. Never raises — a corrupt value degrades to
    fewer/no links, not a crash."""
    out: List[Group] = []
    seen: set = set()
    if not isinstance(raw, (list, tuple)):
        return out
    for g in raw:
        if not isinstance(g, (list, tuple)):
            continue
        group: Group = []
        for k in g:
            if not isinstance(k, str) or ":" not in k:
                continue
            k = k.strip().lower()
            name, site = split_key(k)
            if not name or not site or k in seen:
                continue
            seen.add(k)
            group.append(k)
        if len(group) >= 2:
            out.append(group)
    return out


def find_group(links: List[Group], key: Key) -> Optional[Group]:
    for g in links:
        if key in g:
            return g
    return None


def aka(links: List[Group], key: Key) -> List[Key]:
    """Other members of key's group ([] when unlinked)."""
    g = find_group(links, key)
    return [k for k in g if k != key] if g else []


def link(links: List[Group], key_a: Key, key_b: Key) -> Group:
    """Link two models (in place). Merges their groups if both are already
    linked elsewhere; returns the resulting group."""
    if key_a == key_b:
        raise ValueError("cannot link a model to itself")
    ga = find_group(links, key_a)
    gb = find_group(links, key_b)
    if ga is not None and ga is gb:
        return ga                       # already linked together
    if ga is None and gb is None:
        g: Group = [key_a, key_b]
        links.append(g)
        return g
    if ga is not None and gb is not None:   # merge two existing groups
        ga.extend(k for k in gb if k not in ga)
        links.remove(gb)
        return ga
    g = ga if ga is not None else gb
    g.append(key_b if ga is not None else key_a)
    return g


def unlink(links: List[Group], key: Key) -> bool:
    """Remove one model from its group (in place). A group left with a single
    member is dissolved. Returns False when the model wasn't linked."""
    g = find_group(links, key)
    if g is None:
        return False
    g.remove(key)
    if len(g) < 2:
        links.remove(g)
    return True


def suggestions(links: List[Group], tracked_keys, ignores) -> List[dict]:
    """Same-username-on-another-site suggestions: for every name that appears
    on 2+ sites among tracked models, propose the keys that aren't already all
    in one group. `ignores` is a list of dismissed suggestions (each a sorted
    key list); a dismissed set stays hidden until its membership changes
    (e.g. the same name shows up on a third site)."""
    by_name: dict = {}
    for k in tracked_keys:
        name, _site = split_key(k)
        by_name.setdefault(name, []).append(k)
    ignore_set = {tuple(sorted(x)) for x in (ignores or [])}
    out = []
    for name, keys in sorted(by_name.items()):
        if len(keys) < 2:
            continue
        keys = sorted(set(keys))
        g = find_group(links, keys[0])
        if g is not None and all(k in g for k in keys):
            continue                    # already fully linked
        if tuple(keys) in ignore_set:
            continue
        out.append({"name": name, "keys": keys})
    return out
