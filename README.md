# Project Inkwell: Xilinx 7-series

Timing for Xilinx 7-series FPGAs in every speed grade, for
[nextpnr](https://github.com/YosysHQ/nextpnr)'s himbaechel `xilinx` uarch:
the delay of every routing pip and wire, the ways into and out of the bels
inside each site, and the timing arcs of the cells on them. It complements
[Project X-Ray](https://github.com/f4pga/prjxray-db)'s database, which gives
the uarch the device and its bitstream, and is laid out as that database is.

The data is derived by [Kraken](#provenance) from builds of the vendor's
tools; nothing in it was written by hand.

## Devices

| Device   | Speed grades |
| -------- | ------------ |
| xc7a100t | 1, 2, 2L, 3  |

## Using it with nextpnr

Point the xilinx uarch's build at this checkout:

    cmake . \
      -DARCH=himbaechel \
      -DHIMBAECHEL_UARCH=xilinx \
      -DHIMBAECHEL_PRJXRAY_DB=/path/to/prjxray-db \
      -DHIMBAECHEL_XILINX_TIMING=/path/to/xc7

A device with a directory here gets a chipdb timed in each of its grades;
`--device xc7a100tcsg324-1` then selects grade `1`. Devices without one keep
X-Ray's own delays.

## Layout

    <device>/<grade>/tile_type_<tile type>.json
    <device>/<grade>/timings/<site type>.sdf

A grade is the part's suffix (`1`, `2L`).

- **`tile_type_<tile type>.json`**: X-Ray's fields for a tile type, the
  delays only. `pips`: each pip's `src_wire`, `dst_wire` and
  `src_to_dst.delay` (a pip that goes both ways is two). `wires`: each wire's
  `delay`, which X-Ray's layout does not have: a node's delay is the sum of
  its wires'.
- **`timings/<site type>.sdf`**: the cells by type on each bel
  (`INSTANCE <site type>/<bel>`), their `IOPATH`s and `SETUPHOLD`s; and the
  site itself (`INSTANCE <site type>`): the way into a bel pin from the site's
  routing (`PORT`), out of one (`DEVICE`), and from one bel pin to another
  inside the site (`INTERCONNECT`).

Delays are in ns, as X-Ray writes them: in JSON `[fast min, fast max, slow
min, slow max]`; in SDF `(fast min::fast max)(slow min::slow max)`, and a
check's single value `(fast min::slow max)`.

The model is additive: a connection's delay is the sum of the delays of the
pips and nodes it passes and of the ways out of its source bel pin and into
its sink. There are no RC terms.

## Provenance

Kraken is a reverse-engineering system: it runs a vendor's tools on designs
of its own, records what they do and write, and infers a model of the device
from those observations, every claim traced to the builds it came from.

| Set      | Designs | What they exercise |
| -------- | ------- | ------------------ |
| timing   | 13      | one design per kind of cell the device times |
| routing  | 120     | LUTs and registers in slices drawn from the whole device, so that routes cross it in every direction and take every kind of wire |
| crossing | 40      | the wires those leave out: the bypass into a register, shared clock enables and resets, carry chains, slices far apart |
| insides  | 40      | the choices inside slices, taken many ways |
| blocks   | 40      | block RAMs and DSPs in their modes, widths and registers |

The cell arcs are the SDF's, per bel and cell type. The routing delays come
from a fit: each connection's delay in the SDF is a sum of class delays --
a pip's class, its wires' classes and the bel pins at its ends -- solved by
non-negative least squares over every connection of every build.

## Accuracy

| Grade | rms error | mean error |
| ----- | --------- | ---------- |
| 1     | 25.9 ps   | 0.55 %     |
| 2, 2L | 26.3 ps   | 0.67 %     |
| 3     | 28.6 ps   | 0.82 %     |

These connections are those the model was fitted to. Fitted on four fifths
of the builds and measured on the fifth, the error is 0.7-1.1 %. X-Ray's own
delays, as the xilinx uarch uses them without this database, are 14.5 % off
on the same connections at grade 1, about 0.45 ns fast on average.

`tools/check.py` measures this on any designs Vivado placed and routed:
`tools/routes.tcl`, sourced in Vivado with the routed design open, writes
what it reads -- each net's route and the SDF of every speed grade.

    python3 tools/check.py --nextpnr <checkout> --prjxray-db <database> \
        xc7a100t <build dir>...

## Licence

[CC0 1.0](LICENSE): no rights reserved.
