#!/usr/bin/env python3
"""Extract / repack a Qualcomm-AOSP dtbo.img (header v0.4) round-trip safe.

extract <dtbo.img> <outdir>   -> outdir/manifest.json + outdir/NN_id<id>_rev<rev>.dtbo
repack  <outdir> <new.img>    -> rebuild with identical order/id/rev/custom/page_size
"""
import json
import os
import struct
import sys

MAGIC = 0xD7B7AB1E
HDR_FMT = ">8I"
HDR_SIZE = 32
ENT_FMT = ">4I4I"
ENT_SIZE = 32


def read_img(path):
    with open(path, "rb") as f:
        blob = f.read()
    magic, total, hsize, esize, count, eoff, page, ver = struct.unpack_from(HDR_FMT, blob, 0)
    if magic != MAGIC:
        raise SystemExit("not a dtbo image: magic=0x%08x" % magic)
    if hsize != HDR_SIZE or esize != ENT_SIZE:
        raise SystemExit("unsupported header (hsize=%d esize=%d)" % (hsize, esize))
    entries = []
    for i in range(count):
        off = eoff + i * ENT_SIZE
        dsize, doff, did, rev, c0, c1, c2, c3 = struct.unpack_from(ENT_FMT, blob, off)
        entries.append({
            "index": i,
            "size": dsize,
            "offset": doff,
            "id": did,
            "rev": rev,
            "custom": [c0, c1, c2, c3],
            "data": blob[doff:doff + dsize],
        })
    return {"page_size": page, "version": ver, "entries": entries}


def cmd_extract(img, outdir):
    img_data = read_img(img)
    os.makedirs(outdir, exist_ok=True)
    manifest = {"page_size": img_data["page_size"], "version": img_data["version"], "entries": []}
    for e in img_data["entries"]:
        name = "%02d_id%08x_rev%08x.dtbo" % (e["index"], e["id"], e["rev"])
        with open(os.path.join(outdir, name), "wb") as f:
            f.write(e["data"])
        manifest["entries"].append({
            "index": e["index"], "file": name, "id": e["id"], "rev": e["rev"],
            "custom": e["custom"],
        })
        print("%s  %6d bytes  id=0x%08x rev=0x%08x" % (name, e["size"], e["id"], e["rev"]))
    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print("manifest written: %s (page_size=%d, entries=%d)"
          % (os.path.join(outdir, "manifest.json"), img_data["page_size"], len(img_data["entries"])))


def cmd_repack(outdir, out_img):
    with open(os.path.join(outdir, "manifest.json")) as f:
        manifest = json.load(f)
    page = manifest["page_size"]
    version = manifest.get("version", 0)
    blobs = []
    for e in manifest["entries"]:
        with open(os.path.join(outdir, e["file"]), "rb") as f:
            blobs.append((e, f.read()))

    count = len(blobs)
    offset = HDR_SIZE + count * ENT_SIZE
    table = []
    for e, data in blobs:
        table.append((e, data, offset))
        offset += len(data)
    total = offset

    out = bytearray(total)
    struct.pack_into(HDR_FMT, out, 0, MAGIC, total, HDR_SIZE, ENT_SIZE, count,
                     HDR_SIZE, page, version)
    for i, (e, data, off) in enumerate(table):
        c = list(e["custom"]) + [0] * (4 - len(e["custom"]))
        struct.pack_into(ENT_FMT, out, HDR_SIZE + i * ENT_SIZE,
                         len(data), off, e["id"], e["rev"], c[0], c[1], c[2], c[3])
        out[off:off + len(data)] = data
    with open(out_img, "wb") as f:
        f.write(out)
    print("wrote %s (%d bytes, %d entries, page_size=%d)" % (out_img, total, count, page))


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("extract", "repack"):
        print(__doc__)
        return 1
    if sys.argv[1] == "extract":
        cmd_extract(sys.argv[2], sys.argv[3])
    else:
        cmd_repack(sys.argv[2], sys.argv[3])
    return 0


if __name__ == "__main__":
    sys.exit(main())
