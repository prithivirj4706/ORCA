"""Canonical parameter -> GFS dataset/variable bindings.

Bindings are DATA, not logic. Read from the dataset's own `.das` on 2026-09-03,
not guessed.

GFS publishes wind as COMPONENTS. ORCA does not add a scalar speed here: the
geospatial kernel derives speed and direction with a recorded method and input
provenance (D-8), exactly as it does for CMEMS.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...schemas.enums import Representativeness as R

#: NCEP GFS atmospheric model, global 0.5 degree, hourly forecast steps.
GFS_DATASET = "ncep_global"


@dataclass(frozen=True, slots=True)
class GfsBinding:
    parameter: str            # canonical ORCA parameter name
    dataset_id: str
    variable: str             # variable name as published by the dataset
    representativeness: R
    canonical_unit: str
    note: str | None = None


#: GFS is an INSTANTANEOUS forecast field at each hourly step -- not a mean --
#: so it may serve a short analysis window, which is precisely why it unblocks
#: a wind forecast that the CMEMS observation product cannot (F-11, F-25).
BINDINGS: dict[str, list[GfsBinding]] = {
    "eastward_wind": [
        GfsBinding("eastward_wind", GFS_DATASET, "ugrd10m", R.INSTANTANEOUS,
                   "m s-1", note="10 m eastward wind component"),
    ],
    "northward_wind": [
        GfsBinding("northward_wind", GFS_DATASET, "vgrd10m", R.INSTANTANEOUS,
                   "m s-1", note="10 m northward wind component"),
    ],
    "air_temperature": [
        GfsBinding("air_temperature", GFS_DATASET, "tmp2m", R.INSTANTANEOUS,
                   "K", note="2 m air temperature; published in kelvin"),
    ],
    "air_pressure": [
        GfsBinding("air_pressure", GFS_DATASET, "prmslmsl", R.INSTANTANEOUS,
                   "Pa", note="mean sea level pressure"),
    ],
}

#: GFS publishes no gust field in this dataset. `wind_gust` therefore stays
#: unavailable and is reported as not evaluated -- it is never approximated
#: from the mean wind, which would be an invented number.
NOT_PUBLISHED = ("wind_gust",)
