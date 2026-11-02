#!/usr/bin/env python3
"""Assert that a patched dtbo entry really carries the intended PPS payload.

Usage: verify_payload.py <stock.dtbo> <patched.dtbo> <stage: 1|2>

Checks, on the node that already carries qcom,pd_input_current_charger_ma
(that is the &battery_charger overlay, whose fragment index differs per entry):
  * the anchor node is found and is unique,
  * qcom,pd_input_current_charger_ma == 3000,
  * every scalar/string property listed in src/pps_props.txt is present with the
    exact expected bytes,
  * stage 2: child pps_charge_third_strategy exists with 6 strategy_soc_range_*
    children, each carrying the 6 strategy_temp_* 5-tuple arrays,
  * nothing else in the blob changed (the tool's own semantic diff already
    proves this; here we only re-check the payload is reachable as a driver
    would see it).
Exit status 0 on success, 1 with a message on the first mismatch.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "dtbo"))
from fdt_splice import Fdt, FdtError, Prop  # noqa: E402
import validate_payload  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "payload")
PROPS = os.path.join(SRC, "pps_props.txt")

SOC_RANGES = ["strategy_soc_range_min", "strategy_soc_range_low",
              "strategy_soc_range_mid_low", "strategy_soc_range_mid",
              "strategy_soc_range_mid_high", "strategy_soc_range_high"]
TEMP_RANGES = ["strategy_temp_little_cold", "strategy_temp_cool",
               "strategy_temp_little_cool", "strategy_temp_normal_low",
               "strategy_temp_normal_high", "strategy_temp_warm"]


def load_props(path):
    props = []
    with open(path) as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("str:"):
                name, _, val = line[4:].partition("=")
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] == '"':
                    val = val[1:-1]
                props.append((name.strip(), val.encode("latin-1") + b"\0"))
            else:
                name, _, val = line.partition("=")
                val = val.strip()
                if val == "":
                    props.append((name.strip(), b""))
                else:
                    cells = [int(c, 0) for c in val.split(",")]
                    props.append((name.strip(), b"".join(struct.pack(">I", c) for c in cells)))
    return props


def fail(msg):
    sys.stderr.write("verify_payload: FAIL: %s\n" % msg)
    return 1


def anchor_overlay(fdt):
    hits = []
    for node in fdt.walk():
        ov = node.child("__overlay__")
        if ov is not None and ov.prop("qcom,pd_input_current_charger_ma") is not None:
            hits.append(ov)
    return hits


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    stock_path, patched_path, stage = argv[1], argv[2], int(argv[3])

    stock = Fdt(open(stock_path, "rb").read(), stock_path)
    patched = Fdt(open(patched_path, "rb").read(), patched_path)

    sh, ph = anchor_overlay(stock), anchor_overlay(patched)
    if len(sh) != 1:
        return fail("%s: expected exactly 1 anchor overlay in stock, got %d"
                    % (stock_path, len(sh)))
    if len(ph) != 1:
        return fail("%s: expected exactly 1 anchor overlay in patched, got %d"
                    % (patched_path, len(ph)))
    if sh[0].path != ph[0].path:
        return fail("%s: anchor overlay moved from %s to %s"
                    % (patched_path, sh[0].path, ph[0].path))
    ov = ph[0]

    for name, want in load_props(os.path.join(SRC, "pps_props.txt")):
        got = ov.prop(name)
        if got is None:
            return fail("%s: %s/%s missing" % (patched_path, ov.path, name))
        if got.value != want:
            return fail("%s: %s/%s = %s, expected %s"
                        % (patched_path, ov.path, name, got.value.hex(), want.hex()))

    # expected value is whatever src/pps_props.txt says (it is the single source
    # of truth for this property; it moved 3000 -> 5000 for the 55 W work)
    exp_pd = 3000
    try:
        for line in open(PROPS):
            line = line.strip()
            if line.startswith("qcom,pd_input_current_charger_ma="):
                exp_pd = int(line.split("=", 1)[1], 0)
    except OSError:
        pass
    got_pd = int.from_bytes(ov.prop("qcom,pd_input_current_charger_ma").value, "big")
    if got_pd != exp_pd:
        return fail("%s: pd_input_current_charger_ma is %d, expected %d"
                    % (patched_path, got_pd, exp_pd))

    if stage == 2:
        node = ov.child("pps_charge_third_strategy")
        if node is None:
            return fail("%s: %s/pps_charge_third_strategy missing"
                        % (patched_path, ov.path))
        # same structural rules the payload validator enforces on the subtree blob
        validate_payload.errors.clear()
        validate_payload.check_subtree_node(node, patched_path)
        if validate_payload.errors:
            return fail("; ".join(validate_payload.errors))

    print("verify_payload: OK %s (anchor %s, %d props, %d children)"
          % (os.path.basename(patched_path), ov.path, len(ov.props), len(ov.children)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
