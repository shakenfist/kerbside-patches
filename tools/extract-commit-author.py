#!/usr/bin/python3
"""Print the Author header of a patch file, or nothing if it has none."""

import sys

from patch_message import author

value = author(sys.argv[1])
if value:
    print(value)
