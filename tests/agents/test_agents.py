"""Agent contract behaviour (06_AGENT_SPEC.md sections 2, 4, 6, 7, 10)."""
import pytest

from backend.orca.agents.contracts import Plan, PlanStep
from backend.orca.agents.discovery import DiscoveryAgent, classify_outcome
from backend.orca.agents.reporting import ReportingAgent
from backend.orca.agents.risk import RiskAgent
from backend.orca.agents.validators import validate_narrative
from backend.orca.llm.provider import LLMResponse, Usage
from backend.orca.schemas.assessment import Assessment, Driver, Evidence
from backend.orca.schemas.enums import (
    Confidence, Domain, EnvelopeStatus, ValueKind, Verdict,
)
from backend.orca.schemas.envelope import OrcaEnvelope
from backend.orca.schemas.errors import ErrorCode
from backend.orca.tools.registry import ToolRegistry


class FakeProvider:
    """A model that answers with whatever it was told to, including nonsense."""

    name = "fake"
    model = "fake-1"
    available = True

    def __init__(self, text="", parsed=None):
        self._text, self._parsed = text, parsed
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        return LLMResponse(text=self._text, model=self.model, provider=self.name,
                           template_id=request.template_id,
                           template_version=request.template_version,
                           usage=Usage(10, 10), parsed=self._parsed)


# --------------------------------------------------------------------------
# Discovery: a tool that dies must not take the run with it.
# --------------------------------------------------------------------------

class TestDiscoveryFailureHandling:
    def test_a_raising_tool_becomes_a_structured_failure(self):
        r = ToolRegistry()
        r.bind("get_sst", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        step = PlanStep(step_id="s1", tool="get_sst",
                        args={"lat": 9.9, "lon": 76.2, "valid_time": "x"})
        env, result, mod = DiscoveryAgent().execute_step(step, r)
        assert result.outcome == "failed"
        assert env is None and mod is None

    def test_auth_required_is_never_widened(self):
        """A credential problem is not fixed by asking a different question."""
        calls = []

        def tool(**kw):
            calls.append(kw)
            return OrcaEnvelope.empty("get_pfz", ErrorCode.AUTH_REQUIRED,
                                      "credentials required", "pfz")

        r = ToolRegistry()
        r.bind("get_pfz", tool)
        step = PlanStep(step_id="s1", tool="get_pfz", necessity="required",
                        args={"lat": 9.9, "lon": 76.2, "valid_time": "x"})
        DiscoveryAgent().execute_step(step, r)
        assert len(calls) == 1, "AUTH_REQUIRED must not provoke a second request"

    def test_a_warning_lookup_may_never_widen_its_area(self):
        """A warning for a different area is a different warning."""
        calls = []

        def tool(**kw):
            calls.append(kw)
            return OrcaEnvelope.empty("get_marine_warnings", ErrorCode.NO_DATA,
                                      "nothing", "marine_warning")

        r = ToolRegistry()
        r.bind("get_marine_warnings", tool)
        step = PlanStep(step_id="s1", tool="get_marine_warnings",
                        necessity="required",
                        args={"lat": 9.9, "lon": 76.2, "valid_time": "x"})
        DiscoveryAgent().execute_step(step, r)
        assert len(calls) == 1

    def test_empty_is_a_result_not_a_failure(self):
        env = OrcaEnvelope.empty("get_marine_warnings",
                                 ErrorCode.NO_ACTIVE_WARNING, "none", "w")
        assert classify_outcome(env) == "empty"


# --------------------------------------------------------------------------
# Risk: the model may phrase a verdict; it may never change one.
# --------------------------------------------------------------------------

def _assessment():
    return Assessment(
        assessment_id="as-1", domain=Domain.SAFETY, verdict=Verdict.MARGINAL,
        confidence=Confidence.MEDIUM,
        drivers=[Driver(factor="significant_wave_height", value=2.4, unit="m",
                        band="marginal", contribution="limiting")],
        rationale="MARGINAL for SAFETY; the limiting factor is wave height.")


class TestRiskLLMCannotAlterVerdict:
    def test_rationale_inventing_a_number_is_discarded(self):
        from backend.orca.assessment.engine import DomainResult

        agent = RiskAgent(llm=FakeProvider(text="Seas are running to 9.9 metres."))
        original = _assessment()
        result = agent._with_rationale(DomainResult(assessment=original, evidence=[]))
        assert result.assessment.rationale == original.rationale

    def test_rationale_using_official_language_is_discarded(self):
        from backend.orca.assessment.engine import DomainResult

        agent = RiskAgent(llm=FakeProvider(text="An official warning is in force."))
        original = _assessment()
        result = agent._with_rationale(DomainResult(assessment=original, evidence=[]))
        assert result.assessment.rationale == original.rationale

    def test_a_clean_rationale_is_accepted_but_the_verdict_is_untouched(self):
        from backend.orca.assessment.engine import DomainResult

        agent = RiskAgent(llm=FakeProvider(
            text="Sea state is marginal for small craft; wave height is the "
                 "limiting factor."))
        original = _assessment()
        result = agent._with_rationale(DomainResult(assessment=original, evidence=[]))
        assert result.assessment.rationale != original.rationale
        assert result.assessment.verdict is Verdict.MARGINAL


# --------------------------------------------------------------------------
# Reporting: grounded by construction.
# --------------------------------------------------------------------------

def _evidence(domain=Domain.SAFETY):
    return [Evidence(evidence_id="ev-1", domain=domain,
                     statement="Significant wave height 2.4 m",
                     parameter="significant_wave_height", value=2.4, unit="m",
                     value_kind=ValueKind.FORECAST, provenance_id="pv-1",
                     weight="primary")]


class TestReportingGrounding:
    def test_narrative_that_drifts_numerically_falls_back_to_template(self):
        agent = ReportingAgent(llm=FakeProvider(text="Waves are 7.7 m today."))
        rec = agent.report(assessments=[_assessment()],
                           evidence=_evidence()).value
        assert "7.7" not in rec.narrative
        assert "not an official advisory" in rec.narrative

    def test_every_claim_is_bound_to_evidence(self):
        rec = ReportingAgent().report(assessments=[_assessment()],
                                      evidence=_evidence()).value
        assert rec.claims
        for claim in rec.claims:
            assert claim.evidence_ids

    def test_no_claim_is_emitted_for_a_domain_with_no_evidence(self):
        rec = ReportingAgent().report(assessments=[_assessment()],
                                      evidence=[]).value
        assert rec.claims == []

    def test_recommendation_is_never_an_official_advisory(self):
        rec = ReportingAgent().report(assessments=[_assessment()],
                                      evidence=_evidence()).value
        assert rec.is_official_advisory is False


class TestAbsenceIsNotSafety:
    def test_safety_claim_rejected_when_safety_was_not_assessed(self):
        issues = validate_narrative("It is safe to go out.", evidence_values=[],
                                    safety_assessed=False)
        assert any(i.code == "ABSENCE_AS_SAFETY" for i in issues)


class TestNoModelConfigured:
    def test_agents_degrade_to_the_deterministic_path(self):
        agent = ReportingAgent()
        assert agent.use_llm() is False
        rec = agent.report(assessments=[_assessment()], evidence=_evidence()).value
        assert rec.narrative
        assert "deterministic template" in rec.reasoning_summary


class TestCappedVerdictKeepsTheAbsenceGuardArmed:
    """O-1: a capped verdict is a ceiling, not a clean bill of health."""

    def _capped(self):
        return Assessment(
            assessment_id="as-2", domain=Domain.SAFETY, verdict=Verdict.MARGINAL,
            confidence=Confidence.MEDIUM,
            verdict_capped_by=["official_warning_status"],
            drivers=[Driver(factor="significant_wave_height", value=0.4, unit="m",
                            band="favourable", contribution="supporting")],
            rationale="MARGINAL for SAFETY, capped.")

    def test_narrative_may_not_claim_safety_when_the_verdict_was_capped(self):
        agent = ReportingAgent(llm=FakeProvider(text="It is safe to go out today."))
        rec = agent.report(assessments=[self._capped()],
                           evidence=_evidence()).value
        assert "safe to go out" not in rec.narrative
        assert "deterministic template" in rec.reasoning_summary

    def test_the_template_states_why_the_verdict_is_a_ceiling(self):
        rec = ReportingAgent().report(assessments=[self._capped()],
                                      evidence=_evidence()).value
        assert "capped" in rec.narrative
        assert "official warning status" in rec.narrative
