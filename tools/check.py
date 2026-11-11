"""This script checks how close this database's delays are to Vivado's.

For each connection of designs that Vivado placed and routed, it takes the
delay the chipdb made from this database gives the connection on Vivado's
own route -- with the load of the net's other pips on each node, as
himbaechel charges it -- and sets it against the delay in Vivado's SDF: slow corner,
maximum, in every speed grade. Beside it, for comparison, the delay of X-Ray's
model as nextpnr's xilinx uarch uses it without this database (its pips'
delays and the RC into each pip's node, without loads; 20 ps a site pip).

A build directory is what tools/routes.tcl writes in Vivado: routes.txt and
sdf/<part>/slow.sdf for each part of the device and package.

    python3 tools/check.py --nextpnr <checkout> --prjxray-db <database> \\
        xc7a100t <build dir>...

It reads the device and its sites as the uarch's generator does, with the
generator's own code: --nextpnr is a checkout whose xilinx uarch reads a
timing directory (gen/xilinx_timing.py), its site metadata (meta/) checked out.
"""
import argparse
import math
import os
import re
import sys
from collections import defaultdict

parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
parser.add_argument("--nextpnr", required=True, help="nextpnr checkout")
parser.add_argument("--prjxray-db", required=True, help="Project X-Ray database (holding artix7/, ...)")
parser.add_argument("device", help="this database's directory of the device (xc7a100t)")
parser.add_argument("builds", nargs="+", help="build directories")
parser.add_argument("--worst", type=int, default=0, help="list the N connections furthest from Vivado's at grade 1")
parser.add_argument("--by-fanout", action="store_true", help="break grade 1's error down by the fanout of each connection's net")
args = parser.parse_args()

gen = os.path.join(args.nextpnr, "himbaechel", "uarch", "xilinx", "gen")
sys.path[:0] = [gen, os.path.join(args.nextpnr, "himbaechel")]
import xilinx_device  # noqa: E402
from xilinx_timing import TimingDir  # noqa: E402

device = os.path.basename(os.path.normpath(args.device))
family = {"xc7a": "artix7", "xc7s": "spartan7", "xc7k": "kintex7", "xc7z": "zynq7"}[device[:4]]


class Pool:
    """What the database gives a chipdb's timing pool, kept by class name:
    {grade: slow max}, and each pip class's in_cap and node class's res."""

    def __init__(self):
        self.pips, self.nodes, self.in_cap, self.res = {}, {}, {}, {}

    def set_speed_grades(self, grades):
        pass

    def set_pip_class(self, g, name, v, in_cap=None):
        self.pips.setdefault(name, {})[g] = v.slow_max
        self.in_cap.setdefault(name, {})[g] = in_cap.slow_max if in_cap else 0

    def set_node_class(self, g, name, v, res=None):
        self.nodes.setdefault(name, {})[g] = v.slow_max
        self.res.setdefault(name, {})[g] = res.slow_max if res else 0

    def node_class_idx(self, name):
        return 0

    def add_cell_variant(self, g, name):
        return None


pool = Pool()
timing = TimingDir(args.device, pool)
grades = timing.grades
d = xilinx_device.import_device(device, os.path.join(args.prjxray_db, family), os.path.join(gen, "..", "meta", family))
skipped = defaultdict(int)


def node_of(name):
    tile, wire = name.split("/", 1)
    return d.tile(tile).wire(wire).node()


def tile_pip(p):
    """A routes.txt pip as (tile, tile type, src wire, dst wire)."""
    tile, rest = p[1].split("/", 1)
    tile_type, pip = rest.split(".", 1)
    src, dst = re.split(r"<<->>|->>|->", pip)
    return tile, tile_type, src, dst


def xray_pip(tile, src, dst):
    return next((x for x in d.tile(tile).pips() if x.src_wire().name() == src and x.dst_wire().name() == dst), None)


def chipdb_pip(p):
    """The chipdb's class of a routes.txt pip, X-Ray's pip, and whether the
    class is X-Ray's delay, this database having none."""
    tile, tile_type, src, dst = tile_pip(p)
    xp = xray_pip(tile, src, dst)
    name = timing.tile_pip(tile_type, src, dst)
    if name is not None:
        return name, xp, False
    return (timing.pip_class({g: [int(xp.min_delay() * 1000), int(xp.max_delay() * 1000)] * 2 for g in grades}) if xp else timing.zero()), xp, True


sites = {}


def site_path(site_pin, bel_pin, inward):
    """The chipdb's delay between a site pin and a bel pin, or None."""
    site_name, pin = site_pin.split("/")
    site_type, bel, bel_pin = bel_pin.split("/")
    if site_type not in sites:
        sv = d.site(site_name).variant(site_type)
        sites[site_type] = (sv, timing.site(sv))
    sv, site = sites[site_type]
    bel_wire = next((p.site_wire().name() for b in sv.bels() if b.name() == bel for p in b.pins() if p.name == bel_pin), None)
    if sv.pin(pin) is None or bel_wire is None:
        skipped[f"site pin or bel pin not in X-Ray's {site_type}"] += 1
        return None
    pin_wire = sv.pin(pin).site_wire().name()
    names = [site.entrance(pin_wire) if inward else site.exit(pin_wire)]
    if bel_wire != pin_wire:
        src, dst = (pin_wire, bel_wire) if inward else (bel_wire, pin_wire)
        if not any(p.src_wire().name() == src and p.dst_wire().name() == dst for p in sv.pips()):
            skipped[f"more than one site pip to {site_type}/{bel}/{bel_pin}"] += 1
            return None
        names.append(site.internal(src, dst))
    return {g: sum(pool.pips[n][g] for n in names) for g in grades}


def through_lut(cell_pin, bel_pin):
    """Where Vivado reaches a carry chain's S input through a LUT it passes
    the signal through (the cell pin a CARRY4's S[k], the bel pin a LUT's
    A6), its SDF counts the LUT in the interconnect; nextpnr places a
    feed-through LUT cell there, timed by the LUT's arc. That arc and the
    way from the LUT's O6 to the S pin, or None where this is no such pin."""
    m = re.fullmatch(r".*/S\[(\d)\]", cell_pin)
    site_type, bel, pin = bel_pin.split("/")
    if not m or not re.fullmatch(r"[A-D]6LUT", bel):
        return None
    arc = {}
    for g, entries in timing.cells.get(("LUT6", f"{site_type}/{bel}"), {}).items():
        for e in entries:
            if getattr(e, "from_pin", None) == pin and e.to_pin == "O6":
                arc[g] = e.falling.maxv * 1000
    inside = timing.delays.get(f"inside:{site_type}/{bel}/O6>{site_type}/CARRY4/S{m.group(1)}", {})
    if set(arc) != set(grades):
        return None
    return {g: arc[g] + (inside[g][3] if g in inside else 0) for g in grades}


CLOCKING = ("BUFG", "BUFGCTRL", "BUFHCE", "MMCME2_ADV", "PLLE2_ADV", "IOB33")


def clock_network(driver, sink):
    """A connection of the clock network: out of or into a clock buffer, a
    clock manager or a pad."""
    return any(p[3].split("/")[0] in CLOCKING for p in (driver, sink) if p[3])


def sdf_delays(path):
    """Each interconnect's delay, slow max in ps, by (driver pin, sink pin)."""
    with open(path) as f:
        text = f.read()
    number, unit = re.search(r"\(TIMESCALE\s+(\d+)\s*(\w+)\)", text).groups()
    ps = int(number) * {"ps": 1, "ns": 1000, "us": 1000000}[unit]
    triple = r"\(([-\d.]*):([-\d.]*):([-\d.]*)\)"
    out = {}
    for m in re.finditer(rf"\(INTERCONNECT (\S+) (\S+) {triple} {triple}\)", text):
        out[(m.group(1).replace("\\", ""), m.group(2).replace("\\", ""))] = ps * max(float(m.group(5)), float(m.group(8)))
    return out


def routes(path):
    nets, net = [], None
    with open(path) as f:
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if fields[0] == "net":
                net = {"pins": [], "pips": [], "nodes": []}
                nets.append(net)
            elif fields[0] in ("pin", "pip", "node"):
                net[fields[0] + "s"].append(fields)
    return nets


def connections(net):
    """Each connection of the net from its driver to a sink, as (driver
    pin, sink pin, the pips and the nodes it passes, and the net's pips out
    of each node)."""
    drivers = [p for p in net["pins"] if p[2] == "OUT" and len(p) > 6 and p[6]]
    if len(drivers) != 1 or not net["pips"]:
        return
    driver = drivers[0]
    # Each node's pips onward; a bidirectional pip either way.
    onward = defaultdict(list)
    for p in net["pips"]:
        onward[p[3]].append((p, p[4]))
        if "<<->>" in p[1]:
            tile, tile_type, src, dst = tile_pip(p)
            onward[p[4]].append(([p[0], f"{tile}/{tile_type}.{dst}->>{src}"] + p[2:], p[3]))
    named = {node_of(n[1]).unique_index(): n[1] for n in net["nodes"]}
    start = named.get(node_of(driver[6]).unique_index())
    if start is None:
        skipped["driver's node not in the route"] += 1
        return
    back = {start: None}
    frontier = [start]
    while frontier:
        n = frontier.pop()
        for p, m in onward[n]:
            if m not in back:
                back[m] = (p, n)
                frontier.append(m)
    out_of = defaultdict(list)
    for p, n in filter(None, back.values()):
        out_of[n].append(p)
    for sink in net["pins"]:
        if sink[2] != "IN" or len(sink) < 7 or not sink[6] or not sink[3]:
            continue
        cursor, pips, nodes = named.get(node_of(sink[6]).unique_index()), [], []
        while cursor != start and back.get(cursor) is not None:
            nodes.append(cursor)
            p, cursor = back[cursor]
            pips.append(p)
        if cursor != start:
            skipped["route not traced"] += 1
            continue
        yield driver, sink, pips, nodes + [start], out_of


errors = defaultdict(list)  # (grade, model) -> [(delay - Vivado's, Vivado's)]
worst = []  # (|error|, error, Vivado's, driver pin, sink pin, fallback pips) at grade 1
by_fanout = defaultdict(list)  # fanout bucket -> [(error, Vivado's)] at grade 1, outside the clock network
BUCKETS = ((1, 1), (2, 4), (5, 16), (17, 64), (65, 10**9))
for build in args.builds:
    sdf = {}
    for g in grades:
        parts = [p for p in os.listdir(os.path.join(build, "sdf")) if p.startswith(device) and p.endswith(f"-{g}")]
        sdf[g] = sdf_delays(os.path.join(build, "sdf", parts[0], "slow.sdf"))
    for net in routes(os.path.join(build, "routes.txt")):
        for driver, sink, pips, nodes, out_of in connections(net):
            key = (driver[1], sink[1])
            if not all(key in sdf[g] for g in grades):
                skipped["no interconnect in the SDF"] += 1
                continue
            inward = site_path(sink[4], sink[3], True)
            outward = site_path(driver[4], driver[3], False) if driver[3] else None
            if inward is None or outward is None:
                continue
            ours = {g: inward[g] + outward[g] for g in grades}
            through = through_lut(sink[1], sink[3])
            if through:
                ours = {g: ours[g] + through[g] for g in grades}
            fallbacks = 0
            xray = 40
            for p in pips:
                name, xp, fallback = chipdb_pip(p)
                if fallback:
                    skipped["pip this database has no delay for (X-Ray's)"] += 1
                    fallbacks += 1
                for g in grades:
                    ours[g] += pool.pips[name][g]
                if xray is not None and xp is not None:
                    node = node_of(p[4])
                    cap = sum(int(w.capacitance() * 1000) for w in node.wires)
                    res = sum(int(w.resistance()) for w in node.wires)
                    xray += int(xp.max_delay() * 1000) + (int(xp.resistance()) + res // 2) * cap / 1e6
                else:
                    xray = None
            for n in nodes:
                name = timing.node_class([(w.tile.tile_type(), w.name()) for w in node_of(n).wires])
                # The load: the node's res / 2 times the in_cap of the net's
                # pips out of it, mohm * fF to ps.
                caps = [pool.in_cap[chipdb_pip(p)[0]] for p in out_of[n]]
                for g in grades:
                    ours[g] += pool.nodes[name][g] if name else 0
                    ours[g] += (pool.res[name][g] // 2 if name else 0) * sum(c[g] for c in caps) / 1e6
            kind = "clock network" if clock_network(driver, sink) else "this database"
            for g in grades:
                errors[(g, kind)].append((ours[g] - sdf[g][key], sdf[g][key]))
            if "1" in grades and kind == "this database":
                fanout = sum(1 for p in net["pins"] if p[2] == "IN")
                bucket = next(b for b in BUCKETS if b[0] <= fanout <= b[1])
                by_fanout[bucket].append((ours["1"] - sdf["1"][key], sdf["1"][key]))
            if "1" in grades:
                e = ours["1"] - sdf["1"][key]
                worst.append((abs(e), e, sdf["1"][key], f"{driver[1]} ({driver[3]})", f"{sink[1]} ({sink[3]})", fallbacks))
            if xray is not None and "1" in grades:
                errors[("1", "X-Ray's")].append((xray - sdf["1"][key], sdf["1"][key]))

if args.worst:
    print(f"the {args.worst} connections furthest from Vivado's, grade 1 (error, Vivado's, driver, sink, pips without a delay):")
    for _, e, s, drv, snk, fb in sorted(worst, reverse=True)[:args.worst]:
        print(f"  {e:+7.0f} ps of {s:6.0f}  {drv} -> {snk}  {fb}")
if args.by_fanout:
    print("grade 1 outside the clock network, by the fanout of the connection's net:")
    for (lo, hi), errs in sorted(by_fanout.items()):
        n = len(errs)
        rms = math.sqrt(sum(e * e for e, _ in errs) / n)
        mean = sum(abs(e) for e, _ in errs) / sum(s for _, s in errs) * 100
        bias = sum(e for e, _ in errs) / n
        label = f"{lo}" if lo == hi else (f"{lo}-{hi}" if hi < 10**9 else f"{lo}+")
        print(f"  fanout {label:6} {n:7} connections  rms {rms:6.1f} ps  mean error {mean:5.2f} %  bias {bias:+6.1f} ps")
if skipped:
    print("skipped:", ", ".join(f"{why} {n}" for why, n in sorted(skipped.items())))
print(f"{'grade':6} {'model':14} {'connections':>11} {'rms':>9} {'mean error':>10} {'bias':>9}")
for (g, model), errs in sorted(errors.items()):
    n = len(errs)
    rms = math.sqrt(sum(e * e for e, _ in errs) / n)
    mean = sum(abs(e) for e, _ in errs) / sum(s for _, s in errs) * 100
    bias = sum(e for e, _ in errs) / n
    print(f"-{g:5} {model:14} {n:11} {rms:6.1f} ps {mean:9.2f} % {bias:+6.1f} ps")
