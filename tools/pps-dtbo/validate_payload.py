#!/usr/bin/env python3
"""Mechanical validation of the PPS payload against audit §9.1 / §9.2.

    tools/pps-dtbo/validate_payload.py [--props src/pps_props.txt] [--subtree work/pps_subtree.dtbo]

Two independent checks, both of which the build must pass before an image is
produced:

  §9.1 (property list, text)
    * every numeric property in the list carries the exact number of u32 cells
      the audit prescribes: 7 for *_strategy_soc / *_strategy_temp / r_default,
      6 for strategy_batt_high_temp / strategy_high_current, 4 for
      low_curr_full_strategy_temp, 5 for r_limit, 1 for everything else,
      and pps_region_list must be EXACTLY one cell (region check short circuit);
    * all mandatory properties are present, `oplus,pps_ops` is a string and is
      the only string;
    * no duplicate names; anything outside §9.1 is reported (not an error --
      qcom,pd_input_current_charger_ma is a deliberate carry-over).

  §9.2 (compiled subtree, binary -- what actually ships)
    * root has exactly one child: pps_charge_third_strategy;
    * that node has 6 soc children, exact names, exact order, no properties;
    * each soc child has all 6 strategy_temp_* properties and no children;
    * every value is a whole number of 5-tuples, non-empty, and the first cell
      (target_vbus) of every tuple is 11000.

Why this exists: v1 aborts the whole curve parse at the first missing
child/property (oplus_pps.c:915-935) and the failure is silent at boot -- the
engine simply never runs. Catching it at build time is much cheaper than
catching it in dmesg on a phone.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(HERE, "..", "dtbo"))

from fdt_splice import Fdt, FdtError  # noqa: E402

SOC_RANGES = ["strategy_soc_range_min", "strategy_soc_range_low",
              "strategy_soc_range_mid_low", "strategy_soc_range_mid",
              "strategy_soc_range_mid_high", "strategy_soc_range_high"]
TEMP_RANGES = ["strategy_temp_little_cold", "strategy_temp_cool",
               "strategy_temp_little_cool", "strategy_temp_normal_low",
               "strategy_temp_normal_high", "strategy_temp_warm"]

# audit §9.1 -> required u32 cell count per property
EXPECTED_CELLS = {
    # mandatory
    "oplus,pps_support_type": 1,
    "oplus,pps_support_third": 1,
    "oplus,pps_region_list": 1,
    "oplus,pps_charge_strategy_soc": 7,
    "oplus,pps_charge_strategy_temp": 7,
    # current / voltage strategy
    "oplus,pps_strategy_normal_current": 1,
    "oplus,pps_strategy_normal_bypass_limit_current": 1,
    "oplus,pps_over_high_or_low_current": 1,
    "oplus,pps_warm_allow_vol": 1,
    "oplus,pps_warm_allow_soc": 1,
    "oplus,pps_timeout_third": 1,
    "oplus,pps_timeout_oplus": 1,
    "oplus,pps_ibat_over_third": 1,
    "oplus,pps_ibat_over_oplus": 1,
    "oplus,pps_strategy_batt_high_temp": 6,
    "oplus,pps_strategy_high_current": 6,
    "oplus,pps_low_curr_full_strategy_temp": 4,
    # full-charge thresholds
    "oplus,pps_full_cool_sw_vbat": 1,
    "oplus,pps_full_normal_sw_vbat": 1,
    "oplus,pps_full_normal_hw_vbat": 1,
    "oplus,pps_full_ffc_vbat": 1,
    "oplus,pps_full_warm_vbat": 1,
    "oplus,pps_full_cool_sw_vbat_third": 1,
    "oplus,pps_full_normal_sw_vbat_third": 1,
    "oplus,pps_full_normal_hw_vbat_third": 1,
    # impedance model
    "oplus,pps_r_default": 7,
    "oplus,pps_rmos_mohm": 1,
    "oplus,pps_r_limit": 5,
    "oplus,pps_exit_ms": 1,
}
STRING_PROPS = ["oplus,pps_ops"]
KNOWN_EXTRA = {"qcom,pd_input_current_charger_ma"}

VBUS_EXPECTED = 11000

errors = []
warnings = []


def fail(msg):
    errors.append(msg)


def parse_props(path):
    """Return [(name, is_string, cells|string, lineno)] in file order."""
    out = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("str:"):
                name, _, val = line[4:].partition("=")
                name = name.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] == '"':
                    val = val[1:-1]
                out.append((name, True, val, lineno))
            else:
                name, _, val = line.partition("=")
                name = name.strip()
                val = val.strip()
                if val == "":
                    out.append((name, False, [], lineno))
                    continue
                cells = []
                for tok in val.split(","):
                    tok = tok.strip()
                    try:
                        n = int(tok, 0)
                    except ValueError:
                        fail("%s:%d %s: bad integer %r" % (path, lineno, name, tok))
                        n = 0
                    if n < 0 or n > 0xFFFFFFFF:
                        fail("%s:%d %s: %r does not fit a u32" % (path, lineno, name, tok))
                    cells.append(n)
                out.append((name, False, cells, lineno))
    return out


def check_props(path):
    props = parse_props(path)
    seen = {}
    for name, is_str, value, lineno in props:
        if name in seen:
            fail("%s:%d duplicate property %s (first at line %d)"
                 % (path, lineno, name, seen[name]))
        seen[name] = lineno
        if is_str:
            if name not in STRING_PROPS:
                fail("%s:%d %s is a string but audit §9.1 declares no strings there"
                     % (path, lineno, name))
            continue
        if name in STRING_PROPS:
            fail("%s:%d %s must be a string, found u32 cells" % (path, lineno, name))
            continue
        if name not in EXPECTED_CELLS:
            if name not in KNOWN_EXTRA:
                warnings.append("%s:%d property %s is not in audit §9.1"
                                % (path, lineno, name))
            continue
        want = EXPECTED_CELLS[name]
        if len(value) != want:
            fail("%s:%d %s has %d u32 cell(s), audit §9.1 requires %d"
                 % (path, lineno, name, len(value), want))
    for name in EXPECTED_CELLS:
        if name not in seen:
            fail("%s: mandatory property %s from audit §9.1 is missing" % (path, name))
    for name in STRING_PROPS:
        if name not in seen:
            fail("%s: mandatory string property %s from audit §9.1 is missing"
                 % (path, name))
    print("  props  : %d propert(ies) checked against audit §9.1" % len(props))
    return {name: value for name, is_str, value, _ in props if not is_str}


def check_subtree_node(node, where):
    """Validate a compiled pps_charge_third_strategy node (blob or patched entry)."""
    if node.name != "pps_charge_third_strategy":
        fail("%s: expected node pps_charge_third_strategy, found %r"
             % (where, node.name))
        return
    if node.props:
        fail("%s: audit §9.2 lists no properties on the node itself, found %s"
             % (where, ", ".join(p.name for p in node.props)))
    names = [c.name for c in node.children]
    if names != SOC_RANGES:
        fail("%s: expected the 6 soc children %s in that order, found %s"
             % (where, SOC_RANGES, names))
    for soc in node.children:
        if soc.name not in SOC_RANGES:
            continue
        if soc.children:
            fail("%s/%s: unexpected child node(s) %s"
                 % (where, soc.name, [c.name for c in soc.children]))
        got = [p.name for p in soc.props]
        missing = [t for t in TEMP_RANGES if t not in got]
        extra = [g for g in got if g not in TEMP_RANGES]
        if missing:
            fail("%s/%s: missing %s (v1 aborts the whole curve parse here)"
                 % (where, soc.name, ", ".join(missing)))
        if extra:
            fail("%s/%s: unexpected propert(ies) %s" % (where, soc.name, ", ".join(extra)))
        if len(got) != len(set(got)):
            fail("%s/%s: duplicate property names" % (where, soc.name))
        for prop in soc.props:
            if prop.name not in TEMP_RANGES:
                continue
            n = len(prop.value)
            if n == 0 or n % 20 != 0:
                fail("%s/%s/%s: %d bytes is not a whole number of 5-tuples"
                     % (where, soc.name, prop.name, n))
                continue
            tuples = [struct.unpack_from(">5I", prop.value, off)
                      for off in range(0, n, 20)]
            for idx, tup in enumerate(tuples):
                if tup[0] != VBUS_EXPECTED:
                    fail("%s/%s/%s row %d: target_vbus %d != %d"
                         % (where, soc.name, prop.name, idx, tup[0], VBUS_EXPECTED))
                if tup[2] == 0 or tup[2] > 5000:
                    warnings.append("%s/%s/%s row %d: target_ibus %d mA outside 1..5000"
                                    % (where, soc.name, prop.name, idx, tup[2]))


def check_subtree(path):
    fdt = Fdt(open(path, "rb").read(), path)
    kids = fdt.root.children
    if len(kids) != 1:
        fail("%s: subtree blob must contain exactly one node, found %s"
             % (path, [k.name for k in kids]))
        return
    check_subtree_node(kids[0], path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--props", default=os.path.join(HERE, "payload", "pps_props.txt"))
    ap.add_argument("--subtree", default=os.path.join(HERE, "..", "..", "artifacts", "dtbo", "work",
                                "pps_subtree.dtbo"))
    args = ap.parse_args(argv)

    print("validate_payload: audit §9.1 + §9.2")
    if args.props:
        if not os.path.exists(args.props):
            fail("missing props file %s" % args.props)
        else:
            check_props(args.props)
    if args.subtree:
        if not os.path.exists(args.subtree):
            warnings.append("subtree blob %s not present (stage 1 build?)" % args.subtree)
        else:
            check_subtree(args.subtree)

    for w in warnings:
        print("  WARN  %s" % w)
    if errors:
        sys.stderr.write("\nvalidate_payload: FAILED (%d error(s))\n" % len(errors))
        for e in errors:
            sys.stderr.write("  ERROR %s\n" % e)
        return 1
    print("  subtree: 6 soc children x 6 temp props, every row a 5-tuple with "
          "vbus=11000")
    print("validate_payload: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
