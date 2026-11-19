#!/usr/bin/env python3
"""Fail if any generated function ends on a transfer whose delay slot was lost.

The recompiler splits a function whenever it believes a new one starts. When the
"new function" is really the delay slot of the last jump/call, the emitter used
to write the transfer without its slot and the slot only ran afterwards, on the
fall-through path. That is not what the hardware does, and DJMAX hit it twice
(0x1BFFC/0x1C000 and 0x2C224/0x2C228).

Usage: check_delay_slots.py <gen-dir>
"""

import re
import glob
import sys

ins = re.compile(r'/\* ([0-9A-F]{8})  (\S+)\s*(.*?) \*/')
bad = []
for f in sorted(glob.glob(sys.argv[1] + '/djmax_funcs_*.c')):
    last = None
    for line in open(f, errors='replace'):
        m = ins.search(line)
        if m:
            last = m.groups()
            continue
        if 'falls into' in line and last and \
           (last[1] in ('jal', 'jalr', 'j', 'jr') or last[1].startswith('b')):
            bad.append(last)
for b in bad:
    print(*b)
sys.exit(1 if bad else 0)
