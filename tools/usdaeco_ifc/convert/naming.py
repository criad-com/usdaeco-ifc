"""Deterministic prim naming.

One documented rule, meant to be implemented identically in every
exporter (this one and any native authoring-tool add-in) so federated
layers and cross-tool overrides land on the same spatial-structure paths:

  name = sanitize(source name, else entity class)
  on sibling collision: append '_' + first 6 hex of the aeco:id (UUID)

Authoring passes visit siblings in stable identity order before calling
Namer, including catalog types (their GlobalId supplies the suffix but is
not authored as an occurrence identity). Thus the same sibling owns the
unsuffixed name after rebuilding or renumbering an IFC file.

Correctness never depends on paths (aeco:id joins, repath repairs) —
shared paths are what make sparse cross-layer `over`s line up.
"""
import re

_INVALID = re.compile(r"[^A-Za-z0-9_]")
_UNDERSCORES = re.compile(r"__+")


def sanitize(name, fallback="Prim"):
    s = _INVALID.sub("_", name or "")
    s = _UNDERSCORES.sub("_", s).strip("_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "_" + s
    return s


class Namer:
    """Per-parent unique child names, deterministic across rebuilds."""

    def __init__(self):
        self._taken = {}   # parent path str -> set of names

    def child(self, parent_path, name, uid, fallback="Prim"):
        base = sanitize(name, fallback)
        taken = self._taken.setdefault(str(parent_path), set())
        candidate = base
        if candidate in taken:
            candidate = "%s_%s" % (base, uid.replace("-", "")[:6])
        n = 2
        while candidate in taken:   # pathological double collision
            candidate = "%s_%s_%d" % (base, uid.replace("-", "")[:6], n)
            n += 1
        taken.add(candidate)
        return candidate
