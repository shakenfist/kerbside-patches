#!/usr/bin/env python3
"""Report the anonymous RSS that importing TARGET modules adds on top of BASE modules.

Usage: marginal.py BASE[,BASE...] TARGET[,TARGET...]

BASE may be an empty string. Choose a BASE that the processes in question load
anyway (oslo.config, oslo.log, requests, cryptography and so on), so the result
is what a process would save by not importing TARGET, not the cost of TARGET's
dependencies that the process needs regardless.

Run it with python -I in a fresh interpreter per measurement.
"""
import importlib
import sys


def anon_mb():
    with open('/proc/self/status') as f:
        for line in f:
            if line.startswith('RssAnon'):
                return int(line.split()[1]) / 1024
    return 0.0


def main():
    for m in [m for m in sys.argv[1].split(',') if m]:
        importlib.import_module(m)
    before, nbefore = anon_mb(), len(sys.modules)
    for m in sys.argv[2].split(','):
        importlib.import_module(m)
    print('%-55s +%5.1f MB  +%4d modules' % (sys.argv[2][:55], anon_mb() - before, len(sys.modules) - nbefore))


if __name__ == '__main__':
    main()
