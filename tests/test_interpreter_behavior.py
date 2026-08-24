"""Behavioral tests for `interpreter.run()` — the hardcoded 4-node walk
(phase 2, spec AIE-1, plan `260722-0956-day3-interpreter-3node`).

Teeth per `docs/code-standards.md` §4.1: every assertion pins a concrete
node-order/value, not a bare presence check.

Scope-fence: this file's recipes stay `kb-retrieve -> llm-step -> tool-call
-> end` — no node here ever dispatches a `condition`/`hitl-pause` node
through `run()`. Both executors got real bodies in
`260806-0938-d14-aie1-node-executors-grid-prep` (D14 P1), and `condition`'s
`interpreter.run()` wiring (`state`/`when` injection) is proven separately in
`test_condition_dag_e2e.py`, which stays out of this file's scope.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from studio_contracts import (
    AgentConfig,
    Dag,
    Edge,
    KbBinding,
    Node,
    NodeType,
    Recipe,
    ScorecardThreshold,
    TraceEvent,
)
from studio_engine import interpreter
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM
from test_session_context_tenant_wall import default_session_context

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "llm_step" / "smoke-01.json"
_TOOL_NAME = "search_docs"
# Team-wide canonical UUID for tenant "ankor" — same value as
# packages/workbench/tests/test_wiring_d4.py:14 and
# apps/studio/tests/test_trace_writer.py:14.
ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")


class _NoOpTraceWriter:
    """`TraceWriter` seam is wired-but-unused this phase (populating real
    `TraceEvent`s is Day 5 scope) — a conforming no-op is enough to satisfy
    `run()`'s required keyword param."""

    async def write(self, event: TraceEvent) -> None:
        del event


def _four_node_recipe(*, extra_nodes: list[Node] | None = None, extra_edges: list[Edge] | None = None) -> Recipe:
    """4-node `kb-retrieve -> llm-step -> tool-call -> end` recipe (shape
    mirrors the Day-2 minimal-recipe pattern, extended to a real 4-node
    `dag.nodes` list). `tool_whitelist` includes `_TOOL_NAME`,
    the same name the `tool-call` node's `params={"tool": ...}` carries —
    must match what `run()` constructs `WhitelistToolDispatch` with,
    otherwise the dispatcher legitimately raises. `dag.edges` chains the 4
    nodes linearly (Day 6: `run()` walks by edge, not by a hardcoded
    `NodeType` order) — `extra_edges` lets a caller attach `extra_nodes`
    without becoming a second ambiguous start candidate."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    if extra_nodes:
        nodes.extend(extra_nodes)
    edges = [
        Edge(from_="n_kb", to="n_llm"),
        Edge(from_="n_llm", to="n_tool"),
        Edge(from_="n_tool", to="n_end"),
    ]
    if extra_edges:
        edges.extend(extra_edges)
    return Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(system_prompt="x", model="m", tool_whitelist=[_TOOL_NAME]),
        dag=Dag(nodes=nodes, edges=edges),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def _run(recipe: Recipe) -> interpreter.RunResult:
    return await interpreter.run(
        recipe,
        session_context=default_session_context(),
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
    # `EmptyKbSearch` grounded nothing, so `FixtureLLM`'s `[chunk-001]` bracket
    # is ungrounded and dropped — `citations` is empty and the run is therefore
    # a refusal (`refused = not citations`, Day 6). A fabricated answer cannot
    # buy its way out of the refusal branch by bracketing an id it was never
    # given; see `tests/test_refusal_from_grounding.py` for why the two earlier
    # signals (`not retrieved_chunks`, `[[REFUSED]]`) both mis-scored the real
    # golden set.
    assert llm_output["citations"] == []
    assert llm_output["refused"] is True
    assert final_state["n_tool"] == {"tool": _TOOL_NAME, "status": "stub-dispatched"}
    assert final_state["n_end"] == {"terminated": True}


async def test_run_terminates_at_end() -> None:
    """A 5th `condition` node, self-looped (its only edge points to itself,
    so it is never a `_find_start_node_id` candidate and nothing in the
    real `n_kb->n_llm->n_tool->n_end` chain ever edges into it), proves the
    walk stops exactly at `end` without ever reaching an unrelated node: the
    dangling node's own edge only ever points back to itself, so the ONLY
    way `run()` would ever dispatch it is by iterating `recipe.dag.nodes`
    directly (ignoring `dag.edges` reachability) instead of walking
    edge-by-edge from the real start node. `final_state` pins the exact key
    set — a `dict` growing a stray `"n_dangling"` entry fails this
    assertion just as loudly as `ConditionExecutor.execute` raising ever
    did pre-D14 (`ConditionExecutor` is no longer a `NotImplementedError`
    stub, `260806-0938-d14-aie1-node-executors-grid-prep` P1, so this test
    no longer relies on that raise to catch a wrong-order walk)."""
    dangling = Node(id="n_dangling", type=NodeType.CONDITION, params={})
    self_loop = Edge(from_="n_dangling", to="n_dangling")
    result = await _run(_four_node_recipe(extra_nodes=[dangling], extra_edges=[self_loop]))

    assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_tool", "n_end"]
    assert len(result.final_state) == 4


# --------------------------------------------------------------------------
# engine#35 review fixup (@dholmes0207): `tool_dispatch` injection had 0 test
# coverage at the `run()` level — both `ToolCallExecutor(dispatcher)` tests
# in `test_executors_behavior.py` bypass `run()` entirely. These 2 tests
# close that gap and lock the 2 review findings.
# --------------------------------------------------------------------------


class _FalsyButRealDispatch:
    """`__len__` makes `bool(this) is False` — the exact shape the review
    flagged: a table-backed dispatcher (e.g. a `TOOL_REGISTRY` dict-like
    object) is a realistic way to end up falsy without being invalid."""

    def __len__(self) -> int:
        return 0

    async def dispatch(self, tool: str, params: dict[str, object]) -> object:
        del params
        return {"tool": tool, "status": "real-dispatched"}


async def test_run_uses_injected_dispatcher_even_when_falsy() -> None:
    """Locks the `or` -> `is not None` fix: a falsy-but-valid injected
    dispatcher must still be used for real, never silently swapped for the
    `WhitelistToolDispatch` fallback (`tool_dispatch or WhitelistToolDispatch(...)`
    picked the fallback here pre-fix, because `bool(_FalsyButRealDispatch()) is
    False`)."""
    result = await interpreter.run(
        _four_node_recipe(),
        session_context=default_session_context(),
        kb_search=EmptyKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
        tool_dispatch=_FalsyButRealDispatch(),
    )
    assert result.final_state["n_tool"] == {"tool": _TOOL_NAME, "status": "real-dispatched"}


class _PermissiveNoWhitelistDispatch:
    """No whitelist check of its own — the exact caller class the review's
    `shell_exec`/`rm -rf /` scenario used to demonstrate belt 2 silently
    depending on caller convention once a dispatcher is injected."""

    def __init__(self) -> None:
        self.called = False

    async def dispatch(self, tool: str, params: dict[str, object]) -> object:
        self.called = True
        return {"tool": tool, "DA_CHAY_THAT": True, "params": params}


async def test_run_enforces_whitelist_on_injected_dispatcher_regardless_of_its_own_checks() -> None:
    """Locks the `WhitelistGuardedDispatch` fix (option a): `run()` itself
    must reject a tool outside `agent_config.tool_whitelist` even when the
    injected dispatcher performs no whitelist check of its own — belt 2
    (R-SPEC A2) holds structurally, not by caller convention. The permissive
    dispatcher's `dispatch()` must never even be reached."""
    recipe = _four_node_recipe()
    tool_node = next(n for n in recipe.dag.nodes if n.id == "n_tool")
    tool_node.params["tool"] = "shell_exec"  # not in agent_config.tool_whitelist ([_TOOL_NAME])

    dispatcher = _PermissiveNoWhitelistDispatch()
    with pytest.raises(ValueError, match="tool not in whitelist: shell_exec"):
        await interpreter.run(
            recipe,
            session_context=default_session_context(),
            kb_search=EmptyKbSearch(),
            llm=FixtureLLM("smoke-01"),
            embedding=EmptyEmbedding(),
            trace_writer=_NoOpTraceWriter(),
            tool_dispatch=dispatcher,
        )
    assert dispatcher.called is False, "the whitelist check must happen before the injected dispatcher is ever called"
