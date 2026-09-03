"""ORCA HTTP API (08_API_SPEC.md).

A thin layer over the graph. It adds no reasoning: every field it returns was
produced by the pipeline and is already provenance-bound.

Two things it does own, because they are deployment concerns rather than
reasoning ones:

  * **Adapter lifetime.** The registry is built ONCE at startup and held for the
    process. Building it per request opened a fresh HTTP client per source per
    call, which is slow and leaks connections.
  * **Checkpointed threads.** A `thread_id` is a conversation. LangGraph's
    checkpointer restores prior state, and `session_context` carries the
    resolved location and window forward, so "what about tomorrow?" works.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel

from ..graph.build import build_graph
from ..graph.runtime import OrcaRuntime
from ..llm.provider import resolve_provider
from ..tools.live import bind_live_tools, build_sea_mask
from ..tools.registry import CATALOGUE

log = logging.getLogger("orca.api")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data",
                       "orca_memory.db")

_state: dict[str, Any] = {}

#: Fields are large and slow to fetch; keyed to the hour, which is finer than
#: any of the products actually update.
_field_cache: dict[tuple, Any] = {}


def _feature_polygons(index, feature: int, bbox, max_points: int = 450) -> list:
    """A feature's polygons as GeoJSON MultiPolygon coordinates.

    The index is flat arrays: feature -> polygons -> rings -> coords. Rings
    outside the requested bbox are skipped, and long rings are decimated -- a
    single EEZ ring runs to hundreds of thousands of vertices and would lock a
    browser. Decimation is a DISPLAY concern only: containment and distance are
    always computed against the full-precision geometry (D-14).
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    p0, p1 = int(index.feature_offsets[feature]), int(index.feature_offsets[feature + 1])
    polygons = []
    for p in range(p0, p1):
        r0, r1 = int(index.polygon_offsets[p]), int(index.polygon_offsets[p + 1])
        rings = []
        for r in range(r0, r1):
            ring = index.ring(r)
            if ring.shape[0] < 4:
                continue
            lons, lats = ring[:, 0], ring[:, 1]
            if (lons.max() < min_lon or lons.min() > max_lon
                    or lats.max() < min_lat or lats.min() > max_lat):
                continue
            step = max(1, ring.shape[0] // max_points)
            pts = [[round(float(x), 4), round(float(y), 4)]
                   for x, y in ring[::step]]
            if pts[0] != pts[-1]:
                pts.append(pts[0])          # GeoJSON rings must close
            rings.append(pts)
        if rings:
            polygons.append(rings)
    return polygons


def _field_adapters():
    """Long-lived adapters for map fields, held for the process like the registry."""
    cmems = gfs = snapshot = None
    try:
        from ..adapters.cmems.adapter import CmemsAdapter
        cmems = CmemsAdapter()
    except Exception:
        log.warning("CMEMS unavailable; scalar map fields will 502")
    try:
        from ..adapters.noaa_gfs.adapter import NoaaGfsAdapter
        gfs = NoaaGfsAdapter()
    except Exception:
        log.warning("GFS unavailable; the wind field will 502")
    try:
        from ..adapters.marineregions.adapter import MarineRegionsAdapter
        snapshot = MarineRegionsAdapter().snapshot
    except Exception:
        log.warning("no boundary snapshot; /v1/boundaries will 503")
    return cmems, gfs, snapshot


def _route_field_provider(cmems, gfs):
    """Bind the gridded-field fetch the router steers by.

    Held at the composition root for the same reason `navigable` is: the graph
    node must not reach for an adapter, and a missing provider has to degrade to
    a DECLARED distance-only route rather than a silent one.
    """
    from ..tools.fields import route_fields

    def provider(lat: float, lon: float, valid_time: datetime,
                 radius_km: float):
        return route_fields(lat, lon, valid_time, radius_km=radius_km,
                            cmems=cmems, gfs=gfs)
    return provider


def _sea_mask():
    """Route navigability from the versioned boundary snapshot.

    Absent (no snapshot captured) means routing is declared unavailable, which
    is the correct degradation: a route without a land mask crosses land.
    """
    try:
        from ..adapters.marineregions.adapter import MarineRegionsAdapter
        return build_sea_mask(MarineRegionsAdapter())
    except Exception:
        log.warning("no boundary snapshot; route planning will be unavailable")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _state["conn"] = conn
    _state["graph"] = build_graph(checkpointer=SqliteSaver(conn))
    _state["registry"] = bind_live_tools()      # built once, held for the process
    _state["navigable"] = _sea_mask()
    _state["cmems"], _state["gfs"], _state["snapshot"] = _field_adapters()
    _state["route_fields"] = _route_field_provider(_state["cmems"], _state["gfs"])
    _state["llm"] = resolve_provider()
    log.info("ORCA ready · tools=%d · llm=%s",
             len(_state["registry"].available_names()), _state["llm"].name)
    yield
    conn.close()


app = FastAPI(title="ORCA API",
              description="Agentic marine intelligence — evidence-bound answers",
              lifespan=lifespan)

# A browser UI is a separate origin; without this every fetch fails.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

@app.middleware("http")
async def _no_cache_ui(request, call_next):
    """Never cache the interface.

    A stale app.js is indistinguishable from a bug, and costs a confusing
    debugging round every time the UI changes.
    """
    response = await call_next(request)
    if request.url.path.startswith("/ui"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


# Two interfaces ship. `webui/` is the React build (`npm run build` in ui/) and
# is what /ui serves when present; `static/` is the dependency-free vanilla UI,
# always available at /classic/ so a broken frontend build can never take the
# demo down with it.
_STATIC = os.path.join(os.path.dirname(__file__), "static")
_WEBUI = os.path.join(os.path.dirname(__file__), "webui")
if os.path.isdir(_STATIC):
    app.mount("/classic", StaticFiles(directory=_STATIC, html=True), name="classic")
if os.path.isdir(_WEBUI):
    app.mount("/ui", StaticFiles(directory=_WEBUI, html=True), name="ui")
elif os.path.isdir(_STATIC):
    app.mount("/ui", StaticFiles(directory=_STATIC, html=True), name="ui")


@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/ui/")


def _runtime() -> OrcaRuntime:
    return OrcaRuntime(registry=_state["registry"], llm=_state["llm"],
                       navigable=_state.get("navigable"),
                       route_fields=_state.get("route_fields"))


def _dump(x: Any) -> Any:
    """Pydantic models, dataclasses and plain values all reach the client."""
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, list):
        return [_dump(i) for i in x]
    if isinstance(x, dict):
        return {k: _dump(v) for k, v in x.items()}
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    if hasattr(x, "__dict__"):
        return {k: _dump(v) for k, v in vars(x).items() if not k.startswith("_")}
    return str(x)


class ChatRequest(BaseModel):
    query: str
    thread_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    language: str | None = None          # omit to auto-detect from the query


def _initial_state(req: ChatRequest) -> dict:
    from ..i18n.detect import detect_language

    # Detect per TURN, not per thread. A checkpoint restores the previous
    # turn's language, but the problem statement asks for the language of the
    # query in hand — someone may ask in Malayalam and follow up in English.
    state: dict[str, Any] = {
        "query_text": req.query,
        "language": req.language or detect_language(req.query),
    }
    if req.lat is not None and req.lon is not None:
        state["client_location"] = {"lat": req.lat, "lon": req.lon,
                                    "label": None}
    return state


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _as_dict(x: Any) -> dict:
    if isinstance(x, dict):
        return x
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    return {k: v for k, v in vars(x).items() if not k.startswith("_")}


def _temporal_alignment(final: dict) -> dict:
    """Every retrieved value's validity window against the analysis window.

    This is the answer to "why was 2011 SST rejected and two-day-old
    chlorophyll accepted?", which is otherwise the least visible and most
    load-bearing judgement ORCA makes. The strip is built from PROVENANCE, not
    from evidence, because provenance records everything that was RETRIEVED --
    including the values the aligner then refused. A strip drawn from evidence
    alone would show only the survivors and so could never show the rejection.

    Nothing here is computed for the client: every field is a value the
    pipeline already produced, joined on provenance_id and stamped with the age
    at the time of the request.
    """
    win = final.get("resolved_time_window") or {}
    now = datetime.now(timezone.utc)

    # provenance_id -> the record, so a derivation's inputs can be followed.
    by_id: dict[str, dict] = {}
    for env in final.get("tool_results") or []:
        for rec in getattr(env, "provenance", []) or []:
            r = _as_dict(rec)
            if r.get("provenance_id"):
                by_id[r["provenance_id"]] = r

    # provenance_id -> the evidence that used it, when any did
    used_by: dict[str, dict] = {}
    for ev in final.get("evidence") or []:
        e = _as_dict(ev)
        pid = e.get("provenance_id")
        if pid:
            used_by[pid] = e

    # A DERIVED value's inputs were used too, just not directly: the raw
    # chlorophyll behind chlorophyll_ratio_to_local_median is the reason the
    # ratio exists. Marking it unused would put the strip's most important
    # distinction -- retrieved-and-refused versus retrieved-and-used -- on the
    # wrong row. Walk the derivation lineage and attribute the inputs to the
    # same evidence, guarding against a cycle in the recorded chain.
    pending = list(used_by.items())
    while pending:
        pid, ev = pending.pop()
        deriv = (by_id.get(pid) or {}).get("derivation") or {}
        for src in deriv.get("inputs") or []:
            if src in used_by:
                continue
            used_by[src] = ev
            pending.append((src, ev))

    # Why a factor was NOT evaluated, keyed by the factor/parameter name, so a
    # rejected row can carry its own reason rather than just going missing.
    reasons: dict[str, dict] = {}
    for a in final.get("assessments") or []:
        for n in _as_dict(a).get("not_evaluated") or []:
            n = _as_dict(n)
            if n.get("factor"):
                reasons.setdefault(n["factor"], n)

    def _reason_for(param: str | None) -> dict:
        """A factor name is not always the parameter name it came from.

        `sst_anomaly_abs` is judged from the `sst_anomaly` parameter, so an
        exact join drops precisely the rows whose exclusion the strip exists to
        explain. Fall back to the longest factor that extends the parameter.
        """
        if not param:
            return {}
        if param in reasons:
            return reasons[param]
        cand = [f for f in reasons if f.startswith(param)]
        return reasons[max(cand, key=len)] if cand else {}

    entries, seen = [], set()
    for env in final.get("tool_results") or []:
        tool = getattr(env, "tool", None)
        for rec in getattr(env, "provenance", []) or []:
            r = _as_dict(rec)
            pid = r.get("provenance_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            derived_via = ((r.get("derivation") or {}).get("method")
                           if r.get("derivation") else None)
            t = r.get("temporal") or {}
            valid = t.get("valid_time")
            age_s = None
            if valid:
                try:
                    age_s = (now - datetime.fromisoformat(str(valid))).total_seconds()
                except ValueError:
                    age_s = None
            ev = used_by.get(pid)
            param = r.get("parameter")
            entries.append({
                "provenance_id": pid,
                "tool": tool,
                "parameter": param,
                "value_kind": r.get("value_kind"),
                "source": r.get("source"),
                "source_id": r.get("source_id"),
                "dataset": r.get("dataset"),
                "valid_time": _iso(valid),
                "valid_from": _iso(t.get("valid_from")),
                "valid_to": _iso(t.get("valid_to")),
                "reference_time": _iso(t.get("reference_time")),
                "lead_time_h": t.get("lead_time_h"),
                "representativeness": t.get("representativeness"),
                "retrieved_at": _iso(t.get("retrieved_at") or r.get("retrieved_at")),
                "age_s": age_s,
                # `used` is the whole point of the strip: a row that was
                # retrieved and then NOT used is the interesting one.
                "used": ev is not None,
                "derived_via": derived_via,
                "evidence_id": (ev or {}).get("evidence_id"),
                "domain": (ev or {}).get("domain"),
                "excluded_reason": _reason_for(param).get("reason"),
                "excluded_detail": _reason_for(param).get("detail"),
            })

    entries.sort(key=lambda e: (e["valid_time"] or "", e["parameter"] or ""))
    return {
        "window": {"start_time": win.get("start_time"),
                   "end_time": win.get("end_time")},
        "generated_at": now.isoformat(),
        "entries": entries,
    }


def _project(final: dict, thread_id: str) -> dict:
    """The client projection: everything the UI renders, nothing internal."""
    rec = final.get("recommendation")
    plan = final.get("plan")
    return {
        "thread_id": thread_id,
        "language": final.get("language", "en"),
        "intent": final.get("intent"),
        "resolved_location": final.get("resolved_location"),
        "resolved_time_window": final.get("resolved_time_window"),
        "resolution_notes": final.get("resolution_notes") or [],
        "clarification_needed": final.get("clarification_needed"),
        "plan": {
            "domains": [d.value if hasattr(d, "value") else str(d)
                        for d in getattr(plan, "domains_required", [])],
            "required_evidence": getattr(plan, "required_evidence", []),
            "steps": [{"step_id": s.step_id, "tool": s.tool,
                       "necessity": s.necessity}
                      for s in getattr(plan, "steps", [])],
            "unavailable": getattr(plan, "unavailable_capabilities", []),
            "reasoning_summary": getattr(plan, "reasoning_summary", ""),
        } if plan is not None else None,
        "assessments": _dump(final.get("assessments") or []),
        "evidence": _dump(final.get("evidence") or []),
        "alerts": _dump(final.get("alerts") or []),
        "map_layers": _dump(final.get("map_layers") or final.get("layers") or []),
        "claims": _dump(final.get("claims") or []),
        "not_evaluated": _dump(final.get("not_evaluated") or []),
        "temporal_alignment": _temporal_alignment(final),
        "disposition": final.get("disposition"),
        "recommendation": _dump(rec),
        "trace": [_dump(e) for e in (final.get("node_events") or [])],
    }


@app.post("/v1/chat")
def chat(req: ChatRequest):
    thread_id = req.thread_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id, **_runtime().configurable()}}
    try:
        final = _state["graph"].invoke(_initial_state(req), config=config)
    except Exception as exc:                     # never leak a stack trace
        log.exception("graph failed")
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
    return _project(final, thread_id)


@app.post("/v1/chat/stream")
def chat_stream(req: ChatRequest):
    """Server-sent events: one message per completed graph node.

    This is what lets a UI show the agents working — the plan forming, each
    tool resolving, the domains being assessed — rather than a spinner.
    `node_events` already carry no chain-of-thought, so the feed is a filter
    over what exists (07 §12).
    """
    thread_id = req.thread_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id, **_runtime().configurable()}}

    def gen():
        yield f"event: start\ndata: {json.dumps({'thread_id': thread_id})}\n\n"
        final: dict[str, Any] = {}
        sent = 0
        try:
            for chunk in _state["graph"].stream(_initial_state(req), config=config,
                                                stream_mode="values"):
                final = chunk
                events = chunk.get("node_events") or []
                # Emit EVERY new event, not just the last. A superstep that ran
                # six tools in parallel produces six events at once; sending
                # only the newest collapsed the fan-out to a single line and
                # hid the one thing the trace exists to show.
                for ev in events[sent:]:
                    payload = json.dumps(_dump(ev), default=str)
                    yield f"event: node\ndata: {payload}\n\n"
                sent = len(events)
            body = json.dumps(_project(final, thread_id), default=str)
            yield f"event: result\ndata: {body}\n\n"
        except Exception as exc:
            log.exception("stream failed")
            err = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
            yield f"event: error\ndata: {err}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/v1/health")
def health():
    return {"status": "ok",
            "tools_bound": len(_state["registry"].available_names()),
            "llm": _state["llm"].name,
            "llm_available": _state["llm"].available}


@app.get("/v1/health/sources")
def health_sources():
    """What each capability can do right now, without calling them."""
    r = _state["registry"]
    return {"sources": [
        {"tool": s.name, "description": s.description,
         "yields": list(s.yields),
         "domains": [d.value for d in s.domains],
         "available": r.is_available(s.name),
         "unavailable_reason": r.unavailable_reason(s.name)}
        for s in CATALOGUE]}


@app.get("/v1/field/{name}")
def get_field_endpoint(name: str,
                       lat: float = Query(...), lon: float = Query(...),
                       radius_km: float = Query(300.0, ge=10, le=800),
                       valid_time: str | None = Query(default=None)):
    """A gridded field for map rendering: wind, current, chlorophyll, sst, waves.

    Masked cells come back as `null`, and `cells.coverage` says what fraction is
    real, so a renderer can show a hole as a hole. Drawing a masked cell as zero
    would paint a calm sea over missing data.
    """
    from ..tools.fields import FIELDS, FieldError, get_field

    when = (datetime.fromisoformat(valid_time) if valid_time
            else datetime.now(timezone.utc))
    key = (name, round(lat, 2), round(lon, 2), radius_km,
           when.strftime("%Y%m%d%H"))
    hit = _field_cache.get(key)
    if hit is not None:
        return hit
    try:
        out = get_field(name, lat, lon, when, radius_km=radius_km,
                        cmems=_state.get("cmems"), gfs=_state.get("gfs"))
    except FieldError as exc:
        raise HTTPException(
            404 if exc.code in ("NO_DATA", "DATASET_UNAVAILABLE") else 502,
            f"{exc.code}: {exc.detail}") from exc
    except Exception as exc:
        log.exception("field fetch failed")
        raise HTTPException(502, f"{type(exc).__name__}: {exc}") from exc
    # Fields are big and slow; an hourly key is plenty for a live map.
    if len(_field_cache) > 24:
        _field_cache.clear()
    _field_cache[key] = out
    return out


@app.get("/v1/fields")
def list_fields():
    from ..tools.fields import FIELDS
    return {"fields": [{"name": k, **{x: y for x, y in v.items()
                                      if x in ("kind", "unit", "label")}}
                       for k, v in FIELDS.items()]}


@app.get("/v1/boundaries")
def boundaries(min_lat: float = Query(...), min_lon: float = Query(...),
               max_lat: float = Query(...), max_lon: float = Query(...),
               layer: str = Query("MarineRegions:eez")):
    """Maritime boundary geometry as GeoJSON, for the map.

    Served from the versioned snapshot, so the map draws the SAME geometry the
    REGULATORY verdict was decided against, and carries the dataset version
    that says which.
    """
    snap = _state.get("snapshot")
    if snap is None:
        raise HTTPException(503, "no boundary snapshot; run "
                                 "scripts/capture_boundaries.py")
    try:
        ls = snap.layer(layer)
    except Exception as exc:
        raise HTTPException(404, f"unknown layer {layer!r}: {exc}") from exc

    bbox = (min_lat, min_lon, max_lat, max_lon)
    features = []
    for fi in range(ls.index.feature_count):
        coords = _feature_polygons(ls.index, fi, bbox)
        if not coords:
            continue
        attrs = ls.features[fi] if fi < len(ls.features) else None
        # BoundaryFeature uses __slots__, so vars() does not work on it.
        if attrs is None:
            props = {}
        elif isinstance(attrs, dict):
            props = dict(attrs)
        else:
            props = {k: _dump(getattr(attrs, k, None))
                     for k in getattr(attrs, "__slots__", ())
                     if k not in ("index", "bbox")}
        features.append({
            "type": "Feature",
            "geometry": {"type": "MultiPolygon", "coordinates": coords},
            "properties": {**props, "layer": layer,
                           "dataset_version": ls.dataset_version,
                           "advisory_only": True}})
    return {"type": "FeatureCollection", "features": features,
            "layer": layer,
            "dataset_version": ls.dataset_version,
            "snapshot_version": snap.snapshot_version,
            "advisory_only": True,
            "note": "geometry is decimated for display; containment and "
                    "distance use full precision"}


@app.get("/v1/boundaries/layers")
def boundary_layers():
    snap = _state.get("snapshot")
    if snap is None:
        raise HTTPException(503, "no boundary snapshot")
    return {"snapshot_version": snap.snapshot_version, "layers": snap.layers}


@app.get("/v1/runs/{thread_id}")
def get_run(thread_id: str):
    """Replay the last state of a thread from its checkpoint."""
    config = {"configurable": {"thread_id": thread_id, **_runtime().configurable()}}
    snap = _state["graph"].get_state(config)
    if not snap or not snap.values:
        raise HTTPException(404, f"no run for thread {thread_id}")
    return _project(snap.values, thread_id)


@app.get("/v1/runs/{thread_id}/provenance")
def get_provenance(thread_id: str,
                   provenance_id: str | None = Query(default=None)):
    """The provenance chain behind a value — the evidence panel's L2/L3."""
    config = {"configurable": {"thread_id": thread_id, **_runtime().configurable()}}
    snap = _state["graph"].get_state(config)
    if not snap or not snap.values:
        raise HTTPException(404, f"no run for thread {thread_id}")
    records = []
    for env in snap.values.get("tool_results") or []:
        records.extend(getattr(env, "provenance", []) or [])
    dumped = [_dump(p) for p in records]
    if provenance_id:
        dumped = [p for p in dumped if p.get("provenance_id") == provenance_id]
        if not dumped:
            raise HTTPException(404, f"no provenance {provenance_id}")
    return {"thread_id": thread_id, "provenance": dumped}
