"""End-to-end `condition` wiring through `interpreter.run()` (spec AIE-1,
plan `260806-0938-d14-aie1-node-executors-grid-prep`, phase 2).

P1 (`test_executors_behavior.py`) proved `ConditionExecutor.execute` in
isolation, with `node.params` hand-built. This file proves the OTHER half of
DoD #2: `interpreter.run()` itself threads `state` (the walk's last output)
and `when` (from the edge the walk actually took) into a `condition` node's
`params` before dispatch — the same inject-into-params pattern already used
for `kb-retrieve`'s `tenant_id` and `llm-step`'s `retrieved_chunks`/`query`
(`interpreter.py`) — and that the 4-node chain `kb-retrieve -> llm-step ->
condition -> end` walks through `run()` for real, emitting one `TraceEvent`
per node.

Coverage check (done before writing this file, phase-2 doc's "Kiểm trùng
coverage" table): `test_interpreter_behavior.py`, `test_trace_event_emission.py`,
`test_dag_edge_walk.py` all cover `kb-retrieve -> llm-step -> tool-call -> end`
— none dispatches a `condition` node, so nothing here duplicates them.

Teeth per `docs/code-standards.md` §4.1: every assertion pins a concrete
value (event count/order, `reason`/`result` string/bool, the exact edge whose
`when` won) — no bare presence check.
"""

from __future__ import annotations

import json
from uuid import UUID

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
from test_kb_retrieve_llm_step_threading import FixtureKbSearch
from test_session_context_tenant_wall import default_session_context

# Team-wide canonical UUID for tenant "ankor" — same value as
# `test_kb_retrieve_llm_step_threading.py:50`, `test_trace_event_emission.py:31`.
ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")


class _RecordingTraceWriter:
    """Spy `TraceWriter` double — records every `write()` call in order.
    Same mold as `test_trace_event_emission.py:34-43`'s `_RecordingTraceWriter`
    (duplicated here rather than imported: that class is module-private in a
    sibling test file, not a shared fixture)."""

    def __init__(self) -> None:
        self.written: list[TraceEvent] = []

    async def write(self, event: TraceEvent) -> None:
        self.written.append(event)


class _Chunk042AnsweringLLM:
    """Test-local `LLM` double — always answers citing `[chunk-042]`, the
    same id `FixtureKbSearch` (`test_kb_retrieve_llm_step_threading.py:53-74`)
    always retrieves. Paired together the citation is grounded (both
    retrieved AND bracket-cited), so `llm-step` reports `refused=False` — the
    positive input `test_condition_evaluates_upstream_llm_output` (T3) needs
    to prove `condition` reads REAL upstream data, not a hardcoded value.

    Distinct from `demo_stubs.FixtureLLM`: that one is a fixed VCR replay of
    `smoke-01.json`, whose recorded answer brackets `[chunk-001]` — a
    citation `FixtureKbSearch`'s `chunk-042` would never match, which would
    make every T3-style scenario a refusal regardless of what is being
    tested. `FixtureLLM` is still used below wherever the citation TEXT
    doesn't matter (any upstream refusal reads the same)."""

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return "Nhân viên được nghỉ phép năm 12 ngày. [chunk-042]"


def _recipe(nodes: list[Node], edges: list[Edge]) -> Recipe:
    return Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(system_prompt="x", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=edges),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


def _kb_llm_cond_end_recipe(*, cond_params: dict[str, object] | None = None, cond_when: str | None) -> Recipe:
    """`n_kb -> n_llm -> n_cond -> n_end`, single successor throughout. The
    `n_cond -> n_end` edge carries `when=cond_when` (`None` when a test wants
    NO edge-supplied predicate, e.g. because the node declares its own)."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_cond", type=NodeType.CONDITION, params=cond_params or {}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    edges = [
        Edge(from_="n_kb", to="n_llm"),
        Edge(from_="n_llm", to="n_cond"),
        Edge(from_="n_cond", to="n_end", when=cond_when),
    ]
    return _recipe(nodes, edges)


async def _run(
    recipe: Recipe, *, kb_search: object, llm: object, writer: _RecordingTraceWriter
) -> interpreter.RunResult:
    return await interpreter.run(
        recipe,
        session_context=default_session_context(),
        kb_search=kb_search,  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        embedding=EmptyEmbedding(),
        trace_writer=writer,
    )


# --------------------------------------------------------------------------
# T1-T2: the 4-node chain dispatches for real and emits one event per node.
# --------------------------------------------------------------------------


async def test_linear_dag_dispatches_kb_llm_condition_end() -> None:
    """DoD #2: `kb-retrieve -> llm-step -> condition -> end` walks through a
    real `run()` call — `final_state`'s key order pins exact dispatch
    order."""
    recipe = _kb_llm_cond_end_recipe(cond_when="refused == false")
    result = await _run(recipe, kb_search=EmptyKbSearch(), llm=FixtureLLM("smoke-01"), writer=_RecordingTraceWriter())

    assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_cond", "n_end"]


async def test_every_node_emits_exactly_one_trace_event() -> None:
    """4 nodes -> 4 `TraceEvent`s, correct `node_type` order, all sharing one
    `run_id`, `ts` strictly increasing — "no node skipped, none duplicated"."""
    recipe = _kb_llm_cond_end_recipe(cond_when="refused == false")
    writer = _RecordingTraceWriter()
    result = await _run(recipe, kb_search=EmptyKbSearch(), llm=FixtureLLM("smoke-01"), writer=writer)

    assert len(result.events) == 4
    assert writer.written == result.events
    assert [e.node_type for e in result.events] == [
        NodeType.KB_RETRIEVE,
        NodeType.LLM_STEP,
        NodeType.CONDITION,
        NodeType.END,
    ]
    run_ids = {e.run_id for e in result.events}
    assert run_ids == {result.run_id}
    timestamps = [e.ts for e in result.events]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == 4


# --------------------------------------------------------------------------
# T3-T5: condition reads REAL upstream data, and never changes the route.
# --------------------------------------------------------------------------


async def test_condition_evaluates_upstream_llm_output() -> None:
    """`FixtureKbSearch` retrieves `chunk-042`, `_Chunk042AnsweringLLM` cites
    it back -> `llm-step` reports `refused=False` -> the `when="refused ==
    false"` edge predicate reads that REAL value and evaluates `True`. A
    hardcoded-`True` implementation passes this by accident; paired with T4
    (same predicate, opposite upstream outcome) it cannot."""
    recipe = _kb_llm_cond_end_recipe(cond_when="refused == false")
    result = await _run(
        recipe, kb_search=FixtureKbSearch(), llm=_Chunk042AnsweringLLM(), writer=_RecordingTraceWriter()
    )

    cond_output = result.final_state["n_cond"]
    assert isinstance(cond_output, dict)
    assert cond_output["reason"] == "ok"
    assert cond_output["result"] is True
    assert cond_output["when"] == "refused == false"


async def test_condition_result_flips_when_upstream_refuses() -> None:
    """Same recipe/predicate as T3, but `EmptyKbSearch` grounds nothing ->
    `refused=True` -> `refused == false` evaluates `False`. Catches a
    hardcoded-`True` implementation, which T3 alone cannot."""
    recipe = _kb_llm_cond_end_recipe(cond_when="refused == false")
    result = await _run(recipe, kb_search=EmptyKbSearch(), llm=FixtureLLM("smoke-01"), writer=_RecordingTraceWriter())

    cond_output = result.final_state["n_cond"]
    assert isinstance(cond_output, dict)
    assert cond_output["reason"] == "ok"
    assert cond_output["result"] is False


async def test_condition_result_does_not_change_the_route() -> None:
    """DEC-A/DL-12.A1-1 is still honored: even with `result is False` (T4's
    exact scenario), the walk keeps following `_build_next_map`'s single
    successor all the way to `n_end` and stops there — no branch on
    `condition`'s own result yet."""
    recipe = _kb_llm_cond_end_recipe(cond_when="refused == false")
    result = await _run(recipe, kb_search=EmptyKbSearch(), llm=FixtureLLM("smoke-01"), writer=_RecordingTraceWriter())

    cond_output = result.final_state["n_cond"]
    assert isinstance(cond_output, dict)
    assert cond_output["result"] is False
    assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_cond", "n_end"]
    assert result.final_state["n_end"] == {"terminated": True}


# --------------------------------------------------------------------------
# T6: node-declared `when` wins over the edge's `when` (same precedence as
# `llm-step`'s `prompt`/`model`, `executors.py:228-247`).
# --------------------------------------------------------------------------


async def test_node_declared_when_wins_over_edge_when() -> None:
    recipe = _kb_llm_cond_end_recipe(
        cond_params={"when": "refused == true"},
        cond_when="refused == false",
    )
    result = await _run(
        recipe, kb_search=FixtureKbSearch(), llm=_Chunk042AnsweringLLM(), writer=_RecordingTraceWriter()
    )

    cond_output = result.final_state["n_cond"]
    assert isinstance(cond_output, dict)
    # Node declared "refused == true"; the edge declared "refused == false".
    # The echoed `when` proves which one the executor actually saw.
    assert cond_output["when"] == "refused == true"
    # `refused` is `False` (FixtureKbSearch + _Chunk042AnsweringLLM, same as
    # T3) -> `False == True` -> `result is False`, `reason == "ok"`.
    assert cond_output["reason"] == "ok"
    assert cond_output["result"] is False


# --------------------------------------------------------------------------
# T7: condition's TraceEvent is JSON-safe and carries no citations (C-1).
# --------------------------------------------------------------------------


async def test_condition_event_outputs_json_serializable_and_no_citations() -> None:
    recipe = _kb_llm_cond_end_recipe(cond_when="refused == false")
    result = await _run(recipe, kb_search=EmptyKbSearch(), llm=FixtureLLM("smoke-01"), writer=_RecordingTraceWriter())

    cond_event = next(e for e in result.events if e.node_id == "n_cond")
    json.dumps(cond_event.outputs)  # must not raise
    assert cond_event.citations is None


# --------------------------------------------------------------------------
# T8: a `condition` fed a `list` (kb-retrieve's raw output) as `state` does
# not crash — it reports `reason="state-not-a-dict"`.
# --------------------------------------------------------------------------


async def test_condition_after_kb_retrieve_gets_list_state_without_crashing() -> None:
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_cond", type=NodeType.CONDITION, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    edges = [
        Edge(from_="n_kb", to="n_cond"),
        Edge(from_="n_cond", to="n_end", when="score > 0"),
    ]
    recipe = _recipe(nodes, edges)
    result = await _run(recipe, kb_search=EmptyKbSearch(), llm=FixtureLLM("smoke-01"), writer=_RecordingTraceWriter())

    kb_output = result.final_state["n_kb"]
    assert isinstance(kb_output, list)
    cond_output = result.final_state["n_cond"]
    assert isinstance(cond_output, dict)
    assert cond_output["reason"] == "state-not-a-dict"
    assert cond_output["result"] is None
    assert list(result.final_state.keys()) == ["n_kb", "n_cond", "n_end"]


# --------------------------------------------------------------------------
# T9 (F4): `when` must come from the SAME edge the walk actually takes.
# --------------------------------------------------------------------------


async def test_when_comes_from_the_edge_the_walk_actually_takes() -> None:
    """`n_cond` has 2 outgoing edges: `to="n_x", when="refused == false"`
    declared FIRST, `to="n_end", when=None` declared SECOND.
    `_build_next_map` (last-write-wins) picks the SECOND edge -> walk goes to
    `n_end`, not `n_x`. A `when` map that filtered by `when is not None`
    instead of following the same last-write-wins rule would hand the
    executor the FIRST edge's predicate ("refused == false") for a walk that
    never took it — F4's exact bug. The correct behavior is
    `reason == "no-when-declared"` (the edge actually taken carries no
    `when`), never `"ok"`."""
    nodes = [Node(id="n_cond", type=NodeType.CONDITION, params={}), Node(id="n_end", type=NodeType.END, params={})]
    edges = [
        Edge(from_="n_cond", to="n_x", when="refused == false"),
        Edge(from_="n_cond", to="n_end", when=None),
    ]
    recipe = _recipe(nodes, edges)

    # Invariant lock (F4): the 2 maps must agree on which edge was chosen.
    edge_map = interpreter._build_edge_map(recipe.dag.edges)
    next_map = interpreter._build_next_map(recipe.dag.edges)
    assert edge_map["n_cond"].to == next_map["n_cond"]
    assert next_map["n_cond"] == "n_end"

    result = await _run(recipe, kb_search=EmptyKbSearch(), llm=FixtureLLM("smoke-01"), writer=_RecordingTraceWriter())

    assert list(result.final_state.keys()) == ["n_cond", "n_end"]
    cond_output = result.final_state["n_cond"]
    assert isinstance(cond_output, dict)
    assert cond_output["reason"] == "no-when-declared"


# --------------------------------------------------------------------------
# T10 (F8): a `condition` as the DAG's start node has no upstream output —
# distinct from "an upstream node ran and returned an empty dict".
# --------------------------------------------------------------------------


async def test_condition_as_start_node_reports_no_upstream_output() -> None:
    nodes = [Node(id="n_cond", type=NodeType.CONDITION, params={}), Node(id="n_end", type=NodeType.END, params={})]
    edges = [Edge(from_="n_cond", to="n_end", when="score > 0")]
    recipe = _recipe(nodes, edges)
    writer = _RecordingTraceWriter()
    result = await _run(recipe, kb_search=EmptyKbSearch(), llm=FixtureLLM("smoke-01"), writer=writer)

    assert list(result.final_state.keys()) == ["n_cond", "n_end"]
    assert len(result.events) == 2
    cond_output = result.final_state["n_cond"]
    assert isinstance(cond_output, dict)
    assert cond_output["reason"] == "no-upstream-output"
    assert cond_output["result"] is None


async def test_start_node_condition_ignores_client_declared_fake_state() -> None:
    """I-1 (D14 #96 final review): a start-node `condition` with a
    client-declared `params={"state": {...}}` (spoofing upstream output)
    must NOT get evaluated against that data — the walk's own truth about
    "no node ran before this one" (F8's `_NO_UPSTREAM`) must win regardless
    of what the recipe already put in `node.params`. Before the fix,
    `interpreter.py`'s injection branch only ran when `last_output is not
    _NO_UPSTREAM`; on a start node it left the client's `state` key
    untouched, so `score >= 0.8` evaluated `True` purely from client-
    supplied data. Fixed: the walk explicitly pops any client-declared
    `state` on this branch, so the result must be
    `reason="no-upstream-output"`/`result is None`, never `reason="ok"`/
    `result is True`."""
    nodes = [
        Node(id="n_cond", type=NodeType.CONDITION, params={"state": {"score": 0.99}}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    edges = [Edge(from_="n_cond", to="n_end", when="score >= 0.8")]
    recipe = _recipe(nodes, edges)
    writer = _RecordingTraceWriter()
    result = await _run(recipe, kb_search=EmptyKbSearch(), llm=FixtureLLM("smoke-01"), writer=writer)

    cond_output = result.final_state["n_cond"]
    assert isinstance(cond_output, dict)
    assert cond_output["reason"] == "no-upstream-output"
    assert cond_output["result"] is None


# --------------------------------------------------------------------------
# T11 (F8): 2 consecutive `condition` nodes — the 2nd's `state` is the 1st's
# OWN output dict, not the walk's original upstream (`llm-step`) output.
# --------------------------------------------------------------------------


async def test_second_condition_gets_first_condition_output_as_state() -> None:
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_cond1", type=NodeType.CONDITION, params={}),
        Node(id="n_cond2", type=NodeType.CONDITION, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    edges = [
        Edge(from_="n_kb", to="n_llm"),
        Edge(from_="n_llm", to="n_cond1"),
        # `n_cond1`'s OWN outgoing edge carries the `when` IT evaluates
        # (against `n_llm`'s state, injected as `n_cond1` dispatches) —
        # `_build_edge_map` keys by `from_`, so this is `edge_by_id["n_cond1"]`.
        Edge(from_="n_cond1", to="n_cond2", when="refused == false"),
        # Likewise `n_cond2`'s OWN outgoing edge carries ITS `when`, evaluated
        # against `n_cond1`'s own output dict (the thing under test here).
        Edge(from_="n_cond2", to="n_end", when="result == true"),
    ]
    recipe = _recipe(nodes, edges)
    result = await _run(
        recipe, kb_search=FixtureKbSearch(), llm=_Chunk042AnsweringLLM(), writer=_RecordingTraceWriter()
    )

    cond1_output = result.final_state["n_cond1"]
    assert isinstance(cond1_output, dict)
    assert cond1_output["reason"] == "ok"
    assert cond1_output["result"] is True

    cond2_output = result.final_state["n_cond2"]
    assert isinstance(cond2_output, dict)
    assert cond2_output["reason"] == "ok"
    assert cond2_output["result"] == cond1_output["result"]
    assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_cond1", "n_cond2", "n_end"]
