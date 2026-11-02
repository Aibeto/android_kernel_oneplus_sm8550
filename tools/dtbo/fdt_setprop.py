#!/usr/bin/env python3
"""In-place patch a u32 property inside a compiled FDT/DTBO blob.

Usage: fdt_setprop.py <blob> <property-name> <new-u32-hex>
Prints the byte offsets changed. Fails if the property is absent, repeated,
or its value is not a single u32 cell.
"""
import struct
import sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def find_props(blob, want):
    magic, total, off_struct, off_strings, _rsv, _ver, _last, _cpu, size_str, _size_struct = \
        struct.unpack_from(">10I", blob, 0)
    if magic != FDT_MAGIC:
        raise SystemExit("not an FDT blob")
    out = []
    pos = off_struct
    path = []
    while True:
        tag = struct.unpack_from(">I", blob, pos)[0]
        pos += 4
        if tag == FDT_BEGIN_NODE:
            end = blob.index(b"\0", pos)
            name = blob[pos:end].decode("latin-1")
            path.append(name)
            pos = (end + 1 + 3) & ~3
        elif tag == FDT_END_NODE:
            if path:
                path.pop()
        elif tag == FDT_PROP:
            length, nameoff = struct.unpack_from(">II", blob, pos)
            pos += 8
            pend = blob.index(b"\0", off_strings + nameoff) if False else None
            pname = blob[off_strings + nameoff:off_strings + nameoff + 64].split(b"\0")[0].decode("latin-1")
            if pname == want:
                out.append(("/".join(path), pos, length))
            pos = (pos + length + 3) & ~3
        elif tag == FDT_NOP:
            continue
        elif tag == FDT_END:
            break
        else:
            raise SystemExit("bad FDT tag 0x%x at %d" % (tag, pos - 4))
    return out


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 1
    path, prop, val = sys.argv[1], sys.argv[2], int(sys.argv[3], 16)
    with open(path, "rb") as f:
        blob = bytearray(f.read())
    hits = find_props(bytes(blob), prop)
    if len(hits) != 1:
        raise SystemExit("expected exactly 1 %r, found %d: %r" % (prop, len(hits), hits))
    nodepath, off, length = hits[0]
    if length != 4:
        raise SystemExit("property %r is %d bytes, expected 4" % (prop, length))
    old = struct.unpack_from(">I", blob, off)[0]
    struct.pack_into(">I", blob, off, val)
    with open(path, "wb") as f:
        f.write(blob)
    print("node=%s offset=%d 0x%08x -> 0x%08x" % (nodepath, off, old, val))
    return 0


if __name__ == "__main__":
    sys.exit(main())
