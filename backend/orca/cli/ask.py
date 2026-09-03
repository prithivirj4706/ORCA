"""ORCA graph-driven CLI.

The same pipeline as `cli.query`, except that NOTHING here decides what to
retrieve. A Planner reads the question and chooses the capabilities; the graph
executes, validates, re-plans if evidence is missing, assesses each domain
independently and composes the answer.

Run it with different questions to see the plan change:

    python -m backend.orca.cli.ask "is it safe to go out near Kochi tomorrow?"
    python -m backend.orca.cli.ask "am I inside the Indian EEZ?"
    python -m backend.orca.cli.ask "is there a warning in force right now?"
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..adapters.cmems.adapter import CmemsAdapter
from ..adapters.incois_erddap.adapter import IncoisErddapAdapter
from ..adapters.marineregions.adapter import MarineRegionsAdapter
from ..adapters.incois_wms.adapter import IncoisPfzAdapter
from ..adapters.noaa_gfs.adapter import NoaaGfsAdapter
from ..graph.build import build_graph
from ..graph.runtime import OrcaRuntime
from ..llm.provider import resolve_provider
from ..tools.live import build_live_registry

IST = ZoneInfo("Asia/Kolkata")
BAR = "=" * 78


def _print_trace(final: dict) -> None:
    print("\nGRAPH TRACE   (node · outcome · duration)")
    for e in final.get("node_events") or []:
        node = e.get("node", "?")
        extra = ""
        if node == "tool_exec":
            extra = f"  {e.get('tool', '')}"
            if e.get("fallback_used"):
                extra += " (fallback)"
            if e.get("codes"):
                extra += f"  [{','.join(e['codes'])}]"
        print(f"  {node:18} {e.get('status', ''):10} {e.get('duration_ms', 0):>6} ms"
              f"{extra}")
        if e.get("summary") and node not in ("tool_exec",):
            print(f"    {e['summary']}")


def run(query: str, *, lat: float | None, lon: float | None,
        when: datetime | None, show_trace: bool = True) -> int:
    print(BAR)
    print("ORCA — agent-planned run")
    print(BAR)
    print(f"question    {query!r}")

    llm = resolve_provider()
    print(f"planner     {'LLM ' + llm.model if llm.available else 'deterministic'}"
          + ("" if llm.available
             else f"  ({getattr(llm, 'reason', 'no model configured')})"))

    state: dict = {"query_text": query}
    if lat is not None and lon is not None:
        state["resolved_location"] = {"lat": lat, "lon": lon, "label": None}
    if when is not None:
        state["resolved_time_window"] = {
            "start_time": when.isoformat(),
            "end_time": (when + timedelta(hours=4)).isoformat()}

    with IncoisErddapAdapter() as erddap, CmemsAdapter() as cmems, \
            MarineRegionsAdapter() as boundaries, NoaaGfsAdapter() as gfs, \
            IncoisPfzAdapter() as pfz:
        registry = build_live_registry(erddap=erddap, cmems=cmems,
                                       boundaries=boundaries, gfs=gfs, pfz=pfz)
        rt = OrcaRuntime(registry=registry, llm=llm)
        graph = build_graph()
        final = graph.invoke(state, config={"configurable": rt.configurable()})

    loc = final.get("resolved_location")
    win = final.get("resolved_time_window")
    print(f"intent      {final.get('intent')}")
    if loc:
        print(f"location    {loc.get('label') or ''} "
              f"({loc['lat']:.3f} N, {loc['lon']:.3f} E)".replace("  ", " "))
    if win:
        start = datetime.fromisoformat(win["start_time"]).astimezone(IST)
        end = datetime.fromisoformat(win["end_time"]).astimezone(IST)
        print(f"window      {start:%d %b %Y %H:%M}–{end:%H:%M} IST")
    for note in final.get("resolution_notes") or []:
        print(f"            · {note}")

    plan = final.get("plan")
    if plan is not None:
        print("\nPLAN")
        print(f"  v{plan.plan_version}  domains "
              f"{', '.join(d.value for d in plan.domains_required) or 'none'}")
        print(f"  required evidence: {', '.join(plan.required_evidence) or 'none'}")
        for step in plan.steps:
            print(f"    {step.step_id}  {step.tool:26} {step.necessity}")
        for gap in plan.unavailable_capabilities:
            # Some gaps are a missing TOOL; others are an evidence key no tool
            # yields at all. Name whichever one is meaningful.
            label = (gap["tool"] if gap.get("tool") and gap["tool"] != "-"
                     else gap.get("evidence", "?"))
            print(f"    --  {label:26} UNAVAILABLE — {gap['reason']}")
        print(f"  {plan.reasoning_summary}")

    if show_trace:
        _print_trace(final)

    report = final.get("validation_report")
    if report is not None:
        print(f"\nVALIDATION  {report.valid_objects} value(s); required gaps: "
              f"{', '.join(report.required_gaps) or 'none'}")

    print("\nASSESSMENTS   (independent by design; never merged into one score)")
    for a in final.get("assessments") or []:
        print(f"\n  {a.domain.value:22} {a.verdict.value:22} "
              f"confidence={a.confidence.value}")
        for d in a.drivers:
            mark = ">>" if d.contribution == "limiting" else "  "
            if isinstance(d.value, bool):
                # A boolean means containment for a boundary and presence for
                # an advisory or a warning; they do not read the same way.
                pair = (("inside", "outside") if "boundary" in d.factor
                        or d.factor.isupper() else ("present", "absent"))
                val = pair[0] if d.value else pair[1]
            elif d.value is not None:
                val = f"{d.value:g} {d.unit or ''}".strip()
            else:
                val = "-"
            print(f"      {mark} {d.factor:30} {val:14} {d.band or ''}")
        for n in a.not_evaluated:
            print(f"         not evaluated: {n.factor:26} {n.reason}")
        if a.rationale:
            print(f"      {a.rationale}")

    rec = final.get("recommendation")
    if rec is not None:
        category = getattr(rec, "category", None) or rec.get("category", "")
        headline = getattr(rec, "headline", None) or rec.get("headline", "")
        print(f"\nANSWER   [{category}]")
        print(f"  {headline}")
        narrative = getattr(rec, "narrative", "")
        if narrative:
            for line in narrative.splitlines()[1:]:
                print(f"  {line}")
        claims = getattr(rec, "claims", [])
        if claims:
            print(f"\n  {len(claims)} claim(s), each bound to evidence:")
            for c in claims:
                print(f"    · {c.text}  [{', '.join(c.evidence_ids[:3])}"
                      f"{'...' if len(c.evidence_ids) > 3 else ''}]")
        print(f"\n  disposition: {final.get('disposition')}   "
              f"confidence: {getattr(rec, 'confidence', '')}")

    print("\nORCA output is not an official advisory. "
          "Follow IMD and INCOIS bulletins.")
    print(BAR)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="orca-ask",
                                description="Ask ORCA a question; a Planner "
                                            "decides what to retrieve.")
    p.add_argument("query", nargs="?",
                   default="is it good for fishing near Kochi tomorrow morning?")
    p.add_argument("--lat", type=float, default=None)
    p.add_argument("--lon", type=float, default=None)
    p.add_argument("--when", default=None, help="ISO-8601 UTC")
    p.add_argument("--no-trace", action="store_true")
    a = p.parse_args(argv)
    when = (datetime.fromisoformat(a.when).replace(tzinfo=timezone.utc)
            if a.when else None)
    return run(a.query, lat=a.lat, lon=a.lon, when=when,
               show_trace=not a.no_trace)


if __name__ == "__main__":
    sys.exit(main())
