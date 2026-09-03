"""geo_reason (07 section 4). Alignment and derivation; continues degraded."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from ...agents.geospatial_agent import GeospatialAgent
from ...schemas.assessment import NotEvaluated
from ...schemas.core import utcnow
from ...schemas.enums import Domain
from ..events import node_event
from ..runtime import runtime_from
from ..state import OrcaGraphState

log = logging.getLogger("orca.graph.analysis")


def _window(state: OrcaGraphState, hours: int = 4) -> tuple[datetime, datetime]:
    """The analysis window, defaulting to now.

    A time-independent question -- "am I inside the EEZ?" -- legitimately
    resolves no window, and the Planner does not ask for one. The analysis frame
    still needs an interval to align against, so it defaults to the present.
    Time-SENSITIVE intents never reach here without a window: the Planner asks
    for one first.
    """
    w = state.get("resolved_time_window") or {}
    if w.get("start_time"):
        start = datetime.fromisoformat(w["start_time"])
        end = (datetime.fromisoformat(w["end_time"]) if w.get("end_time")
               else start + timedelta(hours=hours))
        return start, end
    start = utcnow()
    return start, start + timedelta(hours=hours)


def _corridor_radius_km(lat1: float, lon1: float,
                        lat2: float, lon2: float) -> float:
    """A radius about the corridor midpoint that covers the whole route.

    Half the endpoint separation would cover only the straight line; the route
    is free to bow away from it, and a penalty field that stops at the straight
    line would steer the first half of a detour and then go blind. The margin is
    generous for that reason, and clamped so a short hop still fetches a usable
    grid and a long one does not ask for the whole ocean.
    """
    from ...geospatial.routing import _km
    half = _km(lon1, lat1, lon2, lat2) / 2.0
    return max(150.0, min(800.0, half * 1.6))


def geo_reason(state: OrcaGraphState, config=None) -> dict:
    started = time.perf_counter()
    rt = runtime_from(config)
    agent = GeospatialAgent(llm=rt.llm, ledger=rt.ledger, budget=rt.budget)
    loc = state.get("resolved_location") or {}
    plan = state.get("plan")
    start, end = _window(state)

    result = agent.analyse(
        list(state.get("tool_results") or []),
        lat=loc.get("lat"), lon=loc.get("lon"),
        window_start=start, window_end=end,
        domains=list(getattr(plan, "domains_required", []) or [Domain.SAFETY]))

    if not result.ok:
        # Degraded, not fatal: assessment can still run on the retrieved values.
        return {"node_events": [node_event("geo_reason", "error", started=started,
                                           summary=result.failure.detail)]}
    report = result.value
    
    # NEW ROUTING LOGIC
    intent = getattr(plan, "intent", "") if plan else ""
    additional_tool_results = []
    extra_provenance = []
    route_gaps: list[NotEvaluated] = []
    
    if intent == "route_optimization" and loc.get("dest_lat") is not None:
        from ...geospatial.routing import a_star_route
        from ...schemas.data import OceanField, DerivedResult
        from ...schemas.core import SpatialRef
        from ...schemas.envelope import OrcaEnvelope
        from ...schemas.core import Provenance, TemporalRef, utcnow
        from ...schemas.enums import EnvelopeStatus
        
        # Flatten tool results properly (they are envelopes)
        fields = []
        for env in (state.get("tool_results") or []):
            if hasattr(env, "data"):
                fields.extend([e for e in env.data if isinstance(e, OceanField)])
                
        # Navigability comes from the composition root, so this module never
        # imports an adapter. Without it, routing is DECLARED UNAVAILABLE rather
        # than run unmasked -- an unmasked route crosses land (F-43).
        navigable = getattr(rt, "navigable", None)
        if navigable is None:
            return {
                "not_evaluated": [NotEvaluated(
                    factor="optimized_route", reason="DATASET_UNAVAILABLE",
                    detail="no navigability mask is configured; a route is not "
                           "offered rather than risk one that crosses land")],
                "node_events": [node_event(
                    "geo_reason", "success", started=started,
                    summary="route requested but no navigability mask configured")],
            }
        # Gridded wave and wind for STEERING.
        #
        # `tool_results` carries point values, not grids, so the field list
        # above is always empty in practice -- which made every route a
        # shortest path while the cost function sat there returning zero. The
        # grids are fetched here, for the corridor rather than for a point,
        # because that is what the router needs and nothing upstream produces.
        #
        # Failure is DECLARED, never swallowed: a route steered by nothing is a
        # shortest path, and the risk this guards against is a distance-only
        # line presented as an optimised one.
        steered_by: list[str] = []
        field_gaps: list[dict] = []
        provider = getattr(rt, "route_fields", None)
        if provider is not None:
            mid_lat = (loc["lat"] + loc["dest_lat"]) / 2.0
            mid_lon = (loc["lon"] + loc["dest_lon"]) / 2.0
            # Half the diagonal, plus room for the detour the fields may force.
            span = _corridor_radius_km(loc["lat"], loc["lon"],
                                       loc["dest_lat"], loc["dest_lon"])
            try:
                grids, grid_prov, field_gaps = provider(
                    mid_lat, mid_lon, utcnow(), span)
                fields.extend(grids)
                extra_provenance.extend(grid_prov)
                steered_by = [f.parameter for f in grids]
            except Exception as exc:                  # never fail the route
                field_gaps = [{"parameter": "route_fields",
                               "reason": "ADAPTER_ERROR",
                               "detail": f"{type(exc).__name__}: {exc}"}]

        try:
            path = a_star_route(
                start_lon=loc["lon"], start_lat=loc["lat"],
                end_lon=loc["dest_lon"], end_lat=loc["dest_lat"],
                fields=fields, is_navigable=navigable
            )
            if not path:
                return {
                    "not_evaluated": [NotEvaluated(
                        factor="optimized_route", reason="NO_DATA",
                        detail="no navigable route was found within the "
                               "snapshot region")],
                    "node_events": [node_event(
                        "geo_reason", "success", started=started,
                        summary="no navigable route found")],
                }
            # The VALUE is the route's length, not its waypoint count: a
            # count says nothing a user could act on.
            from ...geospatial.routing import _km as _leg_km
            length_km = sum(_leg_km(path[i][0], path[i][1], path[i+1][0],
                                    path[i+1][1]) for i in range(len(path) - 1))
            route_evidence = DerivedResult(
                parameter="optimized_route",
                value=round(length_km, 1), unit="km",
                spatial=SpatialRef(kind="linestring", coordinates=path),
                temporal=TemporalRef(valid_time=utcnow()),
                provenance_id="pv-orca-routing-engine-v1",
                detail={"waypoints": len(path),
                        "length_km": round(length_km, 1),
                        "navigability": "MarineRegions EEZ snapshot",
                        "advisory_only": True,
                        # What the route was actually steered by, and what it
                        # was not. A reader must be able to tell an
                        # environmentally-steered route from a shortest path,
                        # because they look identical on a map.
                        "steered_by": steered_by,
                        "fields_unavailable": field_gaps,
                        "objective": ("shortest navigable path, penalised by "
                                      + ", ".join(steered_by)) if steered_by
                                     else "shortest navigable path only",
                        "note": "planned in navigable water; not a "
                                "navigational chart and no depth is considered"
                                + ("" if steered_by else
                                   ". Sea state was NOT considered: no gridded "
                                   "wave or wind field was available, so this "
                                   "is the shortest safe-water path, not a "
                                   "weather-optimised one")}
            )
            report.derived.append(route_evidence.provenance_id)
            
            from ...schemas.enums import ValueKind
            from ...geospatial.methods import derivation
            
            route_env = OrcaEnvelope(
                status=EnvelopeStatus.SUCCESS,
                tool="a_star_route",
                data=[route_evidence],
                provenance=[Provenance(
                    provenance_id="pv-orca-routing-engine-v1", 
                    parameter="optimized_route",
                    value_kind=ValueKind.DERIVED,
                    source="orca_internal",
                    source_id="orca-routing-engine-v1",
                    derivation=derivation(
                        "a_star_route",
                        [f"{loc['lat']},{loc['lon']}",
                         f"{loc['dest_lat']},{loc['dest_lon']}"],
                        {"resolution_deg": 0.15,
                         "navigability": "MarineRegions EEZ snapshot",
                         "steered_by": steered_by},
                        module="routing")
                )] + extra_provenance
            )
            additional_tool_results.append(route_env)
            # A field that could not be fetched is a declared gap, so the
            # answer names what the route could not take into account.
            for gap in field_gaps:
                route_gaps.append(NotEvaluated(
                    factor=f"route_steering:{gap['parameter']}",
                    reason=gap["reason"], detail=gap["detail"]))
        except Exception as exc:
            # A swallowed failure here meant the user asked for a route, got a
            # safety assessment instead, and was never told routing had failed.
            # There is no straight-line fallback: a straight line between two
            # ports crosses land (F-43).
            log.exception("route planning failed")
            return {
                "alignment_report": report,
                "not_evaluated": [NotEvaluated(
                    factor="optimized_route", reason="ADAPTER_ERROR",
                    detail=f"route planning failed: "
                           f"{type(exc).__name__}: {exc}")],
                "node_events": [node_event(
                    "geo_reason", "error", started=started,
                    summary=f"route planning failed: {type(exc).__name__}")],
            }

    return {
        "alignment_report": report,
        "tool_results": additional_tool_results,
        "not_evaluated": route_gaps,
        "node_events": [node_event("geo_reason", "success", started=started,
                                   summary=result.reasoning_summary,
                                   aligned=len(report.aligned),
                                   derived=len(report.derived))],
    }
