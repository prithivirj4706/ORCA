"""Geofencing proximity alerts (problem statement, capability 8).

"Providing geofencing-based notifications when approaching international
maritime boundaries, restricted waters, marine protected areas..."

No new data is fetched. Every boundary run already computes, per boundary type,
whether the position is inside and how far the nearest edge is, so an alert is a
policy over numbers ORCA already has.

Two rules keep this honest:

  * An alert is only raised for a boundary type that was actually EVALUATED.
    A type with no source (`DATASET_UNAVAILABLE`) produces no alert and no
    reassurance -- silence about a restricted zone we never checked would read
    as "you are clear" (D-3).
  * Crossing an international boundary is a REGULATORY event, not a safety one.
    The message says what the geometry shows and names the dataset version; it
    never tells anyone they are permitted to be somewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Distance at which an approach is worth mentioning, per boundary type.
#: A fisher under way covers 5 km in well under an hour, so these are minutes
#: of warning, not academic margins. Engineering parameters, unvalidated.
APPROACH_KM: dict[str, float] = {
    "EEZ": 10.0,
    "territorial_sea": 5.0,
    "contiguous_zone": 5.0,
    "internal_waters": 3.0,
    "marine_protected_area": 5.0,
    "restricted_zone": 10.0,
    "fishing_regulation_zone": 5.0,
    "seasonal_closure": 5.0,
}

#: Types where being INSIDE is itself the notifiable event.
CONSTRAINING = frozenset({"marine_protected_area", "restricted_zone",
                          "fishing_regulation_zone", "seasonal_closure"})


@dataclass(slots=True)
class GeofenceAlert:
    kind: str                      # approaching | inside | leaving
    boundary_type: str
    severity: str                  # info | caution | warning
    distance_km: float | None
    inside: bool
    name: str | None = None
    dataset_version: str | None = None
    message_key: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "boundary_type": self.boundary_type,
                "severity": self.severity, "distance_km": self.distance_km,
                "inside": self.inside, "name": self.name,
                "dataset_version": self.dataset_version,
                "message_key": self.message_key,
                "advisory_only": True, **self.detail}


def geofence_alerts(boundary_env) -> list[GeofenceAlert]:
    """Alerts from a `get_maritime_boundaries` envelope. Never fetches."""
    if boundary_env is None:
        return []
    alerts: list[GeofenceAlert] = []
    for obj in getattr(boundary_env, "data", []) or []:
        if getattr(obj, "parameter", None) != "point_in_boundary":
            continue
        d = getattr(obj, "detail", None) or {}
        btype = d.get("boundary_type")
        if not btype:
            continue
        inside = bool(getattr(obj, "value", False))
        distance = d.get("distance_km")
        version = d.get("dataset_version")
        features = d.get("features") or []
        name = (str(features[0].get("name")) if features
                else (d.get("nearest") or {}).get("name"))

        if inside and btype in CONSTRAINING:
            alerts.append(GeofenceAlert(
                "inside", btype, "warning", distance, True, name, version,
                "geofence.inside_constraining"))
            continue

        threshold = APPROACH_KM.get(btype, 5.0)
        if distance is not None and distance <= threshold:
            # Approaching from either side: leaving a zone you are in, or
            # nearing one you are not. Both are worth saying.
            kind = "leaving" if inside else "approaching"
            severity = "warning" if btype in CONSTRAINING else "caution"
            alerts.append(GeofenceAlert(
                kind, btype, severity, distance, inside, name, version,
                f"geofence.{kind}"))

    # Most urgent first: warnings before cautions, then nearest.
    order = {"warning": 0, "caution": 1, "info": 2}
    alerts.sort(key=lambda a: (order.get(a.severity, 3),
                               a.distance_km if a.distance_km is not None else 1e9))
    return alerts
