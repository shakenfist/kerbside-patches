# v2 series: results

The full measurement writeup, including method, is the source of
truth and lives in the cover letter:
`../series/v2/v2-0000-cover-letter.patch`. This file reproduces its
tables (unchanged) so they are findable from `results/`, and adds two
tables the cover letter does not carry: the per-device qemu CPU
coverage matrix and the v2 patch-3 pacing-variant matrix.

## virtio-vga, streaming-video=filter

  run            draws  streams     MB    p50/p95 latency (ms)
  base, full      6643  18 32x360  26.9   15.2 / 28.6
  series, full      45   1 480x360  5.0    0.2 /  1.8
  base, clips     4937  19 32x360  19.4   14.3 / 28.0
  series, clips     45   1 480x360  5.0    0.1 /  1.4

With streaming off, bytes drop from 79 to 65 MB (full) and 73 to 65 MB
(clips). With the clock beside the video (same rows), patch 4 turns
6.5 MB into 5.0 MB and p95 latency 12ms into 1.2ms.

## Other devices, streaming-video=filter, base -> series

  VGA (std-vga):     24709 draws, no stream, 81 MB
                  ->   830 draws, 1 480x360 stream, 35 MB
  qxl-vga, VGA mode: 24662 draws, no stream, 81 MB
                  ->   813 draws, 1 480x360 stream, 34 MB
  virtio-vga,max_outputs=2, video on both heads:
                      9872 draws, 43 32x360 streams, 36.7 MB
                  ->    90 draws, 2 480x360 streams, 10.4 MB

No regressions seen: bytes with streaming off are equal or lower
everywhere, qemu CPU is equal or lower. Patch 3 does not apply to qxl
(which has its own listener) or gl=on. Windows guests were not tested.

## Diff cost, 1920x1080x32, per update (us)

column diff -> this series: everything changed 315 -> 9, nothing
changed 237 -> 195, 480x360 video 241 -> 210, one pixel column 233 ->
185, one pixel per row at random x 259 -> 385, two pixel columns 640
apart 242 -> 708 (patch 4 column scan). The last two send one or two
large boxes rather than many 32 pixel columns. Measured with
`../rig/bench/bench.c`.

## Device coverage matrix (qemu CPU)

Generated from `../rig/tools/coverage.sh`'s raw `run.json` files
(`coverage.log` in the session scratchpad); `ticks` is `qemu_cpu_ticks`
before/after the run and `cpu%` is `(after - before) / 28s`, matching
`summary.py`'s calculation. std-vga and qxl-vga (VGA mode) use the
vesafb guest path in `../rig/guest/init2.c`; two-head uses the same
guest's second KMS head.

| run | ticks | cpu% |
|---|---|---|
| cov-vga-stock-filter | 100 1454 | 48 |
| cov-vga-stock-off | 100 1450 | 48 |
| cov-vga-v2-filter | 109 1401 | 46 |
| cov-vga-v2-off | 99 1355 | 45 |
| cov-qxlvga-stock-filter | 99 1445 | 48 |
| cov-qxlvga-stock-off | 103 1428 | 47 |
| cov-qxlvga-v2-filter | 100 1307 | 43 |
| cov-qxlvga-v2-off | 97 1343 | 44 |
| cov-twohead-stock-filter | 93 1344 | 45 |
| cov-twohead-stock-off | 100 827 | 26 |
| cov-twohead-v2-filter | 99 858 | 27 |
| cov-twohead-v2-off | 91 706 | 22 |

## Patch-3 pacing variants (qemu CPU)

Generated from `../rig/tools/matrix2.sh`'s raw `run.json` files
(`matrix2.log` in the session scratchpad). See `../rig/tools/matrix2.sh`'s
header comment for what the v2list/v2diff/v2pace/v2pace60 build
variants are; "full"/"rect" and "filter"/"off" match `v1-summary.md`.

| run | ticks | cpu% |
|---|---|---|
| v2list-full-filter | 107 726 | 22 |
| v2diff-full-filter | 113 492 | 14 |
| v2pace-full-filter | 107 483 | 13 |
| v2pace60-full-filter | 106 475 | 13 |
| v2list-full-off | 105 533 | 15 |
| v2diff-full-off | 116 445 | 12 |
| v2pace-full-off | 119 437 | 11 |
| v2pace60-full-off | 125 442 | 11 |
| v2list-rect-filter | 130 656 | 19 |
| v2diff-rect-filter | 116 425 | 11 |
| v2pace-rect-filter | 105 412 | 11 |
| v2pace60-rect-filter | 110 457 | 12 |
| v2list-rect-off | 102 496 | 14 |
| v2diff-rect-off | 124 435 | 11 |
| v2pace-rect-off | 120 426 | 11 |
| v2pace60-rect-off | 112 417 | 11 |
