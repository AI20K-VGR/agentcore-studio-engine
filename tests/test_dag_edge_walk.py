"""Behavioral tests for Day 6's edge-derived walk (spec AIE-1, plan
`260727-...-day6-dag-edge-walk`) — `interpreter.run()` no longer hardcodes a
`NodeType` walk order (Day 3's `_WALK_ORDER`, now removed); dispatch order
comes from `recipe.dag.edges`.

Teeth per `docs/code-standards.md` §4.1: `test_walk_follows_edge_order_not_node_declaration_order`
pins a concrete node-id order that only a genuinely edge-driven walk can produce — declaring
`dag.nodes` out of execution order and asserting the walk still runs correctly is what tells apart
"reads edges" from "got lucky because nodes happened to already be in order" or "still secretly
keyed off NodeType".
"""

from __future__ import annotations

from uuid import UUID

import pytest
from studio_contracts import (
    AgentConfig,
    Dag,
    Edge,
    KbBinding,
    KbSearchResultItem,
    Node,
    NodeType,
    Recipe,
    ScorecardThreshold,
    TraceEvent,
)
from studio_engine import interpreter
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM
from test_session_context_tenant_wall import default_session_context

_TOOL_NAME = "search_docs"
ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")


class _NoOpTraceWriter:
    async def write(self, event: TraceEvent) -> None:
        del event


def _recipe(nodes: list[Node], edges: list[Edge]) -> Recipe:
    return Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[_TOOL_NAME]),
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


class _CountingTraceWriter:
    """Records how many events were written. A walk that fails to terminate
    calls `write` without bound, so a test that must NOT hang needs a hard
    stop here rather than trusting the interpreter to stop on its own — the
    cap raises a distinctly-named error so a hang-bug can never be mistaken
    for the `ValueError` the cycle guard is supposed to produce."""

    def __init__(self, cap: int = 50) -> None:
        self.written: list[str] = []
        self._cap = cap

    async def write(self, event: TraceEvent) -> None:
        self.written.append(event.node_id)
        if len(self.written) > self._cap:
            raise AssertionError(f"walk did not terminate: {self._cap}+ dispatches, order={self.written[:10]}")


class _SequentialKbSearch:
    """Returns a DIFFERENT single chunk per call (`chunk-call1`, `chunk-call2`,
    …), so a test can tell which `kb-retrieve` node's output actually reached
    a downstream `llm-step`. A double returning the same chunk every time
    cannot distinguish "threaded the walk's upstream kb node" from "threaded
    some other kb node"."""

    def __init__(self) -> None:
        self.calls = 0

    async def search(
        self,
        query: str,
        tenant_id: UUID,
        section_roles: list[str],
        top_k: int,
    ) -> list[KbSearchResultItem]:
        del query, tenant_id, section_roles, top_k
        self.calls += 1
        return [
            KbSearchResultItem(
                chunk_id=f"chunk-call{self.calls}",
                text=f"nội dung lần gọi thứ {self.calls}",
                score=0.9,
                tenant_id=ANKOR_ID,
                section_role="public",
            )
        ]


class _AnsweringLLM:
    """Replays a fixed answer string so a test controls exactly which
    `[chunk_id]` brackets appear, independent of the `smoke-01` fixture."""

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return self._answer


async def test_walk_follows_edge_order_not_node_declaration_order() -> None:
    """`dag.nodes` is declared in REVERSE of execution order
    (`end, tool, llm, kb`); only `dag.edges` (`kb->llm->tool->end`) describes
    the real chain. A walk that were still secretly keyed off a fixed
    `NodeType` sequence (or off `dag.nodes` list order) cannot produce the
    correct `kb, llm, tool, end` result here — only reading `dag.edges`
    can."""
    nodes = [
        Node(id="n_end", type=NodeType.END, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
    ]
    edges = [
        Edge(from_="n_kb", to="n_llm"),
        Edge(from_="n_llm", to="n_tool"),
        Edge(from_="n_tool", to="n_end"),
    ]
    result = await _run(_recipe(nodes, edges))

    assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_tool", "n_end"]


async def test_zero_start_candidates_raises_index_error_not_value_error() -> None:
    """A 2-node cycle (`a->b->a`) — every node has an incoming edge, so
    `_find_start_node_id`'s `starts` list is empty. DEC-A (decision-log,
    packages/engine/docs/decisions/decision-log.md) dropped the explicit
    "exactly 1 start node" `ValueError` guard; `starts[0]` on an empty list
    now raises `IndexError` naturally — a loud crash from an unguarded index,
    not the old descriptive `ValueError` message, and NOT a silent fallback
    to some other node."""
    nodes = [
        Node(id="n_a", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_b", type=NodeType.END, params={}),
    ]
    edges = [Edge(from_="n_a", to="n_b"), Edge(from_="n_b", to="n_a")]

    with pytest.raises(IndexError):
        await _run(_recipe(nodes, edges))


async def test_multiple_start_candidates_picks_first_node_in_dag_order() -> None:
    """2 disconnected nodes, no edges at all — both qualify as "no incoming
    edge". DEC-A dropped the ambiguity guard; `_find_start_node_id` now
    returns `starts[0]` unconditionally, and `starts` is built by iterating
    `dag.nodes` in declared order — so the FIRST declared node (`n_a`) wins
    as the (silent) start, and `n_b` is never reached by the walk at all."""
    nodes = [
        Node(id="n_a", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_b", type=NodeType.END, params={}),
    ]

    result = await _run(_recipe(nodes, edges=[]))

    assert list(result.final_state.keys()) == ["n_a"]


async def test_branching_from_non_condition_node_follows_last_declared_edge() -> None:
    """`n_kb` has 2 outgoing edges (`->n_llm` declared first, `->n_tool`
    declared second). DEC-A dropped the ">1 outgoing edge" guard;
    `_build_next_map` now does a plain dict assignment per edge in
    declaration order, so the LAST-declared edge for a given `from_` wins
    (last-write-wins, silent) — the walk follows `n_kb -> n_tool` and never
    visits `n_llm`."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
    ]
    edges = [Edge(from_="n_kb", to="n_llm"), Edge(from_="n_kb", to="n_tool")]

    result = await _run(_recipe(nodes, edges))

    assert list(result.final_state.keys()) == ["n_kb", "n_tool"]


async def test_cycle_now_hangs_bounded_only_by_test_harness_cap() -> None:
    """A "lasso" — a tail leading into a self-loop. DEC-A removed the
    `visited`-set cycle guard ENTIRELY, with no replacement cap anywhere in
    `interpreter.py` (decision-log DEC-A: the cap only ever exists in this
    test file's `_CountingTraceWriter`, never in production code). The walk
    now revisits `n_tool` forever, growing `events` and calling
    `trace_writer.write` without bound. This test name says the quiet part
    out loud: an unbounded hang is the ACCEPTED RISK (plan R1), not a
    desired behavior — the only thing stopping this test itself from hanging
    the suite is `_CountingTraceWriter`'s own cap, which raises
    `AssertionError` after `cap` writes."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
    ]
    edges = [Edge(from_="n_kb", to="n_tool"), Edge(from_="n_tool", to="n_tool")]

    with pytest.raises(AssertionError, match="walk did not terminate"):
        await interpreter.run(
            _recipe(nodes, edges),
            session_context=default_session_context(),
            kb_search=EmptyKbSearch(),
            llm=FixtureLLM("smoke-01"),
            embedding=EmptyEmbedding(),
            trace_writer=_CountingTraceWriter(),
        )


async def test_multi_node_cycle_now_hangs_bounded_only_by_test_harness_cap() -> None:
    """The 3-node lasso `n_kb -> n_t1 -> n_t2 -> n_t1`. Same as the
    self-loop case above (DEC-A removed the `visited` guard with no
    replacement), kept as a separate test because a guard implemented as
    "reject `edge.from_ == edge.to`" would have passed the self-loop test
    while still hanging here — this pins the general (not just
    self-referential) cycle case."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_t1", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
        Node(id="n_t2", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
    ]
    edges = [
        Edge(from_="n_kb", to="n_t1"),
        Edge(from_="n_t1", to="n_t2"),
        Edge(from_="n_t2", to="n_t1"),
    ]

    with pytest.raises(AssertionError, match="walk did not terminate"):
        await interpreter.run(
            _recipe(nodes, edges),
            session_context=default_session_context(),
            kb_search=EmptyKbSearch(),
            llm=FixtureLLM("smoke-01"),
            embedding=EmptyEmbedding(),
            trace_writer=_CountingTraceWriter(),
        )


async def test_chain_without_end_node_returns_truncated_run_result_silently() -> None:
    """The chain runs out of edges at `n_tool`, never reaching an `end`
    node. DEC-A dropped the "must terminate on `end`" guard; `run()` now
    simply `break`s out of the walk loop and returns a normal `RunResult`
    with whatever state it accumulated. This is intentionally the exact
    danger the removed guard's own comment warned about — "a truncated run
    would be indistinguishable from a complete one to every caller" —
    accepted on purpose per DEC-A, not an oversight. `final_state` has an
    entry for every node the walk actually visited (`n_kb`, `n_llm`,
    `n_tool`) and no `terminated` flag exists on `RunResult` to tell this
    apart from a run that legitimately ended at `end`."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
    ]
    edges = [Edge(from_="n_kb", to="n_llm"), Edge(from_="n_llm", to="n_tool")]

    result = await _run(_recipe(nodes, edges))

    assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_tool"]


async def test_llm_step_threads_chunks_from_its_walk_upstream_kb_not_last_declared() -> None:
    """Two `kb-retrieve` nodes. `n_kb_a` is declared LAST, so a by-type
    `{node.type: node}` lookup (last-declared-wins) resolves to it — but the
    walk `n_kb_a -> n_kb_b -> n_llm` makes `n_kb_b` the real upstream. The
    answer cites only `chunk-call2` (`n_kb_b`'s chunk, retrieved second).

    Under a by-type lookup `llm-step` receives `n_kb_a`'s `chunk-call1`
    instead, so `LlmStepExecutor`'s grounding filter drops the cited id as
    un-retrieved and `citations` collapses to `[]` — silently wrong grounding,
    no exception. This asserts the exact value that only walk-derived
    threading can produce."""
    nodes = [
        Node(id="n_kb_b", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_kb_a", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    edges = [
        Edge(from_="n_kb_a", to="n_kb_b"),
        Edge(from_="n_kb_b", to="n_llm"),
        Edge(from_="n_llm", to="n_end"),
    ]
    result = await interpreter.run(
        _recipe(nodes, edges),
        session_context=default_session_context(),
        kb_search=_SequentialKbSearch(),
        llm=_AnsweringLLM("Theo tài liệu vừa tra cứu. [chunk-call2]"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    assert list(result.final_state.keys()) == ["n_kb_a", "n_kb_b", "n_llm", "n_end"]
    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == ["chunk-call2"]


async def test_llm_step_without_upstream_kb_retrieve_threads_empty_not_key_error() -> None:
    """A walk with no `kb-retrieve` at all. A by-type lookup raises a bare
    `KeyError(NodeType.KB_RETRIEVE)` before the executor ever runs — even
    though `LlmStepExecutor` documents an absent/empty `retrieved_chunks` as a
    valid input yielding no citations. The edge-derived walk must reach the
    executor and thread `[]`."""
    nodes = [
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    edges = [Edge(from_="n_llm", to="n_end")]
    result = await interpreter.run(
        _recipe(nodes, edges),
        session_context=default_session_context(),
        kb_search=_SequentialKbSearch(),
        llm=_AnsweringLLM("Không có tài liệu nào được tra cứu. [chunk-call1]"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == []
