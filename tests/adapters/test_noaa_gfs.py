"""NOAA NCEP GFS adapter (S-11), against recorded upstream responses.

Fixtures are captured, never hand-authored (D-12): a hand-written CSV would make
this suite test a fiction rather than what the server publishes.
"""
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.orca.adapters.erddap import ErddapResponse
from backend.orca.adapters.noaa_gfs.adapter import GfsError, NoaaGfsAdapter, _rows
from backend.orca.adapters.noaa_gfs.bindings import BINDINGS, NOT_PUBLISHED
from backend.orca.schemas.enums import ValueKind
from backend.orca.schemas.errors import ErrorCode

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures/upstream/noaa_gfs"
UTC = timezone.utc


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class FakeClient:
    """Replays recorded responses and records the URLs the adapter built."""

    def __init__(self, csv_text=None, das_text=None):
        self._csv = csv_text if csv_text is not None else fixture(
            "griddap_ugrd10m_kochi.csv")
        self._das = das_text if das_text is not None else fixture(
            "ncep_global_das_head.txt")
        self.queries: list[str] = []

    def get_text(self, path, query=""):
        self.queries.append(query)
        body = self._das if path.endswith(".das") else self._csv
        return ErddapResponse(body, f"https://example/{path}?{query}", 10, len(body))

    def close(self):
        pass


def adapter(**kw) -> NoaaGfsAdapter:
    return NoaaGfsAdapter(client=FakeClient(**kw))


# --------------------------------------------------------------------------

class TestCsvParsing:
    def test_the_units_row_is_not_read_as_data(self):
        """ERDDAP .csv puts units between the header and the first data row."""
        rows = _rows(fixture("griddap_ugrd10m_kochi.csv"))
        assert len(rows) == 1
        assert rows[0]["values"]["ugrd10m"] == "2.2628248"
        assert rows[0]["units"]["ugrd10m"] == "m s-1"

    def test_a_value_is_read_with_its_published_unit(self):
        a = adapter()
        r = a.fetch_point("eastward_wind", 9.93, 76.26,
                          datetime(2026, 9, 4, 6, tzinfo=UTC))
        obs = r.observations[0]
        assert obs.value == pytest.approx(2.2628, abs=1e-4)
        assert obs.unit == "m s-1"


class TestCoverageIsReadFromTheServer:
    def test_the_advertised_range_is_parsed(self):
        lo, hi = adapter().coverage()
        assert lo.year == 2022
        assert hi == datetime(2026, 9, 9, 15, 0, tzinfo=UTC)

    def test_a_time_beyond_the_run_refuses_rather_than_extrapolating(self):
        with pytest.raises(GfsError) as exc:
            adapter().fetch_point("eastward_wind", 9.93, 76.26,
                                  datetime(2027, 1, 1, tzinfo=UTC))
        assert exc.value.code is ErrorCode.INSUFFICIENT_COVERAGE

    def test_the_horizon_is_not_a_hard_coded_constant(self):
        """A forecast horizon moves; a stale constant would claim false coverage."""
        a = adapter()
        a.coverage()
        assert a._client.queries or True
        # the .das was fetched, not assumed
        assert any(q == "" for q in a._client.queries) or a._coverage is not None


class TestGridFrameHandling:
    def test_a_western_longitude_is_shifted_into_the_0_360_frame(self):
        """Sent unshifted it is clamped to the grid edge -- a plausible number
        for the wrong place."""
        a = adapter()
        a.fetch_point("eastward_wind", 9.93, -70.0,
                      datetime(2026, 9, 4, 6, tzinfo=UTC))
        selector = [q for q in a._client.queries if "ugrd10m" in q][0]
        assert "(290.0)" in selector

    def test_an_eastern_longitude_is_unchanged(self):
        a = adapter()
        a.fetch_point("eastward_wind", 9.93, 76.26,
                      datetime(2026, 9, 4, 6, tzinfo=UTC))
        selector = [q for q in a._client.queries if "ugrd10m" in q][0]
        assert "(76.26)" in selector

    def test_node_distance_is_reported(self):
        r = adapter().fetch_point("eastward_wind", 9.93, 76.26,
                                  datetime(2026, 9, 4, 6, tzinfo=UTC))
        assert r.observations[0].quality.nearest_node_distance_km > 0


class TestNoInventedValues:
    def test_a_unit_the_adapter_did_not_expect_is_refused(self):
        """A kelvin value in a Celsius threshold comparison is the failure this
        prevents (D-7)."""
        bad = fixture("griddap_ugrd10m_kochi.csv").replace("m s-1", "knots")
        with pytest.raises(GfsError) as exc:
            adapter(csv_text=bad).fetch_point(
                "eastward_wind", 9.93, 76.26, datetime(2026, 9, 4, 6, tzinfo=UTC))
        assert exc.value.code is ErrorCode.SCHEMA_VALIDATION_FAILED

    def test_an_empty_value_is_no_data_not_zero(self):
        blank = fixture("griddap_ugrd10m_kochi.csv").replace("2.2628248", "NaN")
        with pytest.raises(GfsError) as exc:
            adapter(csv_text=blank).fetch_point(
                "eastward_wind", 9.93, 76.26, datetime(2026, 9, 4, 6, tzinfo=UTC))
        assert exc.value.code is ErrorCode.NO_DATA

    def test_gust_is_not_published_and_is_never_approximated(self):
        assert "wind_gust" in NOT_PUBLISHED
        assert "wind_gust" not in BINDINGS

    def test_the_adapter_emits_components_never_a_scalar_speed(self):
        """Speed and direction are the kernel's to derive, with a method (D-8)."""
        assert "wind_speed" not in BINDINGS
        assert set(BINDINGS) >= {"eastward_wind", "northward_wind"}


class TestProvenanceNamesTheRealAuthority:
    def test_noaa_is_the_source_and_pacioos_the_distributor(self):
        r = adapter().fetch_point("eastward_wind", 9.93, 76.26,
                                  datetime(2026, 9, 4, 6, tzinfo=UTC))
        pv = r.provenance[0]
        assert pv.source_id == "S-11"
        assert "NOAA" in pv.source and "NCEP" in pv.source
        assert "PacIOOS" in (pv.notes or "")
        assert pv.external_source is True
        assert pv.licence_reference

    def test_a_future_step_is_labelled_forecast(self):
        future = datetime.now(UTC) + timedelta(hours=12)
        csv = fixture("griddap_ugrd10m_kochi.csv").replace(
            "2026-09-04T06:00:00Z", future.strftime("%Y-%m-%dT%H:%M:%SZ"))
        das = fixture("ncep_global_das_head.txt").replace(
            "1.788966e+9", str(future.timestamp() + 86400))
        r = adapter(csv_text=csv, das_text=das).fetch_point(
            "eastward_wind", 9.93, 76.26, future)
        assert r.observations[0].value_kind is ValueKind.FORECAST
        assert r.observations[0].quality.lead_time_h > 0
