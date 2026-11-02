#!/usr/bin/env python3
"""fdt_splice -- general Flattened Device Tree (FDT/DTBO) blob patcher.

Adds or replaces properties inside an existing node of a compiled blob and/or
splices a whole compiled *subtree* (child nodes) into that node, without
recompiling the blob from source and without reordering any existing bytes.

Why byte splice instead of "decompile -> edit -> recompile": dtc round-trips of
the real OPPO overlay blobs are lossy (438442 -> 438294 bytes), so the only
safe way to extend a stock blob is to insert tokens at one point and fix up the
header. This tool does exactly that.

Usage
-----
  fdt_splice.py <in.dtbo> <out.dtbo> --node /fragment@47/__overlay__ \
        [--set name=u32value] [--set name=0x1,0x2,0x3] [--set name] \
        [--setstr name="string"] [--splice subtree.dtbo] [--force] \
        [--quiet] [--no-dts-diff]

  fdt_splice.py --verify-only <blob> [--node <path>]
  fdt_splice.py --self-test [--test-dir DIR]

Semantics
---------
--set NAME=VALUE
    VALUE forms:  bare u32 (decimal or 0x..), comma separated u32 array, or
    empty (zero-length "boolean" property, same as just `--set NAME`).
--setstr NAME=TEXT
    TEXT may be quoted; stored as a NUL terminated string (no interior NULs).
--node PATH
    Absolute node path inside the blob, e.g. /fragment@47/__overlay__.
--splice SUBTREE.dtbo
    A blob compiled from a tiny standalone .dts with `dtc -@ -I dts -O dtb`.
    The *children of its root node* (with their properties and descendants) are
    inserted into --node. Root properties, if the author wrote any, are spliced
    as properties of --node too. __symbols__ / __fixups__ / __local_fixups__
    of the subtree are always skipped, and the tool aborts if the subtree
    references any phandle (i.e. if it carries a non-empty __fixups__ or
    __local_fixups__ node), because we cannot resolve those into a foreign blob.
--force
    Replace an existing property / child node instead of skipping it. A
    replacement whose value is already identical is a no-op, so running the
    same command twice is byte idempotent in both modes.

Verification (always on unless --no-verify)
------------------------------------------
1. Semantic, FDT level: the original and the patched blob are parsed into node
   trees, the *planned* patch is applied to an in-memory copy of the original
   tree, and the two diffs (original-vs-planned, original-vs-actual) must be
   equal as sets. Any unintended change aborts the run and no output file is
   left behind (output is written to a temp file and only renamed on success).
2. Textual, dtc level: both blobs are decompiled with `dtc -I dtb -O dts`, the
   two .dts are written next to the output and a unified diff is printed. For
   additions-only patches the diff must contain no removed line at all.

Header maintenance: totalsize, off_dt_strings, size_dt_struct, size_dt_strings
are recomputed; off_mem_rsvmap and every byte before off_dt_struct are copied
untouched; FDT_NOP tokens are preserved (they are never dropped or reordered).
"""

import argparse
import difflib
import os
import re
import shutil
import struct
import subprocess
import sys

FDT_MAGIC = 0xD00DFEED

FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9

HEADER_FMT = ">10I"
HEADER_SIZE = 40

SKIP_SUBTREE_NODES = ("__symbols__", "__fixups__", "__local_fixups__")


class FdtError(Exception):
    pass


def align4(n):
    return (n + 3) & ~3


# --------------------------------------------------------------------------
# FDT model
# --------------------------------------------------------------------------


class Prop:
    __slots__ = ("name", "value", "off", "size")

    def __init__(self, name, value, off=None, size=None):
        self.name = name
        self.value = value
        self.off = off
        self.size = size

    def __repr__(self):
        return "Prop(%r, %s)" % (self.name, self.value.hex())


class Node:
    __slots__ = ("name", "path", "begin_off", "end_off", "end_after",
                 "props", "children")

    def __init__(self, name="", path="/"):
        self.name = name
        self.path = path
        self.begin_off = None
        self.end_off = None
        self.end_after = None
        self.props = []
        self.children = []

    def prop(self, name):
        for p in self.props:
            if p.name == name:
                return p
        return None

    def child(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def child_index(self, name):
        for i, c in enumerate(self.children):
            if c.name == name:
                return i
        return -1

    def dump(self, indent=0, out=None):
        out = out if out is not None else []
        pad = "  " * indent
        out.append("%s%s {" % (pad, self.name or "/"))
        for p in self.props:
            out.append("%s  %s = %s;" % (pad, p.name, p.value.hex() or "<empty>"))
        for c in self.children:
            c.dump(indent + 1, out)
        out.append("%s}" % pad)
        return out


class Fdt:
    """Parsed view of a big-endian FDT blob (version 16/17)."""

    def __init__(self, raw, name="<blob>"):
        self.raw = raw
        self.name = name
        if len(raw) < HEADER_SIZE:
            raise FdtError("%s: too short for an FDT header" % name)
        (self.magic, self.totalsize, self.off_struct, self.off_strings,
         self.off_rsvmap, self.version, self.last_comp, self.cpu_id,
         self.size_strings, self.size_struct) = struct.unpack_from(HEADER_FMT, raw, 0)
        self.strtab = raw[self.off_strings:self.off_strings + self.size_strings]
        self._validate_header()
        self.nop_count = 0
        self.root = self._parse()

    def _validate_header(self):
        if self.magic != FDT_MAGIC:
            raise FdtError("%s: not an FDT blob (magic=0x%08x)" % (self.name, self.magic))
        if self.version < 16:
            raise FdtError("%s: unsupported FDT version %d" % (self.name, self.version))
        if self.totalsize > len(self.raw):
            raise FdtError("%s: header totalsize %d > file size %d"
                           % (self.name, self.totalsize, len(self.raw)))
        if self.off_struct + self.size_struct > self.off_strings:
            raise FdtError("%s: struct block [%d,%d) overlaps strings block at %d"
                           % (self.name, self.off_struct,
                              self.off_struct + self.size_struct, self.off_strings))
        if self.off_strings + self.size_strings > self.totalsize:
            raise FdtError("%s: strings block runs past totalsize" % self.name)
        if not (self.off_rsvmap >= HEADER_SIZE and self.off_rsvmap < self.off_struct):
            raise FdtError("%s: suspicious off_mem_rsvmap %d" % (self.name, self.off_rsvmap))
        if self.size_strings and self.strtab_is_terminated() is False:
            raise FdtError("%s: strings block is not NUL terminated" % self.name)

    def strtab_is_terminated(self):
        return self.strtab[-1:] == b"\0"

    def string_at(self, off):
        base = off
        if base < 0 or base >= len(self.strtab):
            raise FdtError("%s: string offset %d outside strings block (%d bytes)"
                           % (self.name, off, len(self.strtab)))
        end = self.strtab.find(b"\0", base)
        if end < 0:
            raise FdtError("%s: unterminated string at offset %d" % (self.name, off))
        return self.strtab[base:end].decode("latin-1")

    # -- struct parsing ----------------------------------------------------

    def _parse(self):
        raw = self.raw
        pos = self.off_struct
        limit = self.off_struct + self.size_struct

        if pos + 4 > limit or struct.unpack_from(">I", raw, pos)[0] != FDT_BEGIN_NODE:
            raise FdtError("%s: struct block does not start with FDT_BEGIN_NODE" % self.name)

        root = Node("", "/")
        root.begin_off = pos
        pos += 4
        end = raw.find(b"\0", pos, limit)
        if end < 0:
            raise FdtError("%s: unterminated root node name" % self.name)
        root.name = raw[pos:end].decode("latin-1")
        pos = align4(end + 1)

        stack = [root]
        while pos < limit:
            tok = pos
            tag = struct.unpack_from(">I", raw, pos)[0]
            pos += 4
            if tag == FDT_BEGIN_NODE:
                end = raw.find(b"\0", pos, limit)
                if end < 0:
                    raise FdtError("%s: unterminated node name at %d" % (self.name, tok))
                node = Node(raw[pos:end].decode("latin-1"))
                pos = align4(end + 1)
                node.begin_off = tok
                parent = stack[-1]
                node.path = (parent.path.rstrip("/") + "/" + node.name)
                parent.children.append(node)
                stack.append(node)
            elif tag == FDT_END_NODE:
                if not stack:
                    raise FdtError("%s: unbalanced FDT_END_NODE at %d" % (self.name, tok))
                node = stack.pop()
                node.end_off = tok
                node.end_after = pos
            elif tag == FDT_PROP:
                length, nameoff = struct.unpack_from(">II", raw, pos)
                pos += 8
                if pos + length > limit:
                    raise FdtError("%s: property value runs past struct block at %d"
                                   % (self.name, tok))
                prop = Prop(self.string_at(nameoff), raw[pos:pos + length], tok, None)
                pos = align4(pos + length)
                prop.size = pos - tok
                stack[-1].props.append(prop)
            elif tag == FDT_NOP:
                self.nop_count += 1
            elif tag == FDT_END:
                break
            else:
                raise FdtError("%s: bad FDT tag 0x%x at offset %d" % (self.name, tag, tok))

        if stack or root.end_off is None:
            raise FdtError("%s: struct block ended with %d unclosed node(s)"
                           % (self.name, len(stack)))
        return root

    # -- lookup ------------------------------------------------------------

    def node_by_path(self, path):
        if path in ("", "/"):
            return self.root
        node = self.root
        for part in [p for p in path.split("/") if p]:
            node = node.child(part)
            if node is None:
                return None
        return node

    def walk(self):
        out = []

        def rec(n):
            out.append(n)
            for c in n.children:
                rec(c)

        rec(self.root)
        return out

    def nodes_referencing(self, name):
        hits = []
        for n in self.walk():
            if n.prop(name) is not None or n.child(name) is not None:
                hits.append(n.path)
        return hits


# --------------------------------------------------------------------------
# strings block builder
# --------------------------------------------------------------------------


class StringTable:
    """Target blob's strings block + append-only additions with dedup."""

    def __init__(self, strtab):
        self.buf = bytearray(strtab)
        self.map = {}
        pos = 0
        n = len(self.buf)
        while pos < n:
            end = self.buf.find(b"\0", pos)
            if end < 0:
                raise FdtError("strings block not NUL terminated at %d" % pos)
            self.map[bytes(self.buf[pos:end]).decode("latin-1")] = pos
            pos = end + 1
        self.original_len = n
        self.added = []

    def offset(self, name):
        off = self.map.get(name)
        if off is None:
            off = len(self.buf)
            self.buf += name.encode("latin-1") + b"\0"
            self.map[name] = off
            self.added.append(name)
        return off

    @property
    def growth(self):
        return len(self.buf) - self.original_len


def prop_token(name, value, strtab):
    if not isinstance(value, (bytes, bytearray)):
        raise FdtError("prop_token: value must be bytes")
    pad = (4 - len(value) % 4) % 4
    return (struct.pack(">III", FDT_PROP, len(value), strtab.offset(name))
            + bytes(value) + b"\0" * pad)


def node_tokens(node, strtab, out=None):
    """Re-emit `node` (and descendants) as struct tokens for another blob."""
    out = out if out is not None else bytearray()
    name = node.name.encode("latin-1") + b"\0"
    out += struct.pack(">I", FDT_BEGIN_NODE) + name + b"\0" * ((4 - len(name) % 4) % 4)
    for p in node.props:
        out += prop_token(p.name, p.value, strtab)
    for c in node.children:
        node_tokens(c, strtab, out)
    out += struct.pack(">I", FDT_END_NODE)
    return out


def check_subtree_payload(sub):
    """Refuse subtrees whose values would need phandle resolution."""
    for special in ("__fixups__", "__local_fixups__"):
        n = sub.root.child(special)
        if n is not None and (n.props or n.children):
            raise FdtError(
                "subtree %s carries a non-empty %s node: it references phandles, "
                "which cannot be resolved into a foreign blob. Our payload must be "
                "self contained (no &label references)." % (sub.name, special))
    for n in sub.walk():
        for p in n.props:
            if p.name in ("phandle", "linux,phandle"):
                raise FdtError("subtree %s: node %s declares %s; refusing"
                               % (sub.name, n.path, p.name))


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------


class Plan:
    def __init__(self):
        self.edits = []          # (start, end, bytes, seq)
        self.expected = None     # Node tree after applying the plan
        self.reports = []
        self.changed = False
        self.additions_only = True


def parse_set_spec(spec):
    """'name=0x1,0x2' / 'name=42' / 'name' / 'name=' -> (name, value bytes)."""
    if "=" in spec:
        name, _, raw = spec.partition("=")
    else:
        name, raw = spec, ""
    name = name.strip()
    if not name:
        raise FdtError("empty property name in --set %r" % spec)
    raw = raw.strip()
    if raw == "":
        return name, b""
    cells = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            raise FdtError("empty cell in --set %r" % spec)
        try:
            val = int(tok, 0)
        except ValueError:
            raise FdtError("bad integer %r in --set %r" % (tok, spec))
        if val < 0 or val > 0xFFFFFFFF:
            raise FdtError("%r does not fit in a u32 cell (--set %r)" % (tok, spec))
        cells.append(val)
    return name, b"".join(struct.pack(">I", c) for c in cells)


def parse_setstr_spec(spec):
    if "=" not in spec:
        raise FdtError("--setstr needs NAME=TEXT (got %r)" % spec)
    name, _, text = spec.partition("=")
    name = name.strip()
    if not name:
        raise FdtError("empty property name in --setstr %r" % spec)
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    if "\0" in text:
        raise FdtError("interior NUL in --setstr %r" % spec)
    return name, text.encode("latin-1") + b"\0"


def clone_node(node):
    dup = Node(node.name, node.path)
    dup.begin_off = node.begin_off
    dup.end_off = node.end_off
    dup.end_after = node.end_after
    dup.props = [Prop(p.name, p.value, p.off, p.size) for p in node.props]
    dup.children = [clone_node(c) for c in node.children]
    return dup


def reparent(node, parent_path):
    node.path = parent_path.rstrip("/") + "/" + node.name
    for c in node.children:
        reparent(c, node.path)


def resolve_by_anchor(fdt, anchor_prop):
    """Find the unique N/fragment@K/__overlay__ node that directly holds anchor_prop.

    The fragment index is NOT stable across the entries of one dtbo image (the
    OnePlus 11 stock image has battery_charger at /fragment@47 in entries 02 and
    04 but at /fragment@42 in entry 08), so the target node is addressed by the
    property it already carries rather than by a hard coded path.
    """
    hits = []
    for node in fdt.walk():
        ov = node.child("__overlay__")
        if ov is not None and ov.prop(anchor_prop) is not None:
            hits.append(ov)
    if not hits:
        raise FdtError("no /fragment@N/__overlay__ node carries the anchor property "
                       "%r; cannot resolve the target node" % anchor_prop)
    if len(hits) > 1:
        raise FdtError("anchor property %r is ambiguous, %d overlays carry it: %s "
                       "(pass --node explicitly)"
                       % (anchor_prop, len(hits), ", ".join(h.path for h in hits)))
    return hits[0]


def plan_patch(fdt, args, strtab):
    """Translate CLI intent into byte edits + an expected post-patch tree."""
    plan = Plan()
    expected_root = clone_node(fdt.root)

    anchor_prop = getattr(args, "anchor_prop", None)
    if anchor_prop:
        target = resolve_by_anchor(fdt, anchor_prop)
        if args.node and args.node != target.path:
            raise FdtError("--node %r contradicts --anchor-prop %r (which resolves to %r)"
                           % (args.node, anchor_prop, target.path))
    else:
        target = fdt.node_by_path(args.node) if args.node else None
    if target is None:
        raise FdtError("node %r not found; candidates containing our props: %s"
                       % (args.node, ", ".join(fdt.nodes_referencing(
                           "qcom,pd_input_current_charger_ma")) or "<none>"))
    expected_target = expected_root if target.path == "/" else _node_by_path(expected_root, target.path)
    if expected_target is None:
        raise FdtError("internal: cannot mirror node %r into expected tree" % target.path)

    seq = 0
    new_prop_bytes = bytearray()      # inserted right after the last property
    new_child_bytes = bytearray()     # inserted right before END_NODE

    def anchor_props(node):
        if node.props:
            last = node.props[-1]
            return last.off + last.size
        if node.children:
            return node.children[0].begin_off
        return node.end_off

    # ---- scalars ---------------------------------------------------------
    seen = set()
    for spec in (args.set or []):
        name, value = parse_set_spec(spec)
        if name in seen:
            raise FdtError("property %r given twice on the command line" % name)
        seen.add(name)
        tok = prop_token(name, value, strtab)
        cur = target.prop(name)
        if cur is None:
            new_prop_bytes += tok
            expected_target.props.append(Prop(name, value))
            plan.reports.append("  + prop   %s/%s = %s" % (target.path, name, value.hex() or "<empty>"))
            plan.changed = True
        elif cur.value == value:
            plan.reports.append("  = prop   %s/%s already %s (no change)"
                                % (target.path, name, value.hex() or "<empty>"))
        elif args.force:
            plan.edits.append((cur.off, cur.off + cur.size, tok, seq)); seq += 1
            expected_target.prop(name).value = value
            plan.reports.append("  ~ prop   %s/%s -> %s (forced)" % (target.path, name, value.hex() or "<empty>"))
            plan.changed = True
            plan.additions_only = False
        else:
            plan.reports.append("  ! prop   %s/%s already exists = %s (skipped; use --force)"
                                % (target.path, name, cur.value.hex() or "<empty>"))

    # ---- strings ---------------------------------------------------------
    for spec in (args.setstr or []):
        name, value = parse_setstr_spec(spec)
        if name in seen:
            raise FdtError("property %r given twice on the command line" % name)
        seen.add(name)
        tok = prop_token(name, value, strtab)
        cur = target.prop(name)
        if cur is None:
            new_prop_bytes += tok
            expected_target.props.append(Prop(name, value))
            plan.reports.append("  + str    %s/%s = %r" % (target.path, name, value[:-1].decode("latin-1")))
            plan.changed = True
        elif cur.value == value:
            plan.reports.append("  = str    %s/%s already set (no change)" % (target.path, name))
        elif args.force:
            plan.edits.append((cur.off, cur.off + cur.size, tok, seq)); seq += 1
            expected_target.prop(name).value = value
            plan.reports.append("  ~ str    %s/%s -> %r (forced)"
                                % (target.path, name, value[:-1].decode("latin-1")))
            plan.changed = True
            plan.additions_only = False
        else:
            plan.reports.append("  ! str    %s/%s already exists (skipped; use --force)"
                                % (target.path, name))

    # ---- subtree splice --------------------------------------------------
    # Every spliced child is either inserted (absent) or replaced (present +
    # --force). Insertions are coalesced into one token blob so the target node
    # is still touched at exactly one point.
    add_child_bytes = bytearray()
    for path in (args.splice or []):
        sub = Fdt(_read(path), path)
        check_subtree_payload(sub)
        spliced = [c for c in sub.root.children if c.name not in SKIP_SUBTREE_NODES]
        if not spliced:
            raise FdtError("subtree %s has no child node to splice" % path)

        for p in sub.root.props:
            tok = prop_token(p.name, p.value, strtab)
            cur = target.prop(p.name)
            if cur is None:
                new_prop_bytes += tok
                expected_target.props.append(Prop(p.name, p.value))
                plan.reports.append("  + prop   %s/%s (from subtree root)"
                                    % (target.path, p.name))
                plan.changed = True
            elif cur.value == p.value:
                pass
            elif args.force:
                plan.edits.append((cur.off, cur.off + cur.size, tok, seq)); seq += 1
                expected_target.prop(p.name).value = p.value
                plan.reports.append("  ~ prop   %s/%s (from subtree root, forced)"
                                    % (target.path, p.name))
                plan.changed = True
                plan.additions_only = False
            else:
                plan.reports.append("  ! prop   %s/%s already exists (skipped)" % (target.path, p.name))

        for child in spliced:
            idx = target.child_index(child.name)
            mirror = clone_node(child)
            reparent(mirror, target.path)
            if idx < 0:
                add_child_bytes += node_tokens(child, strtab)
                expected_target.children.append(mirror)
                plan.reports.append("  + node   %s/%s (%d props, %d children, depth %d)"
                                    % (target.path, child.name, len(child.props),
                                       len(child.children), _depth(child)))
                plan.changed = True
            elif args.force:
                old = target.children[idx]
                plan.edits.append((old.begin_off, old.end_after,
                                   bytes(node_tokens(child, strtab)), seq)); seq += 1
                expected_target.children[idx] = mirror
                plan.reports.append("  ~ node   %s/%s replaced (forced)" % (target.path, child.name))
                plan.changed = True
                plan.additions_only = False
            else:
                plan.reports.append("  ! node   %s/%s already exists (skipped; use --force)"
                                    % (target.path, child.name))

    if new_prop_bytes:
        anchor = anchor_props(target)
        plan.edits.append((anchor, anchor, bytes(new_prop_bytes), seq)); seq += 1
    if add_child_bytes:
        plan.edits.append((target.end_off, target.end_off, bytes(add_child_bytes), seq)); seq += 1

    plan.expected = expected_root
    return plan


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _node_by_path(root, path):
    node = root
    for part in [p for p in path.split("/") if p]:
        node = node.child(part)
        if node is None:
            return None
    return node


def _find_child(root, name):
    for c in root.children:
        if c.name == name:
            return c
    raise FdtError("subtree child %r vanished" % name)


def _count_props(node):
    n = len(node.props)
    for c in node.children:
        n += _count_props(c)
    return n


def _depth(node):
    d = 0
    for c in node.children:
        d = max(d, 1 + _depth(c))
    return d


# --------------------------------------------------------------------------
# applying edits
# --------------------------------------------------------------------------


def apply_edits(fdt, edits, strtab):
    raw = fdt.raw
    # zero-width inserts sort before a replacement starting at the same offset
    edits = sorted(edits, key=lambda e: (e[0], e[1], e[3]))
    prev_end = fdt.off_struct
    for start, end, _data, _seq in edits:
        if start < prev_end:
            raise FdtError("overlapping edits at %d (previous ended %d)" % (start, prev_end))
        prev_end = end

    struct_start = fdt.off_struct
    struct_end = fdt.off_struct + fdt.size_struct
    out = bytearray()
    cur = struct_start
    for start, end, data, _seq in edits:
        if start < struct_start or end > struct_end:
            raise FdtError("edit [%d,%d) outside struct block [%d,%d)"
                           % (start, end, struct_start, struct_end))
        out += raw[cur:start]
        out += data
        cur = end
    out += raw[cur:struct_end]
    return bytes(out)


def rebuild(fdt, new_struct, strtab):
    raw = fdt.raw
    prefix = raw[:fdt.off_struct]
    rsvmap = raw[fdt.off_rsvmap:fdt.off_struct]
    if len(prefix) != fdt.off_struct or rsvmap != raw[fdt.off_rsvmap:fdt.off_struct]:
        raise FdtError("internal: prefix/rsvmap mismatch")

    # preserve any padding the original had between struct and strings blocks
    gap = raw[fdt.off_struct + fdt.size_struct:fdt.off_strings]
    pad = (-(fdt.off_struct + len(new_struct) + len(gap))) % 4
    gap = gap + b"\0" * pad
    new_off_strings = fdt.off_struct + len(new_struct) + len(gap)

    strings = bytes(strtab.buf)
    tail = raw[fdt.off_strings + fdt.size_strings:]

    out = bytearray()
    out += raw[:fdt.off_struct]
    out += new_struct
    out += gap
    out += strings
    out += tail

    total = fdt.off_struct + len(new_struct) + len(gap) + len(strings) + len(tail)
    struct.pack_into(HEADER_FMT, out, 0,
                     FDT_MAGIC, total, fdt.off_struct, new_off_strings,
                     fdt.off_rsvmap, fdt.version, fdt.last_comp, fdt.cpu_id,
                     len(strings), len(new_struct))
    return bytes(out)


# --------------------------------------------------------------------------
# semantic verification
# --------------------------------------------------------------------------


def tree_diff(a, b, path, changes):
    """Structural diff at finest granularity; entries are hashable tuples."""
    pa = {p.name: p for p in a.props}
    pb = {p.name: p for p in b.props}
    for name, prop in pa.items():
        if name not in pb:
            changes.add(("prop-", path, name, prop.value))
        elif pb[name].value != prop.value:
            changes.add(("prop~", path, name, pb[name].value))
    for name, prop in pb.items():
        if name not in pa:
            changes.add(("prop+", path, name, prop.value))
    ca = {c.name: c for c in a.children}
    cb = {c.name: c for c in b.children}
    for name, child in ca.items():
        if name not in cb:
            changes.add(("node-", path, name, sig(child)))
        else:
            tree_diff(child, cb[name], child.path, changes)
    for name, child in cb.items():
        if name not in ca:
            changes.add(("node+", path, name, sig(child)))
    return changes


def sig(node):
    return (node.name,
            tuple(sorted((p.name, p.value) for p in node.props)),
            tuple(sig(c) for c in node.children))


def semantic_diff(orig_root, new_root):
    return tree_diff(orig_root, new_root, "", set())


def format_changes(changes):
    lines = []
    for kind, path, name, payload in sorted(changes, key=lambda x: (x[1], x[0], x[2])):
        target = (path.rstrip("/") + "/" + name) if path != "" else "/" + name
        if isinstance(payload, bytes):
            body = payload.hex() or "<empty>"
        elif isinstance(payload, tuple):
            body = "<subtree>"
        else:
            body = str(payload)
        lines.append("  %-6s %s  %s" % (kind, target, body))
    return lines


def decompile(blob_path, dts_path):
    dtc = shutil.which("dtc")
    if not dtc:
        raise FdtError("dtc not found in PATH; cannot run the dtc-level verification")
    proc = subprocess.run([dtc, "-I", "dtb", "-O", "dts", "-o", dts_path, blob_path],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise FdtError("dtc failed on %s: %s"
                       % (blob_path, proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stderr.decode("utf-8", "replace")


def unified_diff(orig_dts, new_dts, label_orig, label_new):
    a = open(orig_dts, "r", encoding="utf-8", errors="replace").read().splitlines()
    b = open(new_dts, "r", encoding="utf-8", errors="replace").read().splitlines()
    return list(difflib.unified_diff(a, b, label_orig, label_new, n=3, lineterm=""))


HEADER_WORDS = ("magic", "totalsize", "off_dt_struct", "off_dt_strings",
                "off_mem_rsvmap", "version", "last_comp_version", "boot_cpuid_phys",
                "size_dt_strings", "size_dt_struct")


def header_word_diffs(a, b):
    ha = struct.unpack_from(HEADER_FMT, a, 0)
    hb = struct.unpack_from(HEADER_FMT, b, 0)
    return [(HEADER_WORDS[i], ha[i], hb[i]) for i in range(len(ha)) if ha[i] != hb[i]]


def patch_geometry(old_raw, new_raw, fdt, patched, edits, strtab):
    """Describe and *verify* the byte geometry of the patch, from the bytes back.

    For every edit we know where its payload landed in the new blob; replacing
    each payload with the bytes it displaced must reproduce the original struct
    block byte for byte. That is a real check (it walks backwards from the
    patched file), not a restatement of the plan.
    """
    lines = []
    for name, old, new in header_word_diffs(old_raw, new_raw):
        lines.append("  header  %-18s %d -> %d" % (name, old, new))
    ordered = sorted(edits, key=lambda e: (e[0], e[1], e[3]))
    delta = 0
    placed = []
    for start, end, data, _seq in ordered:
        new_start, new_end = start + delta, start + delta + len(data)
        placed.append((new_start, new_end, data, start, end))
        kind = "insert" if start == end else "replace"
        lines.append("  %-7s %d byte(s) at original struct offset %d (patched offset %d)"
                     % (kind, len(data), start, new_start))
        delta += len(data) - (end - start)

    body_off = HEADER_SIZE
    old_body = old_raw[body_off:fdt.off_struct + fdt.size_struct]
    new_body = bytearray(new_raw[body_off:patched.off_struct + patched.size_struct])
    for new_start, new_end, data, start, end in reversed(placed):
        a, b = new_start - body_off, new_end - body_off
        if bytes(new_body[a:b]) != data:
            raise FdtError("internal: patched payload at %d is not what was planned" % a)
        new_body[a:b] = old_raw[start:end]
    if bytes(new_body) != old_body:
        raise FdtError("internal: stripping the patch does not reproduce the original "
                       "struct block -- some other bytes were touched")
    lines.append("  checked: removing the patch from the patched blob reproduces the "
                 "original struct block byte for byte")

    old_str = fdt.strtab
    new_str = bytes(strtab.buf)
    if new_str[:len(old_str)] != old_str:
        raise FdtError("internal: strings block was not append-only")
    lines.append("  strings block is append-only: %d byte(s) appended at offset %d"
                 % (len(new_str) - len(old_str), len(old_str)))
    return lines


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_verify_only(args):
    raw = _read(args.verify_only)
    fdt = Fdt(raw, args.verify_only)
    print("%s: OK" % args.verify_only)
    print("  magic=0x%08x version=%d last_comp=%d totalsize=%d (file %d)"
          % (fdt.magic, fdt.version, fdt.last_comp, fdt.totalsize, len(raw)))
    print("  off_dt_struct=%d size_dt_struct=%d" % (fdt.off_struct, fdt.size_struct))
    print("  off_dt_strings=%d size_dt_strings=%d" % (fdt.off_strings, fdt.size_strings))
    print("  off_mem_rsvmap=%d  struct ends at %d (strings at %d)"
          % (fdt.off_rsvmap, fdt.off_struct + fdt.size_struct, fdt.off_strings))
    nodes = fdt.walk()
    print("  nodes=%d nops=%d" % (len(nodes), fdt.nop_count))
    if args.node:
        node = fdt.node_by_path(args.node)
        if node is None:
            raise FdtError("node %r not found" % args.node)
        print("  node %s: %d props, %d children, token span [%d,%d]"
              % (node.path, len(node.props), len(node.children),
                 node.begin_off, node.end_after))
        for p in node.props:
            print("    %s = %s" % (p.name, p.value.hex() or "<empty>"))
        for c in node.children:
            print("    child: %s (%d props, %d children)"
                  % (c.name, len(c.props), len(c.children)))
    return 0


def cmd_patch(args):
    raw = _read(args.source)
    fdt = Fdt(raw, args.source)
    strtab = StringTable(fdt.strtab)
    plan = plan_patch(fdt, args, strtab)

    print("source: %s" % args.source)
    ap = getattr(args, "anchor_prop", None)
    print("target node: %s%s" % (args.node or "<resolved by anchor>",
                                 "  [anchor prop: %s]" % ap if ap else ""))
    print("plan:")
    for line in plan.reports or ["  (nothing to do)"]:
        print(line)
    print("strings block: %d -> %d bytes (%+d), names added: %s"
          % (len(fdt.strtab), len(strtab.buf), strtab.growth,
             ", ".join(strtab.added) or "<none>"))

    if not plan.changed:
        # still emit a copy so callers get a deterministic output path
        if os.path.abspath(args.source) != os.path.abspath(args.output):
            _write_atomic(args.output, raw)
        print("no changes; output %s is byte identical to input" % args.output)
        return 0

    new_struct = apply_edits(fdt, plan.edits, strtab)
    new_raw = rebuild(fdt, new_struct, strtab)

    tmp = args.output + ".tmp"
    _write_atomic(tmp, new_raw)

    try:
        patched = Fdt(new_raw, tmp)
        _verify(fdt, patched, plan, args, tmp)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    os.replace(tmp, args.output)

    print("patched: %s" % args.output)
    print("  size %d -> %d (%+d bytes)" % (len(raw), len(new_raw), len(new_raw) - len(raw)))
    for line in patch_geometry(raw, new_raw, fdt, patched, plan.edits, strtab):
        print(line)
    print("  off_dt_strings %d -> %d, size_dt_struct %d -> %d, size_dt_strings %d -> %d"
          % (fdt.off_strings, patched.off_strings,
             fdt.size_struct, patched.size_struct,
             fdt.size_strings, patched.size_strings))
    print("  NOP tokens: %d -> %d (must be equal)" % (fdt.nop_count, patched.nop_count))
    return 0


def _write_atomic(path, data):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _verify(orig, patched, plan, args, patched_path):
    """Step 1: FDT-level semantic diff must equal the planned diff exactly."""
    expected = semantic_diff(orig.root, plan.expected)
    actual = semantic_diff(orig.root, patched.root)
    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        msg = ["semantic verification FAILED (patched tree != planned tree)"]
        if missing:
            msg.append(" missing/unrealised changes:")
            msg += format_changes(missing)
        if extra:
            msg.append(" unintended changes:")
            msg += format_changes(extra)
        raise FdtError("\n".join(msg))

    if patched.nop_count != orig.nop_count:
        raise FdtError("NOP tokens changed: %d -> %d" % (orig.nop_count, patched.nop_count))

    if args.no_verify:
        return

    # Step 2: dtc-level decompile + unified diff, written next to the output
    # (or into --dts-dir when given, to keep the entry directory clean).
    out_dir = getattr(args, "dts_dir", None) or os.path.dirname(os.path.abspath(patched_path))
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, os.path.splitext(os.path.basename(patched_path))[0])
    orig_dts = stem + ".orig.dts"
    new_dts = stem + ".new.dts"
    decompile(args.source, orig_dts)
    decompile(patched_path, new_dts)
    diff = unified_diff(orig_dts, new_dts,
                        os.path.basename(orig_dts), os.path.basename(new_dts))
    diff_path = stem + ".diff.txt"
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write("\n".join(diff) + "\n")
    if not diff:
        raise FdtError("dtc decompiles are identical, but the binary changed -- "
                       "cannot prove the patch is the intended one")
    removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
    if plan.additions_only and removed:
        raise FdtError("dtc diff contains %d removed line(s) for an additions-only "
                       "patch:\n%s" % (len(removed), "\n".join(removed[:20])))
    print("  dtc diff: %s (%d added line(s), %d removed)"
          % (diff_path, len([l for l in diff if l.startswith('+') and not l.startswith('+++')]),
             len(removed)))
    if not args.quiet:
        print("\n".join(diff))


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------


BASE_DTS = """\
/dts-v1/;

/ {
\tcompatible = "fdt-splice,self-test";
\t#address-cells = <0x01>;
\t#size-cells = <0x01>;

\tfragment@0 {
\t\ttarget = <0x01>;

\t\t__overlay__ {
\t\t\texisting_u32 = <0x10>;
\t\t\texisting_str = "hello";
\t\t\texisting_bool;

\t\t\tchild@0 {
\t\t\t\treg = <0x00>;
\t\t\t\tvalue = <0xaa 0xbb>;
\t\t\t};

\t\t\tchild@1 {
\t\t\t\treg = <0x01>;
\t\t\t};
\t\t};
\t};
};
"""

SUBTREE_DTS = """\
/dts-v1/;

/ {
\tpps_charge_third_strategy {
\t\toplus,soc_range = <0x00 0x14 0x23 0x37 0x4b 0x55 0x5a>;
\t\toplus,temp_range = <0x00 0x32 0x78 0xa0 0xd2 0x15e 0x1fe>;

\t\tstrategy_soc_range_min {
\t\t\tstrategy_temp_little_cold = <0x2af8 0x1036 0x0bb8 0x00 0x00>;
\t\t\tstrategy_temp_warm = <0x2af8 0x1022 0x073a 0x00 0x00>;
\t\t};

\t\tstrategy_soc_range_high {
\t\t\tstrategy_temp_little_cold = <0x2af8 0x1036 0x03e8 0x01 0x00>;
\t\t};
\t};
};
"""


def _dtc(args_list):
    dtc = shutil.which("dtc")
    if not dtc:
        raise FdtError("dtc not found in PATH")
    proc = subprocess.run([dtc] + args_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise FdtError("dtc %s failed: %s" % (" ".join(args_list),
                                              proc.stderr.decode("utf-8", "replace")))
    return proc.stderr.decode("utf-8", "replace")


def inject_nops(blob, count=3):
    """Insert `count` FDT_NOP tokens at token boundaries (test fixture only)."""
    fdt = Fdt(blob)
    candidates = [n.begin_off for n in fdt.walk()[1:]]
    candidates += [n.end_after for n in fdt.walk()[1:-1]]
    candidates = sorted(set(c for c in candidates if c is not None))
    if len(candidates) < count:
        raise FdtError("self-test: not enough token boundaries to inject NOPs")
    step = max(1, len(candidates) // (count + 1))
    spots = sorted(candidates[step * (i + 1)] for i in range(count))
    raw = bytearray(blob)
    out = bytearray()
    cur = 0
    for s in spots:
        out += raw[cur:s]
        out += struct.pack(">I", FDT_NOP)
        cur = s
    out += raw[cur:]
    struct.pack_into(">10I", out, 0,
                     FDT_MAGIC, len(out), fdt.off_struct,
                     fdt.off_strings + 4 * count, fdt.off_rsvmap,
                     fdt.version, fdt.last_comp, fdt.cpu_id,
                     fdt.size_strings, fdt.size_struct + 4 * count)
    return bytes(out), spots


def _patch_ns(source, output, node, sets=(), setstrs=(), splices=(), force=False,
              quiet=True, no_verify=False):
    return argparse.Namespace(source=source, output=output, node=node,
                              set=list(sets), setstr=setstrs, splice=list(splices),
                              force=force, quiet=quiet, no_verify=no_verify)


def cmd_self_test(args):
    test_dir = args.test_dir
    os.makedirs(test_dir, exist_ok=True)
    base_dts = os.path.join(test_dir, "base.dts")
    sub_dts = os.path.join(test_dir, "subtree.dts")
    base_dtbo = os.path.join(test_dir, "base.dtbo")
    sub_dtbo = os.path.join(test_dir, "subtree.dtbo")

    with open(base_dts, "w") as f:
        f.write(BASE_DTS)
    with open(sub_dts, "w") as f:
        f.write(SUBTREE_DTS)

    print("[self-test] fixtures in %s" % test_dir)
    _dtc(["-I", "dts", "-O", "dtb", "-o", base_dtbo, base_dts])
    _dtc(["-@", "-I", "dts", "-O", "dtb", "-o", sub_dtbo, sub_dts])

    base_raw = _read(base_dtbo)
    nop_raw, spots = inject_nops(base_raw, 3)
    base_nop = os.path.join(test_dir, "base_nop.dtbo")
    _write_atomic(base_nop, nop_raw)
    print("[self-test] injected 3 FDT_NOP tokens at offsets %s" % spots)

    target = "/fragment@0/__overlay__"
    sets = ("new_u32=0x1234", "new_arr=0x1,0x2,0x3", "new_bool")
    setstrs = ('new_str="world"',)
    splices = (sub_dtbo,)
    out1 = os.path.join(test_dir, "patched.dtbo")
    out2 = os.path.join(test_dir, "patched_twice.dtbo")

    print("[self-test] 1/7 patch: scalars + string + spliced subtree")
    cmd_patch(_patch_ns(base_nop, out1, target, sets, setstrs, splices))

    patched = Fdt(_read(out1))
    orig = Fdt(nop_raw)
    if patched.nop_count != orig.nop_count:
        raise FdtError("self-test: NOP tokens not preserved (%d vs %d)"
                       % (patched.nop_count, orig.nop_count))
    node = patched.node_by_path(target)
    for name, want in (("new_u32", struct.pack(">I", 0x1234)),
                       ("new_arr", struct.pack(">3I", 1, 2, 3)),
                       ("new_bool", b""),
                       ("new_str", b"world\0")):
        got = node.prop(name)
        if got is None or got.value != want:
            raise FdtError("self-test: %s wrong after patch: %r" % (name, got))
    spliced = node.child("pps_charge_third_strategy")
    if spliced is None or spliced.child("strategy_soc_range_min") is None:
        raise FdtError("self-test: spliced subtree is missing or incomplete")
    print("[self-test]    ok: additions present, spliced subtree intact, NOPs preserved")

    print("[self-test] 2/7 idempotence: the same command must be byte identical")
    cmd_patch(_patch_ns(out1, out2, target, sets, setstrs, splices))
    if _read(out1) != _read(out2):
        raise FdtError("self-test: re-run is NOT byte identical (idempotence broken)")
    print("[self-test]    ok: %s == %s" % (os.path.basename(out1), os.path.basename(out2)))

    print("[self-test] 3/7 --force replaces an existing scalar")
    out3 = os.path.join(test_dir, "patched_force.dtbo")
    cmd_patch(_patch_ns(out1, out3, target, ("existing_u32=0x99",), force=True))
    got = Fdt(_read(out3)).node_by_path(target).prop("existing_u32")
    if got is None or got.value != struct.pack(">I", 0x99):
        raise FdtError("self-test: forced replacement did not take effect")
    print("[self-test]    ok")

    print("[self-test] 4/7 without --force an existing property is skipped")
    out4 = os.path.join(test_dir, "patched_skip.dtbo")
    cmd_patch(_patch_ns(out1, out4, target, ("existing_u32=0x99",)))
    if _read(out1) != _read(out4):
        raise FdtError("self-test: skip path modified the blob")
    print("[self-test]    ok: blob untouched")

    print("[self-test] 5/7 --verify-only")
    cmd_verify_only(argparse.Namespace(verify_only=out1, node=target))
    print("[self-test]    ok")

    print("[self-test] 6/7 a phandle-carrying subtree must be rejected, leaving no output")
    bad_dts = os.path.join(test_dir, "subtree_bad.dts")
    with open(bad_dts, "w") as f:
        f.write("/dts-v1/;\n/ {\n\tref_node {\n\t\tsome-ref = <&other>;\n\t};\n"
                "\tother: other {\n\t};\n};\n")
    bad_dtbo = os.path.join(test_dir, "subtree_bad.dtbo")
    _dtc(["-@", "-I", "dts", "-O", "dtb", "-o", bad_dtbo, bad_dts])
    out5 = os.path.join(test_dir, "patched_bad.dtbo")
    if os.path.exists(out5):
        os.unlink(out5)
    try:
        cmd_patch(_patch_ns(base_nop, out5, target, splices=(bad_dtbo,)))
    except FdtError as exc:
        print("[self-test]    ok: rejected -> %s" % str(exc).splitlines()[0])
    else:
        raise FdtError("self-test: phandle-carrying subtree was NOT rejected")
    if os.path.exists(out5):
        raise FdtError("self-test: a rejected patch left an output file behind")

    print("[self-test] 7/7 a patch whose target node is absent must be rejected")
    try:
        cmd_patch(_patch_ns(base_nop, os.path.join(test_dir, "nope.dtbo"),
                            "/fragment@9/__overlay__", ("x=1",)))
    except FdtError as exc:
        print("[self-test]    ok: rejected -> %s" % str(exc).splitlines()[0])
    else:
        raise FdtError("self-test: missing node was NOT rejected")

    print("\nSELF-TEST PASS")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(
        prog="fdt_splice.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Insert properties / spliced subtrees into an existing FDT blob.")
    p.add_argument("source", nargs="?")
    p.add_argument("output", nargs="?")
    p.add_argument("--node", help="absolute node path, e.g. /fragment@47/__overlay__")
    p.add_argument("--anchor-prop", metavar="NAME",
                   help="resolve --node as the unique /fragment@N/__overlay__ that already "
                        "carries property NAME (fragment numbering differs per entry)")
    p.add_argument("--set", action="append", metavar="NAME=VALUE",
                   help="add/replace a u32 or u32 array property (or bare NAME for empty)")
    p.add_argument("--setstr", action="append", metavar="NAME=TEXT",
                   help="add/replace a string property")
    p.add_argument("--splice", action="append", metavar="SUBTREE.dtbo",
                   help="splice the children of this compiled subtree into --node")
    p.add_argument("--force", action="store_true",
                   help="replace existing properties/nodes instead of skipping")
    p.add_argument("--verify-only", metavar="BLOB", help="parse and report, change nothing")
    p.add_argument("--self-test", action="store_true", help="run the built-in self test")
    p.add_argument("--test-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     "test_splice"),
                   help="where --self-test writes its fixtures")
    p.add_argument("--no-verify", action="store_true",
                   help="skip the dtc decompile/diff step (FDT-level check still runs)")
    p.add_argument("--dts-dir", metavar="DIR",
                   help="where to write the .orig.dts/.new.dts/.diff.txt of the verification "
                        "(default: next to the output blob)")
    p.add_argument("--no-dts-diff", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--quiet", action="store_true", help="do not print the unified diff")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.self_test:
            return cmd_self_test(args)
        if args.verify_only:
            return cmd_verify_only(args)
        if not args.source or not args.output:
            build_parser().print_help()
            return 2
        if not args.node and not args.anchor_prop:
            raise FdtError("--node or --anchor-prop is required when patching")
        return cmd_patch(args)
    except FdtError as exc:
        sys.stderr.write("fdt_splice: error: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
