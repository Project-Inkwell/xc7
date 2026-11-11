# Project Inkwell: Xilinx 7-series

Nextpnr PR: [#1901](https://github.com/YosysHQ/nextpnr/pull/1901)

Timing for Xilinx 7-series FPGAs in every speed grade, for [nextpnr](https://github.com/YosysHQ/nextpnr)'s himbaechel `xilinx` uarch. This database complements [Project X-Ray](https://github.com/f4pga/prjxray-db), to provide more accurate timing. This data was obtained through solving for slice-internal/external delays from the timing files and routes the vendor's tool writes.

The data is derived by [Kraken](#how-the-data-was-found) from builds of the vendor's
tools; nothing in it was written by hand.

## Devices

| Device   | Speed grades |
| -------- | ------------ |
| xc7a100t | 1, 2, 2L, 3  |

## Using it with nextpnr

The xilinx uarch reads this database through a configuration option:

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

- **`tile_type_<tile type>.json`**: X-Ray's fields for a tile type, containing only the timing data. `pips`: each pip's `src_wire`, `dst_wire`, `src_to_dst.delay` and `src_to_dst.in_cap` (a pip that goes both ways is two). `wires`: each wire's `delay`, which X-Ray's layout does not have, and `res`: a node's delay and resistance are the sums of its wires'.
- **`timings/<site type>.sdf`**: the cells by type on each bel (`INSTANCE <site type>/<bel>`), their `IOPATH`s and `SETUPHOLD`s, and the site itself (`INSTANCE <site type>`): the way into a bel pin from the site's routing (`PORT`), out of one (`DEVICE`), and from one bel pin to another inside the site (`INTERCONNECT`).

Delays are in ns, as X-Ray writes them: in JSON `[fast min, fast max, slow min, slow max]`; in SDF `(fast min::fast max)(slow min::slow max)`, and a check's single value `(fast min::slow max)`. `res` is in ohm and `in_cap` in fF.

The delay of a connection is modelled as the sum of the delays of the pips and nodes it passes, and of the ways out of its source bel pin and into its sink, plus the load on each node it passes. Every pip the net takes out of the node adds `res * in_cap / 2`. Every `in_cap` is 1 fF, so a wire's load per pip (in ps) is equal to `res` / 2000. A node that never branches has a `res` of 0. In this case, the load is already part of its delay.

## How the data was found

This timing DB was semi-autonomously derived using an unpublished reverse-engineering toolkit called `Kraken`. Kraken runs a vendor's tools on a variety of designs, records memory/IO accesses, logs and build artifacts. These data points are stored in a large database, and "inference" rules (constraint based solving with least squares over the 426094 connections of all 292 builds) are then used to infer device information from these observations. For the creation of this DB, only build artifacts and log outputs were used.

| Design set | Number of designs | Details            |
| ---------- | ----------------- | ------------------ |
| timing     | 13                | one design per kind of cell the device times |
| routing    | 120               | LUTs and registers in slices drawn from the whole device, so that routes cross it in every direction and take every kind of wire |
| crossing   | 40                | the wires those leave out: the bypass into a register, shared clock enables and resets, carry chains, slices far apart |
| insides    | 40                | the choices inside slices, taken many ways |
| blocks     | 40                | block RAMs and DSPs in their modes, widths and registers |
| circuits   | 39                | clocks from an MMCM or PLL, adders and counters, shift registers, nets fanning out to 400 loads |

## Accuracy

To measure the accuracy, we used 80% of the builds for constraint solving, and left the remaining 20% out deliberately to act as a test dataset. RMS and mean are used as error metrics versus the vendor tool, in the slow corner.

| Grade | rms error | mean error |
| ----- | --------- | ---------- |
| 1     | 21 ps     | 0.63 %     |
| 2, 2L | 18 ps     | 0.66 %     |
| 3     | 16 ps     | 0.67 %     |

This DB was also tested on six designs that are not in the training set, placed/routed by the vendor tool, with each connection's delay summed as nextpnr does from this database. These are the results:

(The designs contain a LiteX SoC, block RAM designs, LUT RAM with shift registers, a self-checking adder tree clocked by an MMCM, and two blinkies)

| Grade | Connections          | rms error | mean error | bias     |
| ----- | -------------------- | --------- | ---------- | -------- |
| 1     | fabric, 22,792       | 29.0 ps   | 0.76 %     | -3.6 ps  |
| 1     | clock network, 3,028 | 70.6 ps   | 2.84 %     | +31.8 ps |
| 2, 2L | fabric               | 25.0 ps   | 0.90 %     | -4.6 ps  |
| 3     | fabric               | 22.3 ps   | 0.90 %     | -4.4 ps  |

By the fanout of the connection's net, grade 1, fabric:

| Fanout | Connections | rms error | mean error | bias    |
| ------ | ----------- | --------- | ---------- | ------- |
| 1      | 3,235       | 34.6 ps   | 1.69 %     | +1.5 ps |
| 2-4    | 6,079       | 31.9 ps   | 1.07 %     | -5.0 ps |
| 5-16   | 5,052       | 32.7 ps   | 0.91 %     | -4.4 ps |
| 17-64  | 4,797       | 25.7 ps   | 0.72 %     | -6.0 ps |
| 65+    | 3,629       | 12.1 ps   | 0.31 %     | -1.7 ps |

For comparison, X-Ray's own delays, which the uarch uses without this database, are 34.8 % off on the same connections at grade 1, 0.47 ns fast on average.

`tools/check.py` measures this on any designs the vendor tool placed and routed: `tools/routes.tcl`, sourced in the vendor tool with the routed design open, writes what it reads -- each net's route and the SDF of every speed grade.

    python3 tools/check.py --nextpnr <checkout> --prjxray-db <database> \
        xc7a100t <build dir>...

`--by-fanout` adds the table by fanout.

## Licence

[CC0 1.0](LICENSE): no rights reserved.
