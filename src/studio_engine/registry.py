"""Node-type → executor-class registry — the ONLY place `NodeType` maps to a
concrete executor. Closed-set enforcement (R-SPEC A2): exactly the 6
`NodeType` values imported from `studio_contracts` — never redefine the
enum here, never add a 7th (cap cứng, CẤM DSL turing-complete). Real
enforcement of "no 7th value" already happens one layer down at the pydantic
`NodeType` enum (P2, `studio_contracts.nodes`); this registry is the second,
engine-side belt: `test_node_type_closed.py::test_registry_has_exactly_six`
locks the registry's key-set to exactly those 6 values so a regression here
(e.g. someone widening the registry with a home-grown extra key) is caught
even if the enum itself were somehow bypassed upstream.
"""

from __future__ import annotations

from studio_contracts import NodeType

from studio_engine.executors import (
    ConditionExecutor,
    EndExecutor,
    HitlPauseExecutor,
    KbRetrieveExecutor,
    LlmStepExecutor,
    NodeExecutor,
    ToolCallExecutor,
)

REGISTRY: dict[NodeType, type[NodeExecutor]] = {
    NodeType.KB_RETRIEVE: KbRetrieveExecutor,
    NodeType.LLM_STEP: LlmStepExecutor,
    NodeType.CONDITION: ConditionExecutor,
    NodeType.TOOL_CALL: ToolCallExecutor,
    NodeType.HITL_PAUSE: HitlPauseExecutor,
    NodeType.END: EndExecutor,
}


def get_executor_class(node_type: NodeType) -> type[NodeExecutor]:
    """Look up the executor class for a closed `NodeType`.

    Raises `KeyError` for anything outside the 6 closed values. This is a
    defense-in-depth reject, not the primary enforcement layer — a
    `node.type` outside the 6 closed values is already unconstructable via
    the pydantic `Node` model (P2), so in practice this only ever sees one
    of the 6 real values; the direct-string-lookup path is what
    `test_unknown_node_type_rejected` exercises.
    """
    return REGISTRY[node_type]
