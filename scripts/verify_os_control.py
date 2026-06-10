"""Verification: move the cursor smoothly in a circle, then restore it.

Deliberately performs NO clicks and NO typing — synthetic clicks land on
whatever is under the cursor. Keyboard injection is verified separately by
live dictation use (see WHISPERFLOW_AGENT_LOG.md).
"""

from __future__ import annotations

import math
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from whisperquiet.control import mouse

RADIUS, STEPS, PERIOD_S = 120, 120, 2.0


def main() -> int:
    ox, oy = mouse.position()
    cx, cy = ox - RADIUS, oy  # circle starts at current position
    try:
        for i in range(STEPS + 1):
            angle = 2 * math.pi * i / STEPS
            mouse.move(cx + RADIUS * math.cos(angle), cy + RADIUS * math.sin(angle))
            time.sleep(PERIOD_S / STEPS)
    finally:
        mouse.move(ox, oy)
    fx, fy = mouse.position()
    ok = abs(fx - ox) < 2 and abs(fy - oy) < 2
    print(f"start=({ox:.0f},{oy:.0f}) end=({fx:.0f},{fy:.0f})")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
