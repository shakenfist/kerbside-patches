# qemu rig

See [../README.md](../README.md) for how to build and run this. Layout:

- `guest/` -- the static PID-1 KMS guest sources (`init.c` single-head,
  `init2.c` + vesafb + two-head) and `build.sh`, which builds them plus
  the kernel image and initrds into this directory (gitignored, not
  imported).
- `tools/` -- `run.sh`/`run2.sh` (one measurement), `matrix.sh`/
  `matrix2.sh`/`coverage.sh` (sweeps), `ryllana.py`/`traceana.py`/
  `spicepcap.py` (log/trace/pcap analysis, each usable standalone) and
  `summary.py` (turns a sweep into a markdown table).
- `bench/` -- `bench.c`, a standalone microbenchmark of the damage diff
  against the upstream column diff, and `build.sh`.
- `sysroot/` -- `build.sh`, which extracts the SPICE -dev packages
  needed to build qemu without installing them system-wide.
- `qemu/`, `ryll/`, `results/`, `build-*/` -- not part of this import;
  created locally by the steps in `../README.md` and gitignored.
