"""Behavioral tests for `interpreter.run()` — the hardcoded 4-node walk
(phase 2, spec AIE-1, plan `260722-0956-day3-interpreter-3node`).

Teeth per `docs/code-standards.md` §4.1: every assertion pins a concrete
node-order/value, not a bare `pytest.raises(NotImplementedError)` — that
form is reserved for the still-unfilled `condition`/`hitl-pause` seams
(`test_executors_behavior.py::test_condition_hitl_still_not_implemented`),
which stays out of this file's scope (scope-fence).
"""

from __future__ import annotations

import json
from pathlib import Path

from studio_contracts import (
    AgentConfig,
    Dag,
    KbBinding,
    Node,
    NodeType,
    Recipe,
    ScorecardThreshold,
    TraceEvent,
)
from studio_engine import interpreter
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "llm_step" / "smoke-01.json"
_TOOL_NAME = "search_docs"


class _NoOpTraceWriter:
    """`TraceWriter` seam is wired-but-unused this phase (populating real
    `TraceEvent`s is Day 5 scope) — a conforming no-op is enough to satisfy
    `run()`'s required keyword param."""

    async def write(self, event: TraceEvent) -> None:
        del event


def _four_node_recipe(*, extra_nodes: list[Node] | None = None) -> Recipe:
    """4-node `kb-retrieve -> llm-step -> tool-call -> end` recipe (shape
    mirrors the Day-2 minimal-recipe pattern, extended to a real 4-node
    `dag.nodes` list). `tool_whitelist` includes `_TOOL_NAME`,
    the same name the `tool-call` node's `params={"tool": ...}` carries —
    must match what `run()` constructs `WhitelistToolDispatch` with,
    otherwise the dispatcher legitimately raises."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    if extra_nodes:
        nodes.extend(extra_nodes)
    return Recipe(
        agent_id="agent-1",
        tenant="ankor",
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[_TOOL_NAME]),
        dag=Dag(nodes=nodes, edges=[]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def _run(recipe: Recipe) -> interpreter.RunResult:
    return await interpreter.run(
        recipe,
        kb_search=EmptyKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )


async def test_run_executes_four_nodes_in_order() -> None:
    """Executed node_id order must be exactly kb->llm->tool->end, sourced
    from `final_state` key insertion order (a `dict` preserves insertion
    order) — a wrong-order or missing-node implementation genuinely FAILS
    this assertion."""
    result = await _run(_four_node_recipe())
    assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_tool", "n_end"]


async def test_run_final_state_has_each_node_output() -> None:
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    result = await _run(_four_node_recipe())
    final_state = result.final_state

    assert final_state["n_kb"] == []
    llm_output = final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["answer"] == fixture["response"]
    assert llm_output["refused"] is True
    assert final_state["n_tool"] == {"tool": _TOOL_NAME, "status": "stub-dispatched"}
    assert final_state["n_end"] == {"terminated": True}


async def test_run_terminates_at_end() -> None:
    """A 5th dangling `condition` node proves the walk genuinely stops at
    `end`: `ConditionExecutor.execute` always raises `NotImplementedError`
    (still-unfilled seam), so an implementation that dynamically iterates
    ALL of `recipe.dag.nodes` instead of this phase's hardcoded 4-type walk
    (forbidden by the plan's risk table, R2) would trip over it and blow up
    here — a vacuous "just don't touch it" implementation can't fake this."""
    dangling = Node(id="n_dangling", type=NodeType.CONDITION, params={})
    result = await _run(_four_node_recipe(extra_nodes=[dangling]))

    assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_tool", "n_end"]
    assert len(result.final_state) == 4
