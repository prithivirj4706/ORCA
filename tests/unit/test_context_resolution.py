"""Where and when a question is about, resolved from the turn in hand.

Both bugs fixed here were wrong-premise bugs, which are the worst kind this
pipeline can have: every number downstream is correct FOR A PLACE OR TIME THE
USER DID NOT ASK ABOUT, so nothing later can detect them.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.orca.graph.nodes.context import _resolve_location, _resolve_window

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")


def loc(query, **state):
    return _resolve_location({"query_text": query, "language": "en", **state})


def win(query, **state):
    return _resolve_window({"query_text": query, "language": "en", **state}, 4)


class TestToIsUsuallyAnInfinitive:
    """`to` marks a verb far more often than it marks a destination.

    Reading it as a destination made the place the asker was standing in the
    place they were going to, excluded it from origin matching, and resolved
    nothing -- so the commonest phrasing of the commonest question answered
    "where are you asking about?" (F-72).
    """

    @pytest.mark.parametrize("query,expected", [
        ("is it safe to go out near Kochi tomorrow morning?", "near Kochi"),
        ("is it safe to sail near Chennai?", "near Chennai"),
        ("do I need to worry about waves near Mumbai?", "near Mumbai"),
        ("is it safe to go fishing near Goa?", "near Goa"),
        ("I need to know about the sea near Vizag", "near Vizag"),
    ])
    def test_an_infinitive_does_not_steal_the_place(self, query, expected):
        place, _ = loc(query)
        assert place is not None, "no location resolved at all"
        assert place["label"] == expected
        assert place.get("dest_lat") is None, "the origin became a destination"

    @pytest.mark.parametrize("query", [
        "safest route from Kochi to Chennai",
        "route from Kochi to the port of Chennai",
    ])
    def test_a_real_destination_still_resolves(self, query):
        """The fix must not cost us the feature it was protecting."""
        place, _ = loc(query)
        assert place["dest_lat"] == pytest.approx(13.08)
        assert place["lat"] == pytest.approx(9.93)

    def test_a_destination_answer_after_a_question_still_works(self):
        place, _ = loc("Chennai", clarification_needed="destination",
                       session_context={"resolved_location":
                                        {"lat": 9.93, "lon": 76.26,
                                         "label": "near Kochi"}})
        assert place["dest_lat"] == pytest.approx(13.08)


class TestTheWindowFollowsTheTurn:
    """`resolved_time_window` is an OUTPUT channel that a checkpoint restores.

    Reading caller input off it meant every turn after the first reused the
    first turn's window: "what about tonight?" was answered for tomorrow
    morning, and the resolution note claimed the caller had supplied it (F-73).
    """

    def test_this_turns_words_beat_a_carried_window(self):
        yesterday = {"start_time": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                     "end_time": datetime.now(UTC).isoformat()}
        window, note = win("what about tonight?",
                           session_context={"resolved_time_window": yesterday})
        assert window["start_time"] != yesterday["start_time"]
        assert "parsed from the query" in note
        # 18:00 IST today
        assert datetime.fromisoformat(window["start_time"]).astimezone(IST).hour == 18

    def test_a_restored_output_channel_is_ignored(self):
        """The bug itself: the graph's own previous answer must not be read
        back in as though the caller had asked for it."""
        stale = {"start_time": "2020-01-01T00:00:00+00:00",
                 "end_time": "2020-01-01T04:00:00+00:00"}
        window, note = win("is it safe right now?", resolved_time_window=stale)
        assert window["start_time"] != stale["start_time"]

    def test_a_turn_with_no_time_carries_the_conversations(self):
        """"what about the fishing?" after "tomorrow morning" still means
        tomorrow morning -- the carry is the point, only its priority was wrong."""
        carried = {"start_time": "2026-09-04T00:30:00+00:00",
                   "end_time": "2026-09-04T04:30:00+00:00"}
        window, note = win("how is the fishing?",
                           session_context={"resolved_time_window": carried})
        assert window == carried
        assert "carried from session context" in note

    def test_a_caller_window_is_used_when_the_query_has_no_time(self):
        supplied = {"start_time": "2026-09-04T00:30:00+00:00",
                    "end_time": "2026-09-04T04:30:00+00:00"}
        window, note = win("is it safe?", client_time_window=supplied)
        assert window == supplied
        assert "supplied by the caller" in note

    def test_no_time_anywhere_is_reported_as_such(self):
        window, note = win("is it safe?")
        assert window is None
        assert "no time expression" in note
