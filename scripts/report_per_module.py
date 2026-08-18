#!/usr/bin/env python3
"""Per-module savings, normalized as turnaround and compute efficiency.

The savings report totals absolute hours, which makes a big block look like a
big win simply because it is big. This normalizes instead: for each module it
reports what fraction of that module's own work the flow avoided, so a small
block with a high reuse rate and a large one with a low rate are comparable.

For each group it reports what the work would have cost without the flow, what
the flow avoided, and the ratio between them:

    without = ran + saved
    efficiency = saved / without

on wall clock (what an engineer waits through) and on CPU (what the machine
spends).

``saved`` is cache reuse + dependency-check skips + substep resume. It excludes
the parallel-execution bucket on purpose: that is measured per DAG run from task
overlap windows, so it cannot be attributed to one module, and its baseline is a
strictly serial flow rather than a parallel make. What remains is the part that
is per-stage and content-addressed.

Legacy-equivalent skips appear in neither term. A plain make would have skipped
that work too, so it is absent from both worlds and belongs in neither the
baseline nor the credit.

Usage, under the SledgeHammer venv:

    python vlsi/hammer/scripts/report_per_module.py
    python vlsi/hammer/scripts/report_per_module.py --design IrisVLSITop
    python vlsi/hammer/scripts/report_per_module.py --group-by stage --csv
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Optional

from hammer.vlsi import time_tracking


def _hm(seconds: float) -> str:
    s = int(seconds or 0)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


def baseline(ran: float, saved: float) -> float:
    """What this group would have cost without the flow.

    Everything that ran, plus everything the flow skipped on its behalf. The
    legacy-equivalent skips are deliberately absent: a plain make would have
    skipped that work too, so it is in neither world and subtracting it here
    would understate the baseline.
    """
    return (ran or 0.0) + (saved or 0.0)


def efficiency(ran: float, saved: float) -> Optional[float]:
    """Fraction of this group's own work the flow avoided."""
    base = baseline(ran, saved)
    if base <= 0:
        return None
    return (saved or 0.0) / base


def normalize(bucket: Dict[str, Any]) -> Dict[str, Any]:
    """Per-group saved/ran totals plus the two efficiency figures."""
    saved_wall = bucket["cache_wall"] + bucket["depsh_wall"] + bucket["resume_wall"]
    saved_cpu = bucket["cache_cpu"] + bucket["depsh_cpu"] + bucket["resume_cpu"]
    return {
        "events": bucket["events"],
        "hits": bucket["hits"],
        "misses": bucket["misses"],
        "ran_wall": bucket["ran_wall"], "ran_cpu": bucket["ran_cpu"],
        "saved_wall": saved_wall, "saved_cpu": saved_cpu,
        "legacy_wall": bucket["deplegacy_wall"], "legacy_cpu": bucket["deplegacy_cpu"],
        "base_wall": baseline(bucket["ran_wall"], saved_wall),
        "base_cpu": baseline(bucket["ran_cpu"], saved_cpu),
        "tat": efficiency(bucket["ran_wall"], saved_wall),
        "ctat": efficiency(bucket["ran_cpu"], saved_cpu),
    }


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def format_table(agg: Dict[str, Any], source: str) -> str:
    rows = {k: normalize(v) for k, v in agg["groups"].items()}
    rows = {k: v for k, v in rows.items() if v["events"]}
    total = normalize(agg["totals"])
    label = agg["group_by"]
    width = max([len(k) for k in rows] + [len(label), 8])

    out = [f"Per-{label} savings, normalized  (source: {source})",
           f"  {agg['n_runs']} run(s), {agg['n_dags']} dag(s)",
           ""]
    head = (f"  {label:<{width}}  {'hit/miss':>9}  {'w/o sledge':>10}  {'saved':>9}  {'TAT':>7}"
            f"  {'cpu w/o':>10}  {'cpu saved':>10}  {'cTAT':>7}")
    out += [head, "  " + "-" * (len(head) - 2)]
    for key in sorted(rows, key=lambda k: -(rows[k]["tat"] or -1)):
        r = rows[key]
        out.append(f"  {key:<{width}}  {r['hits']:>4}/{r['misses']:<4}  "
                   f"{_hm(r['base_wall']):>10}  {_hm(r['saved_wall']):>9}  {_pct(r['tat']):>7}  "
                   f"{_hm(r['base_cpu']):>10}  {_hm(r['saved_cpu']):>10}  {_pct(r['ctat']):>7}")
    out += ["  " + "-" * (len(head) - 2)]
    out.append(f"  {'ALL':<{width}}  {total['hits']:>4}/{total['misses']:<4}  "
               f"{_hm(total['base_wall']):>10}  {_hm(total['saved_wall']):>9}  {_pct(total['tat']):>7}  "
               f"{_hm(total['base_cpu']):>10}  {_hm(total['saved_cpu']):>10}  {_pct(total['ctat']):>7}")
    out += ["",
            "  w/o sledge = what the module would have cost without the flow",
            "  TAT/cTAT = saved / (w/o sledge), on wall clock and on CPU",
            "  saved = cache + dep-check + substep resume; parallel-execution",
            "  savings are per-run and are not attributed to a module."]
    return "\n".join(out)


def format_csv(agg: Dict[str, Any]) -> str:
    label = agg["group_by"]
    lines = [f"{label},events,hits,misses,base_wall_s,ran_wall_s,saved_wall_s,"
             "tat,base_cpu_s,ran_cpu_s,saved_cpu_s,compute_tat"]
    for key, bucket in sorted(agg["groups"].items()):
        r = normalize(bucket)
        if not r["events"]:
            continue
        tat = "" if r["tat"] is None else f"{r['tat']:.4f}"
        ctat = "" if r["ctat"] is None else f"{r['ctat']:.4f}"
        lines.append(
            f"{key},{r['events']},{r['hits']},{r['misses']},"
            f"{r['base_wall']:.1f},{r['ran_wall']:.1f},{r['saved_wall']:.1f},{tat},"
            f"{r['base_cpu']:.1f},{r['ran_cpu']:.1f},{r['saved_cpu']:.1f},{ctat}")
    return "\n".join(lines)


def format_timeseries(events: List[Dict[str, Any]], group_by: str,
                      bucket: str) -> str:
    """One CSV row per (group, time bucket), for plotting the campaign."""
    import time as _time
    fmt = "%Y-%m-%dT%H:00" if bucket == "hour" else "%Y-%m-%d"
    cells: Dict[Any, Dict[str, Any]] = {}
    for ev in events:
        ts = ev.get("ts")
        if not ts:
            continue
        key = (time_tracking._group_key(ev, group_by),
               _time.strftime(fmt, _time.localtime(float(ts))))
        cells.setdefault(key, time_tracking._empty_bucket())
        time_tracking._accumulate(cells[key], ev)

    lines = [f"{group_by},{bucket},events,hits,misses,ran_wall_s,saved_wall_s,"
             "ran_cpu_s,saved_cpu_s"]
    for (grp, when) in sorted(cells):
        r = normalize(cells[(grp, when)])
        lines.append(f"{grp},{when},{r['events']},{r['hits']},{r['misses']},"
                     f"{r['ran_wall']:.1f},{r['saved_wall']:.1f},"
                     f"{r['ran_cpu']:.1f},{r['saved_cpu']:.1f}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Per-module savings normalized as turnaround/compute efficiency.")
    p.add_argument("-g", "--group-by", default="module",
                   help="module (default), stage, design, project, dag, or run.")
    p.add_argument("--by", choices=["hour", "day"],
                   help="Emit a CSV time series bucketed this way instead of a summary. "
                        "Buckets are stamped at event time, so a long stage lands in "
                        "the bucket where it finished, not the ones it spanned.")
    p.add_argument("--source", choices=["auto", "db", "jsonl", "both"], default="auto")
    p.add_argument("--since", help="Only count events at/after this time (YYYY-MM-DD).")
    p.add_argument("--until", help="Only count events at/before this time (YYYY-MM-DD).")
    p.add_argument("--dag", help="Filter to dag_id containing this substring.")
    p.add_argument("--design", help="Filter to design containing this substring.")
    p.add_argument("--stage", help="Filter to stage containing this substring.")
    p.add_argument("--project", help="Filter to project containing this substring.")
    p.add_argument("--user", help="Filter to triggering user containing this substring.")
    p.add_argument("--csv", action="store_true", help="Emit CSV instead of a table.")
    args = p.parse_args(argv)

    try:
        events, source = time_tracking.collect_savings_events(
            source=args.source, since=args.since, until=args.until,
            dag=args.dag, design=args.design, stage=args.stage,
            project=args.project, user=args.user)
    except Exception as e:
        raise SystemExit(f"Could not read cache events: {e}")
    if not events:
        raise SystemExit("No cache events matched those filters.")

    if args.by:
        print(format_timeseries(events, args.group_by, args.by))
        return 0

    agg = time_tracking.aggregate_savings(events, group_by=args.group_by)
    print(format_csv(agg) if args.csv else format_table(agg, source))
    return 0


if __name__ == "__main__":
    sys.exit(main())
