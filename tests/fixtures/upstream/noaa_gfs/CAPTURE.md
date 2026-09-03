# Recorded upstream fixtures — NOAA NCEP GFS (S-11)

Captured live on **2026-09-03** from
`https://pae-paha.pacioos.hawaii.edu/erddap/griddap/ncep_global`,
unauthenticated, HTTP 200.

| File | Request |
|---|---|
| `ncep_global_das_head.txt` | `.das` — the `time`, `latitude`, `longitude` and leading variable blocks |
| `griddap_ugrd10m_kochi.csv` | `.csv?ugrd10m[(2026-09-04T06:00:00Z)][(9.93)][(76.26)]` |
| `griddap_out_of_range.txt` | the same selector at `2027-01-01`, beyond the model run |

**Why this host.** `03_DATA_SOURCE_MATRIX.md` named "NOAA" for S-11 without an
endpoint, and the obvious ones are gone: `nomads.ncep.noaa.gov/dods` returns an
HTML retirement notice under HTTP 200 (NWS Service Change Notice 25-81), and both
`coastwatch.pfeg.noaa.gov` and `upwell.pfeg.noaa.gov` time out. The surviving
NOMADS GRIB filter needs an eccodes binary to decode. PacIOOS republishes the
NCEP GFS run over ERDDAP griddap, which ORCA already reads.

The originating authority is **NOAA NCEP**; PacIOOS is the **distributor**.
Provenance records both and never presents the redistributor as the authority.

**Two things the fixtures pin.** ERDDAP `.csv` puts a UNITS row between the
header and the data, so a naive reader takes `"m s-1"` as a value. And the grid
is published on longitude 0..360 with latitude *decreasing*: a western longitude
sent unshifted is clamped to the grid edge and returns a plausible number for
the wrong place.

**Out of range is a refusal, not an empty answer.** The server replies
`Your query produced no matching results` when the requested time is past the
end of the run. The adapter checks the advertised range first and raises
`INSUFFICIENT_COVERAGE`, which says why. A forecast horizon MOVES, so the range
is read from the server on each run rather than held as a constant.
