"""Gridded map fields.

The property that matters: a masked cell reaches the client as null and is
counted as missing. Drawn as zero it would paint a calm, empty sea over data
that was never there (F-10 and D-3, in pixels).
"""
from datetime import datetime, timezone

import numpy as np
import pytest

from backend.orca.tools.fields import FIELDS, FieldError, get_field

UTC = timezone.utc
WHEN = datetime(2026, 9, 3, 6, tzinfo=UTC)


class FakeBinding:
    dataset_id = "test_ds"
    canonical_unit = "mg m-3"


class FakeCmems:
    """A 3x3 field with a hole in the middle, like a cloud or a land mask."""

    def fetch_grid(self, parameter, lat, lon, valid_time, radius_km=200.0):
        block = np.array([[1.0, 2.0, 3.0],
                          [4.0, np.nan, 6.0],
                          [7.0, 8.0, 9.0]])
        return [9.0, 9.5, 10.0], [76.0, 76.5, 77.0], block, FakeBinding(), WHEN


class FakeGfs:
    def fetch_grid(self, parameters, lat, lon, valid_time, radius_km=300.0):
        u = np.array([[3.0, np.nan], [0.0, 4.0]])
        v = np.array([[4.0, np.nan], [0.0, 3.0]])
        return ([9.0, 9.5], [76.0, 76.5],
                {"eastward_wind": u, "northward_wind": v}, WHEN)


class TestHolesStayHoles:
    def test_a_masked_cell_is_null_not_zero(self):
        f = get_field("chlorophyll", 9.5, 76.5, WHEN, cmems=FakeCmems())
        assert f["values"][1][1] is None
        assert 0.0 not in [v for row in f["values"] for v in row if v is not None]

    def test_coverage_reports_what_fraction_is_real(self):
        f = get_field("chlorophyll", 9.5, 76.5, WHEN, cmems=FakeCmems())
        assert f["cells"] == {"total": 9, "valid": 8, "coverage": round(8 / 9, 3)}

    def test_range_ignores_the_holes(self):
        f = get_field("chlorophyll", 9.5, 76.5, WHEN, cmems=FakeCmems())
        assert f["range"] == {"min": 1.0, "max": 9.0}

    def test_a_field_with_no_valid_cells_refuses(self):
        class Empty(FakeCmems):
            def fetch_grid(self, *a, **k):
                return ([9.0], [76.0], np.array([[np.nan]]), FakeBinding(), WHEN)

        with pytest.raises(FieldError) as exc:
            get_field("chlorophyll", 9.5, 76.5, WHEN, cmems=Empty())
        assert exc.value.code == "NO_DATA"


class TestVectorFields:
    def test_wind_returns_components_and_speed(self):
        f = get_field("wind", 9.5, 76.5, WHEN, gfs=FakeGfs())
        assert f["kind"] == "vector"
        assert f["u"][0][0] == 3.0 and f["v"][0][0] == 4.0
        assert f["speed"][0][0] == 5.0        # 3-4-5

    def test_a_masked_vector_cell_is_null_in_every_component(self):
        f = get_field("wind", 9.5, 76.5, WHEN, gfs=FakeGfs())
        assert f["u"][0][1] is None
        assert f["v"][0][1] is None
        assert f["speed"][0][1] is None


class TestContract:
    def test_an_unknown_field_names_what_exists(self):
        with pytest.raises(FieldError) as exc:
            get_field("unicorns", 9.5, 76.5, WHEN)
        assert "chlorophyll" in exc.value.detail

    def test_a_field_without_its_adapter_refuses(self):
        with pytest.raises(FieldError) as exc:
            get_field("wind", 9.5, 76.5, WHEN, gfs=None)
        assert exc.value.code == "DATASET_UNAVAILABLE"

    @pytest.mark.parametrize("name", sorted(FIELDS))
    def test_every_field_declares_kind_unit_and_label(self, name):
        spec = FIELDS[name]
        assert spec["kind"] in ("scalar", "vector")
        assert spec["unit"] and spec["label"]

    def test_fields_are_marked_advisory_only(self):
        f = get_field("chlorophyll", 9.5, 76.5, WHEN, cmems=FakeCmems())
        assert f["advisory_only"] is True
        assert f["source_id"] and f["valid_time"]
