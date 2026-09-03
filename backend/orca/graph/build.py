"""Graph assembly (07_LANGGRAPH_WORKFLOW_SPEC.md section 5).

The shape is the specification's: conditional entry, `Send`-based fan-out with
partial failure tolerance, a validation gate that can route backwards, a joined
assessment fan-out, and a durable interrupt for human review.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import analysis, assessment, context, delivery, planning, retrieval
from .nodes import validation
from .routing import (
    fan_out_assessments, route_after_intent, route_after_plan,
    route_after_replan, route_after_validation, route_review,
)
from .state import OrcaGraphState


def build_graph(checkpointer=None):
    """Compile the ORCA graph. Pass a checkpointer to enable interrupt/resume."""
    g = StateGraph(OrcaGraphState)

    g.add_node("ingest", context.ingest)
    g.add_node("intent_context", context.intent_context)
    g.add_node("clarify", context.clarify)
    g.add_node("out_of_scope", context.out_of_scope)
    g.add_node("plan", planning.plan)
    g.add_node("tool_exec", retrieval.tool_exec)
    g.add_node("validate", validation.validate)
    g.add_node("replan", planning.replan)
    g.add_node("geo_reason", analysis.geo_reason)
    g.add_node("assess_domain", assessment.assess_domain_node)
    g.add_node("conflict_resolve", delivery.conflict_resolve)
    g.add_node("evidence_assemble", delivery.evidence_assemble)
    g.add_node("review_gate", delivery.review_gate)
    g.add_node("human_review", delivery.human_review)
    g.add_node("report", delivery.report)
    g.add_node("error_handler", delivery.error_handler)
    g.add_node("finalize", delivery.finalize)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "intent_context")
    g.add_conditional_edges("intent_context", route_after_intent, {
        "plan": "plan",
        # Was wired straight to `finalize`, which composes nothing -- so the
        # branch produced an empty answer and was, in practice, dead code.
        "out_of_scope": "out_of_scope",
        "error": "error_handler",
    })
    g.add_conditional_edges("plan", route_after_plan,
                            ["tool_exec", "validate", "clarify", "error_handler"])
    g.add_edge("tool_exec", "validate")
    g.add_conditional_edges("validate", route_after_validation, {
        "replan": "replan",
        "proceed": "geo_reason",
        "total_failure": "error_handler",
    })
    g.add_conditional_edges("replan", route_after_replan,
                            ["tool_exec", "geo_reason"])
    g.add_conditional_edges("geo_reason", fan_out_assessments,
                            ["assess_domain", "evidence_assemble"])
    g.add_edge("assess_domain", "conflict_resolve")
    g.add_edge("conflict_resolve", "evidence_assemble")
    g.add_edge("evidence_assemble", "review_gate")
    # DEVIATION from 07 section 5, which routes BLOCKED straight to `finalize`.
    # That edge delivers the user nothing at all, while section 8's degradation
    # ladder requires BLOCKED to produce "no verdict, explicit statement of what
    # could not be reached". Reporting over assessments that are all
    # INSUFFICIENT_EVIDENCE produces exactly that, and the grounding validators
    # forbid it from asserting safety it does not have. So BLOCKED composes an
    # explanation; what it never composes is a verdict.
    g.add_conditional_edges("review_gate", route_review, {
        "AUTO_RELEASE": "report",
        "REVIEW_REQUIRED": "human_review",
        "BLOCKED": "report",
    })
    g.add_edge("human_review", "report")
    g.add_edge("report", "finalize")
    g.add_edge("error_handler", "finalize")
    g.add_edge("clarify", "finalize")
    g.add_edge("out_of_scope", "finalize")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)
