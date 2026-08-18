#!/usr/bin/env python3
"""Report tool time spent on stages that did not produce a result.

Deliberately separate from report_time_saved.py. The savings report answers
"how much work did we skip", and failed work is not skipped work -- folding
the two together would let a stage that burned four hours and produced nothing
flatter the numbers. Nothing here writes to the PD cache or its ledger.

The cache only records stages that succeeded, so this reads the build tree
instead: a rundir whose tool log has no matching stage output json ran without
producing a result. That covers failures the tool exited on, stages killed
from outside (a supervisor timeout, a second run colliding in the same
directory), and runs superseded before they finished.

Usage, under the SledgeHammer venv:

    python vlsi/hammer/scripts/report_failed_time.py <obj_dir> [<obj_dir> ...]
    python vlsi/hammer/scripts/report_failed_time.py build/MyDesign --group-by module
    python vlsi/hammer/scripts/report_failed_time.py build/* --min-minutes 10

An in-flight stage looks identical to a failed one -- it has no output yet --
so anything still being written is listed separately and left out of the total
unless you pass --include-running.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

_MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}

# Cadence tools stamp "Date: Fri Aug 14 00:53:02 2026" in their log preamble.
_DATE_RE = re.compile(
    r"Date:\s+\w+\s+(\w{3})\s+(\d+)\s+(\d+):(\d+):(\d+)\s+(\d{4})")

# A log still being appended to within this window is treated as in flight.
_RUNNING_GRACE_S = 15 * 60


def _log_start(path: str) -> Optional[float]:
    """Epoch seconds from the tool's own log preamble, or None."""
    try:
        with open(path, errors="replace") as f:
            for _ in range(60):
                line = f.readline()
                if not line:
                    break
                m = _DATE_RE.search(line)
                if m:
                    mon, day, hh, mm, ss, yr = m.groups()
                    return time.mktime((int(yr), _MONTHS[mon], int(day),
                                        int(hh), int(mm), int(ss), 0, 0, -1))
    except OSError:
        return None
    return None


def _tool_logs(rundir: str) -> List[str]:
    out = []
    for pat in ("innovus.log*", "genus.log*", "calibre.log*", "vcs.log*"):
        for p in glob.glob(os.path.join(rundir, pat)):
            # *.logv / *.logv.gz are the tool's verbose mirrors of the same run
            if "logv" in os.path.basename(p) or p.endswith(".gz"):
                continue
            if os.path.isfile(p):
                out.append(p)
    return out


def _completed_at(rundir: str, stage: str) -> Optional[float]:
    """When this stage last produced its output json, if ever."""
    outs = glob.glob(os.path.join(rundir, f"{stage}-output-full.json"))
    outs += glob.glob(os.path.join(rundir, f"{stage}-output.json"))
    times = [os.path.getmtime(o) for o in outs if os.path.isfile(o)]
    return max(times) if times else None


def scan(obj_dirs: List[str]) -> Tuple[List[Dict], List[Dict]]:
    """Return (failed, running) records for every rundir under ``obj_dirs``."""
    failed: List[Dict] = []
    running: List[Dict] = []
    now = time.time()
    for obj_dir in obj_dirs:
        for rundir in sorted(glob.glob(os.path.join(obj_dir, "*"))):
            if not os.path.isdir(rundir):
                continue
            name = os.path.basename(rundir)
            stage = name.split("-", 1)[0]
            if stage not in ("syn", "par", "drc", "lvs", "sim", "power", "timing"):
                continue
            module = name.split("-", 1)[1] if "-" in name else ""
            done_at = _completed_at(rundir, stage)
            for log in _tool_logs(rundir):
                start = _log_start(log)
                end = os.path.getmtime(log)
                if start is None or end <= start:
                    continue
                # Output newer than this log means this attempt is the one that
                # produced it. Anything else ran and left nothing behind.
                if done_at is not None and done_at >= end - 60:
                    continue
                rec = {
                    "obj_dir": obj_dir, "rundir": rundir, "stage": stage,
                    "module": module, "log": os.path.basename(log),
                    "seconds": end - start, "ended": end,
                }
                (running if now - end < _RUNNING_GRACE_S else failed).append(rec)
    return failed, running


def _fmt(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


def format_report(failed: List[Dict], running: List[Dict],
                  group_by: str = "stage", min_seconds: float = 0.0) -> str:
    failed = [r for r in failed if r["seconds"] >= min_seconds]
    lines = ["Tool time on stages that produced no result",
             "  (separate from the savings report by design; not netted against it)",
             ""]
    if not failed and not running:
        lines.append("  nothing found -- every rundir scanned has a matching stage output")
        return "\n".join(lines)

    groups: Dict[str, Dict[str, float]] = {}
    for r in failed:
        key = r.get(group_by) or "-"
        g = groups.setdefault(key, {"n": 0, "wall": 0.0})
        g["n"] += 1
        g["wall"] += r["seconds"]

    if groups:
        width = max(len(k) for k in groups)
        lines.append(f"  {group_by:<{max(width, 10)}}   attempts   wall")
        lines.append("  " + "-" * (max(width, 10) + 22))
        for key in sorted(groups, key=lambda k: -groups[k]["wall"]):
            g = groups[key]
            lines.append(f"  {key:<{max(width, 10)}}   {int(g['n']):>8}   {_fmt(g['wall'])}")
        lines.append("  " + "-" * (max(width, 10) + 22))

    total = sum(r["seconds"] for r in failed)
    lines.append(f"  TOTAL LOST: {_fmt(total)} across {len(failed)} attempt(s)")

    worst = sorted(failed, key=lambda r: -r["seconds"])[:10]
    if worst:
        lines.append("")
        lines.append("  longest attempts:")
        for r in worst:
            label = f"{r['stage']}-{r['module']}" if r["module"] else r["stage"]
            lines.append(f"    {label:<38} {r['log']:<14} {_fmt(r['seconds']):>8}")

    if running:
        lines.append("")
        lines.append(f"  still in flight, excluded from the total ({len(running)}):")
        for r in sorted(running, key=lambda r: -r["seconds"])[:10]:
            label = f"{r['stage']}-{r['module']}" if r["module"] else r["stage"]
            lines.append(f"    {label:<38} {r['log']:<14} {_fmt(r['seconds']):>8}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Report tool time spent on stages that produced no result.",
        epilog="Kept apart from report_time_saved.py on purpose: failed work is "
               "not saved work and is never netted against the savings totals.")
    p.add_argument("obj_dir", nargs="+",
                   help="Build directory holding syn-*/par-* rundirs (repeatable).")
    p.add_argument("-g", "--group-by", default="stage",
                   choices=["stage", "module", "obj_dir"],
                   help="How to group the totals (default: stage).")
    p.add_argument("--min-minutes", type=float, default=0.0,
                   help="Ignore attempts shorter than this many minutes.")
    p.add_argument("--include-running", action="store_true",
                   help="Count stages still being written as lost time too.")
    args = p.parse_args(argv)

    missing = [d for d in args.obj_dir if not os.path.isdir(d)]
    if missing:
        raise SystemExit(f"not a directory: {', '.join(missing)}")

    failed, running = scan(args.obj_dir)
    if args.include_running:
        failed, running = failed + running, []
    print(format_report(failed, running, group_by=args.group_by,
                        min_seconds=args.min_minutes * 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
