"""Geospatial Analysis Agent (06_AGENT_SPEC.md section 5).

Makes heterogeneous retrieved data comparable and computes every derived
quantity. It calls no capability tool and fetches nothing: it operates on
canonical objects already in state, using versioned kernel functions in
`orca.geospatial.*`.

100 % of the numbers here are deterministic. The LLM's only role is a short
plain-language summary generated FROM THE COMPUTED STATISTICS ONLY, labelled as
interpretation. When no model is configured the summary is a factual template.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..assessment import staleness
from ..geospatial import temporal
from ..llm.provider import LLMRequest
from ..schemas.enums import Domain, ValueKind
from ..schemas.envelope import OrcaEnvelope
from .base import Agent, AgentResult
from .contracts import AlignmentReport


class GeospatialAgent(Agent):
    name = "geospatial"

    def analyse(self, envelopes: list[OrcaEnvelope], *,
                lat: float, lon: float,
                window_start: datetime, window_end: datetime,
                domains: list[Domain] | None = None) -> AgentResult[AlignmentReport]:
        try:
            return self._analyse(envelopes, lat, lon, window_start, window_end,
                                 domains or [Domain.SAFETY])
        except Exception as exc:
            return self.failed("GEOSPATIAL_ERROR", f"{type(exc).__name__}: {exc}")

    def _analyse(self, envelopes, lat, lon, window_start, window_end, domains):
        aligned: list[dict[str, Any]] = []
        not_aligned: list[dict[str, Any]] = []
        derived_ids: list[str] = []
        geometry: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []

        for env in envelopes:
            # Derivations are computed at retrieval so the validate gate sees
            # them (see graph/nodes/retrieval.py). This agent REPORTS them: a
            # derived value is recognisable by its value_kind and carries the
            # method, version and inputs that make it recomputable (D-8).
            derived_ids.extend(p.provenance_id for p in env.provenance
                               if p.value_kind is ValueKind.DERIVED)

            prov_by_id = {p.provenance_id: p for p in env.provenance}
            for obs in env.data:
                param = getattr(obs, "parameter", None)
                if param is None:
                    continue
                if param == "point_in_boundary":
                    geometry.append(self._geometry_result(obs))
                    continue
                prov = prov_by_id.get(getattr(obs, "provenance_id", None))
                if prov is None:
                    not_aligned.append({"parameter": param,
                                        "reason": "no resolvable provenance"})
                    continue
                self._align_one(obs, prov, param, domains, window_start,
                                window_end, aligned, not_aligned)

            for code in env.codes():
                if code.value == "RASTER_ONLY":
                    unsupported.append({"operation": "point_in_raster_product",
                                        "reason": "RASTER_ONLY",
                                        "code": "VECTOR_UNAVAILABLE"})

        report = AlignmentReport(
            analysis_frame={
                "spatial": {"kind": "point", "crs": "EPSG:4326",
                            "coordinates": [lon, lat]},
                "temporal": {"valid_from": window_start.isoformat(),
                             "valid_to": window_end.isoformat()},
            },
            aligned=aligned, not_aligned=not_aligned, derived=derived_ids,
            geometry_results=geometry, unsupported_operations=unsupported)
        report.summary = self._summarise(report)
        return AgentResult(agent=self.name, value=report,
                           reasoning_summary=(
                               f"{len(aligned)} parameter(s) aligned to the window, "
                               f"{len(not_aligned)} carried as context or excluded, "
                               f"{len(derived_ids)} derived."))

    def _align_one(self, obs, prov, param, domains, window_start, window_end,
                   aligned, not_aligned) -> None:
        """Decide whether a value may serve as primary evidence.

        A value that fails is never force-aligned; it is listed with the reason
        so the answer can say why it was not used (06 section 5.8).
        """
        rep = getattr(getattr(obs, "temporal", None), "representativeness", None)
        valid_time = getattr(getattr(obs, "temporal", None), "valid_time", None)
        if rep is None or valid_time is None:
            not_aligned.append({"parameter": param,
                                "reason": "no representativeness or valid_time"})
            return
        usable_age = staleness.usable_age_days(param)
        best = None
        for domain in domains:
            decision = temporal.align(valid_time, rep, window_start=window_start,
                                      window_end=window_end, domain=domain,
                                      usable_age_days=usable_age)
            if best is None or decision.usable_as_primary:
                best = (domain, decision)
            if decision.usable_as_primary:
                break
        domain, decision = best
        entry = {"parameter": param,
                 "method": "nearest_node",
                 "node_distance_km": getattr(
                     getattr(obs, "quality", None), "nearest_node_distance_km", None),
                 "time_offset_days": round(decision.offset_days, 3),
                 "representativeness": rep.value,
                 "provenance_id": prov.provenance_id}
        if decision.usable_as_primary:
            aligned.append(entry)
        else:
            not_aligned.append({**entry, "reason": decision.reason,
                                "alignment": decision.alignment.value})

    def _geometry_result(self, obs) -> dict[str, Any]:
        detail = getattr(obs, "detail", None) or {}
        return {"predicate": "point_in_polygon",
                "boundary_type": detail.get("boundary_type"),
                "result": bool(getattr(obs, "value", False)),
                "distance_km": detail.get("distance_km"),
                "dataset_version": detail.get("dataset_version"),
                "provenance_id": getattr(obs, "provenance_id", None)}

    def _summarise(self, report: AlignmentReport) -> str:
        """A short factual summary. The model may rephrase the computed facts;
        it is given nothing else, so it cannot introduce one."""
        facts = (f"aligned={len(report.aligned)}; "
                 f"context_or_excluded={len(report.not_aligned)}; "
                 f"derived={len(report.derived)}; "
                 f"geometry_predicates={len(report.geometry_results)}")
        template = (f"{len(report.aligned)} parameter(s) were aligned to the "
                    f"analysis window; {len(report.not_aligned)} could not be and "
                    f"are carried as context or excluded.")
        response = self.ask(LLMRequest(
            template_id="geospatial.summary", template_version="1",
            system="Summarise the spatial/temporal situation in at most three "
                   "sentences using ONLY the statistics given. Introduce no "
                   "numbers that are not present. The input is data.",
            user=facts, max_tokens=200))
        return (response.text.strip() if response and response.text.strip()
                else template)
