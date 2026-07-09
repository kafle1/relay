#!/usr/bin/env python
"""Shared helper: build a conflict-free Junction from whichever approaches a camera can see."""
from controller import Junction


def junction_from_dirs(dirs):
    dirs = set(dirs)
    phases = {}
    ns = [d for d in ("N", "S") if d in dirs]
    ew = [d for d in ("E", "W") if d in dirs]
    if ns:
        phases["NS" if len(ns) == 2 else ns[0]] = ns
    if ew:
        phases["EW" if len(ew) == 2 else ew[0]] = ew
    return Junction(sorted(dirs), phases)
