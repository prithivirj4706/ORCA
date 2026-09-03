"""ingest, intent_context and clarify (07 section 4).

`intent_context` resolves location and time DETERMINISTICALLY. When it cannot,
it sets `clarification_needed` and the graph stops before retrieval: a position
ORCA invented would be a fabricated premise for every number that followed.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ...agents.planner import UNKNOWN_INTENT, PlannerAgent
from ..events import node_event
from ..runtime import runtime_from
from ..state import OrcaGraphState

IST = ZoneInfo("Asia/Kolkata")

#: A placeholder gazetteer. STATUS: a real deployment needs a proper gazetteer
#: with admin boundaries and alternate spellings; this covers the demo ports and
#: fails closed (asking the user) for anything else.
GAZETTEER: dict[str, tuple[float, float]] = {
    "kochi": (9.93, 76.26), "cochin": (9.93, 76.26),
    "chennai": (13.08, 80.29), "mumbai": (18.94, 72.83),
    "visakhapatnam": (17.69, 83.30), "vizag": (17.69, 83.30),
    "mangalore": (12.87, 74.84), "goa": (15.42, 73.80),
    "kanyakumari": (8.08, 77.55), "tuticorin": (8.76, 78.13),
    "paradip": (20.26, 86.67), "kolkata": (22.57, 88.36),
}

_LATLON = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*[°]?\s*([ns])\s*,?\s*(-?\d+(?:\.\d+)?)\s*[°]?\s*([ew])",
    re.IGNORECASE)


def ingest(state: OrcaGraphState, config=None) -> dict:
    started = time.perf_counter()
    # A run_id identifies ONE run, not the conversation -- that is the
    # thread_id. Restoring it from the checkpoint made every turn in a thread
    # share an id, so the audit trail could not tell two answers apart.
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    
    # Detect language from query text if not provided
    from ...i18n.detect import detect_language
    lang = state.get("language")
    if not lang:
        lang = detect_language(state.get("query_text") or "")
        
    from ..state import RESET

    # A checkpointed thread carries the previous turn's accumulated channels.
    # Clearing them here scopes every append-reduced field to THIS run, so a
    # follow-up question cannot inherit the last answer's verdicts or alerts.
    return {
        "resolution_notes": RESET, "unavailable_capabilities": RESET,
        "tool_results": RESET, "step_results": RESET, "modifications": RESET,
        "normalized_data": RESET, "fallbacks_used": RESET, "evidence_gaps": RESET,
        "derived": RESET, "layers": RESET, "assessments": RESET,
        "conflicts": RESET, "not_evaluated": RESET, "alerts": RESET,
        "map_layers": RESET, "evidence": RESET, "claims": RESET,
        "provenance": RESET, "errors": RESET,
        "run_id": run_id,
        "trace_id": run_id,
        "language": lang,
        "session_context": state.get("session_context") or {},
        "attempts": state.get("attempts", 0),
        "plan_version": state.get("plan_version", 0),
        # RESET + the event: clear the previous turn's trace, then start this
        # one. A bare RESET here would be overwritten by this same key.
        "node_events": RESET + [node_event("ingest", "success", started=started,
                                           summary=f"run {run_id}")],
    }


def _resolve_location(state: OrcaGraphState) -> tuple[dict | None, str]:
    """Origin, plus a destination when the query names one."""
    # The destination is settled FIRST, then excluded from origin matching:
    # in "plan a route to Mumbai" the only place named is the far end, and
    # treating it as the origin would route from the destination to itself.
    _, dest_key = _route_endpoints(state, (state.get("query_text") or "").lower())
    origin, note = _resolve_origin(state, exclude=dest_key)
    if dest_key and origin and origin.get("dest_lat") is not None:
        origin = {k: v for k, v in origin.items() if not k.startswith("dest_")}
        note = f"carried origin, new destination"
        
    if origin is None or origin.get("dest_lat") is not None or not dest_key:
        return origin, note
    dlat, dlon = GAZETTEER[dest_key]
    if (dlat, dlon) != (origin.get("lat"), origin.get("lon")):
        old_label = origin.get('label') or 'here'
        if " to " in old_label:
            old_label = old_label.split(" to ")[0]
        origin = {**origin, "dest_lat": dlat, "dest_lon": dlon,
                  "label": f"{old_label} to {dest_key.title()}"}
        note = f"{note}; destination {dest_key!r}"
    return origin, note


def _resolve_origin(state: OrcaGraphState,
                    exclude: str | None = None) -> tuple[dict | None, str]:
    explicit = state.get("client_location")
    if explicit and explicit.get("lat") is not None:
        return explicit, "location supplied by the caller"

    text = (state.get("query_text") or "").lower()
    
    # -- route endpoints ---------------------------------------------------
    # Accepts "from A to B" and bare "to B" (origin = wherever else resolves),
    # matches multi-word gazetteer names, and reads the user's own script.
    origin_key, dest_key = _route_endpoints(state, text)
    if dest_key and origin_key:
        olat, olon = GAZETTEER[origin_key]
        dlat, dlon = GAZETTEER[dest_key]
        return ({"lat": olat, "lon": olon, "dest_lat": dlat, "dest_lon": dlon,
                 "label": f"{origin_key.title()} to {dest_key.title()}"},
                f"route {origin_key!r} to {dest_key!r}")

    # A place written in the user's own script resolves through the language's
    # own lexicon; the Latin gazetteer is still tried, because people mix scripts.
    lang = state.get("language") or "en"
    if lang != "en":
        from ...i18n.generate import native_place
        key = native_place(lang, state.get("query_text") or "")
        if key and key in GAZETTEER and key != exclude:
            lat, lon = GAZETTEER[key]
            return ({"lat": lat, "lon": lon, "label": f"near {key.title()}"},
                    f"gazetteer match {key!r} via {lang} lexicon")

    for name, (lat, lon) in GAZETTEER.items():
        if name != exclude and re.search(rf"\b{name}\b", text):
            return ({"lat": lat, "lon": lon, "label": f"near {name.title()}"},
                    f"gazetteer match {name!r}")

    carried = (state.get("session_context") or {}).get("resolved_location")
    if carried:
        return carried, "carried from session context"
    return None, "no location in the query, the session or the gazetteer"


def _query_names_a_place(state: OrcaGraphState) -> bool:
    """Does the QUERY TEXT itself name a place or a position?

    Deliberately ignores the session and any client GPS. A carried location is
    what makes a follow-up answerable, but it does not make "hi" a marine
    question -- and reading it as one is exactly how a greeting mid-conversation
    came to inherit the previous turn and be answered again.
    """
    raw = state.get("query_text") or ""
    text = raw.lower()
    if _LATLON.search(text):
        return True
    if any(re.search(rf"\b{name}\b", text) for name in GAZETTEER):
        return True
    lang = state.get("language") or "en"
    if lang != "en":
        from ...i18n.generate import native_place
        if native_place(lang, raw):
            return True
    return False


#: Words that may sit between `to` and a place without changing what it means:
#: "to the port of Chennai" still names Chennai.
_DEST_FILLER = re.compile(
    r"^(?:the|a|an|towards?|near|off|around|port\s+of|city\s+of|coast\s+of)\s+")


def _place_at_start(fragment: str, lang: str) -> str | None:
    """The place a fragment NAMES, or None when it merely mentions one.

    `to` is an infinitive marker at least as often as it is a preposition, and
    the two readings put the place in completely different roles:

        "safest route TO Chennai"          -> Chennai is the destination
        "is it safe TO go out near Kochi"  -> Kochi is where the asker IS

    A substring search cannot tell them apart. It found `kochi` in the middle of
    a verb phrase, made it the destination, and then excluded it from origin
    matching -- so the commonest phrasing of the commonest question resolved no
    location at all and asked "where?" (F-72).

    Anchoring the name to the START of the fragment separates them: a
    destination follows `to` directly, give or take a determiner.
    """
    frag = fragment.lower().strip()
    while True:
        m = _DEST_FILLER.match(frag)
        if not m:
            break
        frag = frag[m.end():]
    # Longest first, so a name that prefixes another cannot shadow it.
    for key in sorted(GAZETTEER, key=len, reverse=True):
        if re.match(rf"{re.escape(key)}\b", frag):
            return key
    if lang != "en":
        from ...i18n.generate import native_place
        return native_place(lang, frag)
    return None


def _route_endpoints(state, text: str) -> tuple[str | None, str | None]:
    """(origin_key, destination_key) as gazetteer keys, either may be None.

    Both endpoints must be NAMED at their slot, not merely mentioned somewhere
    inside it; the language's own place lexicon handles native scripts.
    """
    lang = state.get("language") or "en"
    raw = state.get("query_text") or ""

    def find(fragment: str) -> str | None:
        return _place_at_start(fragment, lang)

    m = re.search(r"\bfrom\s+(.{2,40}?)\s+to\s+(.{2,40}?)\s*[?.!]?$", text)
    if m:
        o, d = find(m.group(1)), find(m.group(2))
        if d:
            return (o if o != d else None), d

    m = re.search(r"\bto\s+(.{2,40}?)\s*[?.!]?$", text)
    if m:
        d = find(m.group(1))
        if d:
            return None, d

    if state.get("clarification_needed") == "destination":
        for name in GAZETTEER:
            if re.search(rf"\b{name}\b", text):
                return None, name

    # Native scripts rarely use "from/to"; take two distinct places in order.
    if lang != "en":
        from ...i18n.generate import section
        found = [(raw.find(native), key)
                 for native, key in section(lang, "place").items()
                 if native in raw]
        found.sort()
        if len(found) >= 2 and found[0][1] != found[1][1]:
            return found[0][1], found[1][1]
    return None, None


def _resolve_window(state: OrcaGraphState, window_hours: int) -> tuple[dict | None, str]:
    """The analysis window: THIS turn's words first, then what was carried.

    `resolved_time_window` is an OUTPUT channel, and a checkpointed thread
    restores it, so reading it here made every turn after the first reuse the
    first turn's window -- "what about tonight?" was answered for tomorrow
    morning, and the resolution note said "window supplied by the caller"
    while it did so (F-73). A confidently wrong time is exactly the failure
    this system exists to avoid, so the query is parsed BEFORE any carried
    value is consulted, mirroring how a place named in the query already beats
    the one carried in the session.
    """
    text = (state.get("query_text") or "").lower()
    lang = state.get("language") or "en"
    if lang != "en":
        # Fold the native time words into the same English keys the rules below
        # already understand, so there is one set of rules, not one per language.
        from ...i18n.generate import native_time
        text = text + " " + " ".join(sorted(native_time(lang, state.get("query_text") or "")))

    now_ist = datetime.now(IST)
    start = None
    if "tomorrow" in text:
        base = now_ist + timedelta(days=1)
        hour = 6 if ("morning" in text or "dawn" in text) else 12
        start = base.replace(hour=hour, minute=0, second=0, microsecond=0)
    elif "tonight" in text or "evening" in text:
        start = now_ist.replace(hour=18, minute=0, second=0, microsecond=0)
    elif "today" in text or "now" in text or "right now" in text:
        start = now_ist
    if start is not None:
        start_utc = start.astimezone(timezone.utc)
        return ({"start_time": start_utc.isoformat(),
                 "end_time": (start_utc + timedelta(hours=window_hours)).isoformat()},
                "parsed from the query, IST to UTC")

    # No time in this turn's words. A per-turn window from the caller comes
    # next, then the one the conversation established -- "what about the
    # fishing?" after "tomorrow morning" still means tomorrow morning.
    explicit = state.get("client_time_window")
    if explicit and explicit.get("start_time"):
        return explicit, "window supplied by the caller"
    carried = (state.get("session_context") or {}).get("resolved_time_window")
    if carried and carried.get("start_time"):
        return carried, "window carried from session context"
    return None, "no time expression recognised in the query"


def intent_context(state: OrcaGraphState, config=None) -> dict:
    started = time.perf_counter()
    rt = runtime_from(config)
    planner = PlannerAgent(llm=rt.llm, ledger=rt.ledger, budget=rt.budget)

    intent = planner.classify(state.get("query_text") or "",
                              language=state.get("language") or "en")
    location, loc_note = _resolve_location(state)
    window, win_note = _resolve_window(state, rt.window_hours)

    # The planner judges VOCABULARY; it cannot see the gazetteer or the thread.
    # Two escapes are applied here, both of which exist to stop a real answer
    # being refused:
    #
    #   * a query that resolved a PLACE is about somewhere, and "near Kochi" or
    #     "to Chennai" carry no marine noun of their own;
    #   * a thread with a question outstanding is receiving its ANSWER, and an
    #     answer is as short and bare as the question made it.
    #
    # Both are the clarification loop's own replies, so getting this wrong
    # would break the conversation rather than merely a stray greeting.
    if intent == "smalltalk_or_out_of_scope" and (
            _query_names_a_place(state) or state.get("clarification_needed")):
        intent = UNKNOWN_INTENT

    if intent == UNKNOWN_INTENT:
        # An unclassifiable query inside a live conversation is a follow-up:
        # "what about tomorrow?" means whatever the last turn meant. Out of
        # scope is NOT carried -- greeting someone mid-thread must not inherit
        # the previous question and answer it again.
        carried_intent = (state.get("session_context") or {}).get("intent")
        # Defensive: an older checkpoint may still hold a non-topic here.
        if carried_intent and carried_intent not in (
                UNKNOWN_INTENT, "smalltalk_or_out_of_scope"):
            intent = carried_intent

    return {
        "intent": intent,
        "intent_confidence": 1.0 if planner._planner_id() == "deterministic" else 0.9,
        "resolved_location": location,
        "resolved_time_window": window,
        "resolution_notes": [loc_note, win_note],
        "node_events": [node_event("intent_context", "success", started=started,
                                   summary=f"intent={intent}; {loc_note}; {win_note}")],
    }


def out_of_scope(state: OrcaGraphState, config=None) -> dict:
    """Terminal. Says what ORCA does, rather than asking about a sea area.

    The alternative -- the behaviour this replaces -- was to ask "Where are you
    asking about?" for any unrecognised text. That is not a neutral fallback: it
    ASSERTS that the question was a marine one merely missing a detail, so
    "what is c programming" was told its location was the problem, and a user
    who then supplied one would be asked for an intent instead. Nothing was ever
    fabricated, but the exchange was untrue about itself.
    """
    started = time.perf_counter()
    return {
        "disposition": "OUT_OF_SCOPE",
        "recommendation": {
            "category": "OUT_OF_SCOPE",
            "headline": "That is outside what I can answer. I cover sea "
                        "conditions, fishing suitability, maritime boundaries "
                        "and routes in Indian waters.",
            # The narrative LEADS the answer in the interface and its first
            # line becomes the headline there, so it opens with the same
            # sentence rather than with the guidance that follows it.
            "narrative": "That is outside what I can answer.\n"
                         "I cover sea conditions, fishing suitability, "
                         "maritime boundaries and routes in Indian waters. "
                         "Ask about a place and a time \u2014 for example, "
                         "\u201cis it safe near Kochi tomorrow morning?\u201d",
            "is_official_advisory": False,
        },
        "node_events": [node_event("out_of_scope", "success", started=started,
                                   summary="query is not about the marine domain")],
    }


def clarify(state: OrcaGraphState, config=None) -> dict:
    """Terminal. Asks exactly one question rather than guessing a premise."""
    started = time.perf_counter()
    plan = state.get("plan")
    needed = (getattr(plan, "clarification_needed", None)
              or state.get("clarification_needed") or "location")
    questions = {
        "location": "Where are you asking about? A place name or a "
                    "latitude and longitude will do.",
        "time_window": "For when? For example 'tomorrow morning' or a date and time.",
        "intent": "What would you like to know about that sea area — safety, "
                  "fishing conditions, or maritime boundaries?",
        "destination": "Where would you like to sail to? Name a port and I will "
                       "plan a route that stays in navigable water.",
    }
    return {
        "clarification_needed": needed,
        "recommendation": {"category": "CLARIFICATION_NEEDED",
                           "headline": questions.get(needed, questions["location"]),
                           "is_official_advisory": False},
        "node_events": [node_event("clarify", "success", started=started,
                                   summary=f"asked for {needed}")],
    }
