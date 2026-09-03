"""Geofencing proximity alerts (problem statement, capability 8)."""
import pytest

from backend.orca.assessment.geofence import (
    APPROACH_KM, CONSTRAINING, geofence_alerts,
)
from backend.orca.schemas.core import Provenance, SpatialRef, TemporalRef, utcnow
from backend.orca.schemas.data import DerivedResult
from backend.orca.geospatial.methods import derivation
from backend.orca.schemas.enums import EnvelopeStatus, ValueKind
from backend.orca.schemas.envelope import OrcaEnvelope


def boundary_env(*entries):
    """entries: (boundary_type, inside, distance_km, name)"""
    data, prov = [], []
    for i, (btype, inside, dist, name) in enumerate(entries):
        # The envelope enforces a provenance join, so every id must resolve.
        prov.append(Provenance(provenance_id=f"pv-{i}",
                               parameter="point_in_boundary",
                               value_kind=ValueKind.DERIVED,
                               derivation=derivation("point_in_polygon", [f"layer:{btype}"]),
                               source="TEST", source_id="S-TEST"))
        data.append(DerivedResult(
            parameter="point_in_boundary", value=inside,
            spatial=SpatialRef.point(9.9, 76.2),
            temporal=TemporalRef(valid_time=utcnow()),
            provenance_id=f"pv-{i}",
            detail={"boundary_type": btype, "distance_km": dist,
                    "dataset_version": "v12",
                    "features": [{"name": name}] if inside and name else [],
                    "nearest": {"name": name} if name else None}))
    return OrcaEnvelope(status=EnvelopeStatus.SUCCESS,
                        tool="get_maritime_boundaries", data=data, provenance=prov)


class TestApproach:
    def test_approaching_an_eez_raises_an_alert(self):
        a = geofence_alerts(boundary_env(("EEZ", False, 4.0, "Indian EEZ")))
        assert len(a) == 1
        assert a[0].kind == "approaching"
        assert a[0].boundary_type == "EEZ"

    def test_far_from_every_boundary_raises_nothing(self):
        assert geofence_alerts(boundary_env(("EEZ", False, 400.0, "Indian EEZ"))) == []

    def test_leaving_is_distinguished_from_approaching(self):
        a = geofence_alerts(boundary_env(("EEZ", True, 2.0, "Indian EEZ")))
        assert a[0].kind == "leaving"
        assert a[0].inside is True


class TestConstrainingZones:
    def test_being_inside_a_restricted_zone_is_itself_the_event(self):
        a = geofence_alerts(boundary_env(("restricted_zone", True, 30.0, "Naval area")))
        assert a[0].kind == "inside"
        assert a[0].severity == "warning"

    def test_a_constraining_zone_outranks_a_plain_boundary(self):
        a = geofence_alerts(boundary_env(
            ("EEZ", False, 1.0, "Indian EEZ"),
            ("marine_protected_area", True, 20.0, "MPA")))
        assert a[0].boundary_type == "marine_protected_area"   # warning first


class TestSilenceIsNeverReassurance:
    def test_an_unevaluated_boundary_type_produces_no_alert(self):
        """A type with no source must not produce quiet reassurance (D-3)."""
        assert geofence_alerts(boundary_env(("restricted_zone", False, None, None))) == []

    def test_no_envelope_produces_no_alerts(self):
        assert geofence_alerts(None) == []


class TestAlertsAreAdvisoryOnly:
    def test_every_alert_is_marked_advisory_only(self):
        a = geofence_alerts(boundary_env(("EEZ", False, 3.0, "Indian EEZ")))
        assert a[0].as_dict()["advisory_only"] is True

    def test_the_dataset_version_is_carried(self):
        a = geofence_alerts(boundary_env(("EEZ", False, 3.0, "Indian EEZ")))
        assert a[0].as_dict()["dataset_version"] == "v12"

    def test_every_constraining_type_has_a_threshold(self):
        for t in CONSTRAINING:
            assert t in APPROACH_KM
