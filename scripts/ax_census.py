"""Read-only Accessibility-coverage census.

Decides the make-or-break question for the agent's "local-by-default" moat: of
the apps you ACTUALLY use, how many expose a usable Accessibility tree we can
drive locally (Tier 1) vs. fall through to vision/cloud?

For each running, user-facing app it reads the AX tree (no actions, nothing is
clicked or changed) and scores: element count, % labeled, # actionable
(AXPress), and a verdict. It first checks whether THIS process is AX-trusted,
because an untrusted process reads empty trees everywhere — which would look
like "no coverage" when it's really just a missing permission.

Usage:  .venv/bin/python scripts/ax_census.py
Open the apps you use daily first (Slack, Cursor/VS Code, Chrome, Notion, Mail,
Calendar, Terminal, ...). Run from a process that has Accessibility access.
"""
from __future__ import annotations

import AppKit
import ApplicationServices as AX

_CHILDREN = "AXChildren"
_WINDOWS = "AXWindows"
_ROLE = "AXRole"
_TITLE = "AXTitle"
_DESC = "AXDescription"
_VALUE = "AXValue"

_MAX_DEPTH = 8
_MAX_ELEMENTS = 4000  # bound the walk so a huge tree can't hang the census


def _attr(el, name):
    err, val = AX.AXUIElementCopyAttributeValue(el, name, None)
    return val if err == 0 else None


def _labeled(el) -> bool:
    for a in (_TITLE, _DESC, _VALUE):
        v = _attr(el, a)
        if isinstance(v, str) and v.strip():
            return True
    return False


def _actionable(el) -> bool:
    err, names = AX.AXUIElementCopyActionNames(el, None)
    return err == 0 and names is not None and "AXPress" in names


def _walk(el, stats, depth=0):
    if stats["total"] >= _MAX_ELEMENTS or depth > _MAX_DEPTH:
        return
    stats["total"] += 1
    if _labeled(el):
        stats["labeled"] += 1
    if _actionable(el):
        stats["actionable"] += 1
    children = _attr(el, _CHILDREN) or []
    for child in children:
        if stats["total"] >= _MAX_ELEMENTS:
            break
        _walk(child, stats, depth + 1)


def census_app(name: str, pid: int) -> dict:
    app = AX.AXUIElementCreateApplication(pid)
    try:
        AX.AXUIElementSetMessagingTimeout(app, 2.0)  # never hang on a wedged app
    except Exception:
        pass
    err, wins = AX.AXUIElementCopyAttributeValue(app, _WINDOWS, None)
    if err != 0:
        return {"name": name, "verdict": f"blocked (AX err {err})",
                "total": 0, "labeled": 0, "actionable": 0}
    stats = {"total": 0, "labeled": 0, "actionable": 0}
    for w in (wins or []):
        _walk(w, stats)
    total = stats["total"]
    lab_frac = stats["labeled"] / total if total else 0.0
    if total < 5:
        verdict = "EMPTY (vision/cloud only)"
    elif lab_frac >= 0.5 and stats["actionable"] >= 3:
        verdict = "FULL (Tier-1 drivable)"
    else:
        verdict = "PARTIAL"
    return {"name": name, "verdict": verdict, "total": total,
            "labeled": stats["labeled"], "actionable": stats["actionable"],
            "lab_frac": lab_frac}


def main() -> int:
    if not AX.AXIsProcessTrusted():
        print("!! This process is NOT Accessibility-trusted — every tree will "
              "read empty.\n   Grant Accessibility to your terminal (System "
              "Settings > Privacy & Security > Accessibility) and re-run, or run "
              "from WhisperQuiet.app's context. Census below would be FALSE.\n")
        return 2

    ws = AppKit.NSWorkspace.sharedWorkspace()
    apps = [a for a in ws.runningApplications()
            if a.activationPolicy() == 0 and a.localizedName()]  # regular, user-facing
    rows = []
    for a in sorted(apps, key=lambda a: a.localizedName().lower()):
        try:
            rows.append(census_app(a.localizedName(), a.processIdentifier()))
        except Exception as exc:
            rows.append({"name": a.localizedName(), "verdict": f"error {type(exc).__name__}",
                         "total": 0, "labeled": 0, "actionable": 0, "lab_frac": 0})

    head = f"{'app':<24}{'elements':>9}{'labeled':>9}{'actions':>9}  verdict"
    print(head)
    print("-" * len(head))
    counts = {"FULL": 0, "PARTIAL": 0, "EMPTY": 0, "blocked": 0}
    for r in rows:
        print(f"{r['name'][:23]:<24}{r['total']:>9}{r.get('labeled',0):>9}"
              f"{r.get('actionable',0):>9}  {r['verdict']}")
        for k in counts:
            if r["verdict"].startswith(k) or (k == "EMPTY" and r["verdict"].startswith("EMPTY")):
                counts[k] += 1
                break
    n = len(rows) or 1
    full = counts["FULL"]
    print(f"\n{full}/{n} apps FULL Tier-1 drivable ({100*full/n:.0f}%).  "
          f"FULL={counts['FULL']} PARTIAL={counts['PARTIAL']} "
          f"EMPTY={counts['EMPTY']} blocked={counts['blocked']}")
    print("Decision: if FULL% on YOUR daily apps is high, local-by-default is "
          "real. If low, the agent is mostly a vision/cloud product and the "
          "'local moat' is a niche (Mail/Finder/Calendar).")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
