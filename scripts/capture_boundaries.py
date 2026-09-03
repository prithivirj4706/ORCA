"""Capture a versioned MarineRegions boundary snapshot.

    python -m scripts.capture_boundaries
    python -m scripts.capture_boundaries --region 0,64,26,96 --version 2026-09-02

Reads config/boundaries.yaml for the source, the region and which layers serve
which boundary type; writes data/boundaries/<version>/ with a manifest and one
flattened geometry file per layer.

The snapshot records the layer version the service published at capture time.
If the service stops publishing a parseable version the capture FAILS rather
than writing geometry that cannot be cited (11 section 13, "version binding").
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from orca.adapters.marineregions.bindings import load_policy            # noqa: E402
from orca.adapters.marineregions.client import (                        # noqa: E402
    ACCESS_METHOD, ATTRIBUTION, MarineRegionsWfs, ORGANISATION, SOURCE_ID,
    SOURCE_NAME, parse_layer_version,
)
from orca.adapters.marineregions.store import (                         # noqa: E402
    SNAPSHOT_FORMAT, write_layer, write_manifest,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _layer_metadata(caps: str, layer: str) -> tuple[str, str]:
    """(title, abstract) for a layer, from the GetCapabilities document."""
    i = caps.find(f"<Name>{layer}</Name>")
    if i < 0:
        raise SystemExit(f"layer {layer!r} is not advertised by the service")
    seg = caps[i:i + 4000]
    title = re.search(r"<Title>(.*?)</Title>", seg, re.S)
    abstract = re.search(r"<Abstract>(.*?)</Abstract>", seg, re.S)
    return ((title.group(1).strip() if title else ""),
            (abstract.group(1).strip() if abstract else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="capture_boundaries")
    ap.add_argument("--config", default=None, help="path to boundaries.yaml")
    ap.add_argument("--region", default=None,
                    help="min_lat,min_lon,max_lat,max_lon (default: from config)")
    ap.add_argument("--version", default=None,
                    help="snapshot directory name (default: today, UTC)")
    ap.add_argument("--out", default=None, help="output root (default: from config)")
    a = ap.parse_args(argv)

    policy = load_policy(a.config)
    region = dict(policy.region)
    if a.region:
        parts = [float(x) for x in a.region.split(",")]
        if len(parts) != 4:
            raise SystemExit("--region needs min_lat,min_lon,max_lat,max_lon")
        region = dict(zip(("min_lat", "min_lon", "max_lat", "max_lon"), parts))

    now = datetime.now(timezone.utc)
    version = a.version or now.strftime("%Y-%m-%d")
    out_root = pathlib.Path(a.out or (ROOT / policy.snapshot_dir))
    directory = out_root / version

    print(f"MarineRegions boundary capture -> {directory}")
    print(f"  endpoint {policy.endpoint}")
    print(f"  region   lat {region['min_lat']}..{region['max_lat']}  "
          f"lon {region['min_lon']}..{region['max_lon']}")

    layers_manifest = []
    with MarineRegionsWfs(policy.endpoint) as wfs:
        caps = wfs.capabilities()
        print(f"  capabilities {len(caps):,} bytes")
        for name in policy.configured_types:
            layer = policy.layer_for(name)
            title, abstract = _layer_metadata(caps, layer)
            dataset_version, year = parse_layer_version(title)
            if not dataset_version or not year:
                raise SystemExit(
                    f"{layer}: the service no longer publishes a parseable version "
                    f"in its title ({title!r}); refusing to write an unversioned "
                    f"snapshot")
            collection, url = wfs.features(layer, **region)
            meta = write_layer(directory, layer, name, collection, title=title,
                               dataset_version=dataset_version,
                               effective_year=year, abstract=abstract,
                               request_url=url)
            layers_manifest.append(meta)
            print(f"  {name:22} {layer:34} {dataset_version} "
                  f"{meta['feature_count']:>3} features "
                  f"{meta['vertex_count']:>9,} vertices")
        bytes_read, requests = wfs.bytes_read, wfs.requests

    manifest = {
        "format": SNAPSHOT_FORMAT,
        "snapshot_version": version,
        "captured_at": now.isoformat(),
        "region": region,
        "region_note": policy.region_note,
        "source": {
            "source_id": SOURCE_ID, "name": SOURCE_NAME,
            "organisation": ORGANISATION, "endpoint": policy.endpoint,
            "access_method": ACCESS_METHOD, "licence_reference": ATTRIBUTION,
            "authentication": "none required (verified unauthenticated)",
            "bytes_read": bytes_read, "requests": requests,
        },
        "unconfigured_boundary_types": {
            t: policy.types[t].reason for t in policy.unconfigured_types},
        "layers": layers_manifest,
    }
    path = write_manifest(directory, manifest)
    print(f"  manifest {path}  ({len(json.dumps(manifest)):,} bytes, "
          f"{bytes_read:,} bytes read in {requests} requests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
