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


async def test_zero_start_candidates_raises_value_error() -> None:
    """A 2-node cycle (`a->b->a`) — every node has an incoming edge, so
    there is no valid entry point. Must raise, not silently pick one."""
    nodes = [
        Node(id="n_a", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_b", type=NodeType.END, params={}),
    ]
    edges = [Edge(from_="n_a", to="n_b"), Edge(from_="n_b", to="n_a")]

    with pytest.raises(ValueError, match="exactly 1 start node"):
        await _run(_recipe(nodes, edges))


async def test_multiple_start_candidates_raises_value_error() -> None:
    """2 disconnected nodes, no edges at all — both qualify as "no incoming
    edge", an ambiguous choice the interpreter must refuse to guess."""
    nodes = [
        Node(id="n_a", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_b", type=NodeType.END, params={}),
    ]

    with pytest.raises(ValueError, match="exactly 1 start node"):
        await _run(_recipe(nodes, edges=[]))


async def test_branching_from_non_condition_node_raises_value_error() -> None:
    """`n_kb` has 2 outgoing edges (`->n_llm` and `->n_tool`) — that is
    `condition`-style branching, which `Edge.when` evaluation does not exist
    for yet (`ConditionExecutor` is still `NotImplementedError`). The
    interpreter must refuse to silently pick one arm."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
    ]
    edges = [Edge(from_="n_kb", to="n_llm"), Edge(from_="n_kb", to="n_tool")]

    with pytest.raises(ValueError, match=">1 outgoing edge"):
        await _run(_recipe(nodes, edges))


async def test_reachable_self_loop_raises_instead_of_walking_forever() -> None:
    """A "lasso" — a tail leading into a self-loop — satisfies BOTH existing
    structural checks: `n_kb` is the sole node with no incoming edge (1 start
    candidate) and no node has >1 outgoing edge. Without a cycle guard the
    walk revisits `n_tool` forever, growing `events` and calling
    `trace_writer.write` without bound. The `_CountingTraceWriter` cap turns
    that hang into a loud `AssertionError`, so this test distinguishes "raised
    the right ValueError" from "hung and got capped"."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
    ]
    edges = [Edge(from_="n_kb", to="n_tool"), Edge(from_="n_tool", to="n_tool")]

    with pytest.raises(ValueError, match="cycle"):
        await interpreter.run(
            _recipe(nodes, edges),
            kb_search=EmptyKbSearch(),
            llm=FixtureLLM("smoke-01"),
            embedding=EmptyEmbedding(),
            trace_writer=_CountingTraceWriter(),
        )


async def test_reachable_multi_node_cycle_raises_instead_of_walking_forever() -> None:
    """The 3-node lasso `n_kb -> n_t1 -> n_t2 -> n_t1`. Same two structural
    checks pass; only a `visited` set catches it. Separate from the self-loop
    case because a guard implemented as "reject `edge.from_ == edge.to`" would
    pass that test and still hang here."""
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

    with pytest.raises(ValueError, match="cycle"):
        await interpreter.run(
            _recipe(nodes, edges),
            kb_search=EmptyKbSearch(),
            llm=FixtureLLM("smoke-01"),
            embedding=EmptyEmbedding(),
            trace_writer=_CountingTraceWriter(),
        )


async def test_chain_without_end_node_raises_instead_of_returning_silently() -> None:
    """The chain runs out of edges at `n_tool`, never reaching an `end` node.
    Day 3's by-type `end` lookup made this a loud `KeyError`; an edge-derived
    walk would otherwise fall out of the loop and hand back a normal-looking
    `RunResult`, making a truncated run indistinguishable from a complete one
    (`RunResult` has no "terminated" field for a caller to check)."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
    ]
    edges = [Edge(from_="n_kb", to="n_llm"), Edge(from_="n_llm", to="n_tool")]

    with pytest.raises(ValueError, match="without executing an `end` node"):
        await _run(_recipe(nodes, edges))


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
        kb_search=_SequentialKbSearch(),
        llm=_AnsweringLLM("Không có tài liệu nào được tra cứu. [chunk-call1]"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == []
