"""engine#38 — `cost` must be real at emit time, not the `_NO_COST`/`0.0` placeholder.

Both pipelines emit `TraceEvent`s: `interpreter.run()` (DAG walk, 1 emit site) and
`agent_loop.run_agent_loop()` (LLM-centric loop, 4 emit sites). `cost_of` lives in
`studio_contracts.cost` (moved down from `studio_kb.cost`, mini-RFC
`packages/contracts/docs/mini-rfc-cost-of-seam.md`) so both call sites can reach it despite
`.importlinter` forbidding `studio_engine` -> `studio_kb`.

Pins §4.1 ("một số, ba mặt") the way `studio_kb.price_mismatches()` checks it downstream: every
emitted event's `cost` must equal `cost_of(event.tokens)`. A mutant that reverts either call site
back to a hardcoded `0.0` must fail here — `test_agent_loop_cost_nonzero_on_real_tokens` and
`test_interpreter_cost_nonzero_on_real_tokens` assert `!= 0.0` specifically so a mutant that
returns `cost_of(tokens) or 0.0` (or any other path that degrades to the old constant on real
non-zero tokens) cannot slip through an equality-only check.
"""

from __future__ import annotations

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
from studio_contracts.cost import cost_of
from studio_engine import agent_loop, interpreter
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM
from test_agent_loop_behavior import _RecordingKbSearch, _ScriptedLLM, _session
from test_session_context_tenant_wall import default_session_context

_TOOL_NAME = "search_docs"
ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")


class _NoOpTraceWriter:
    async def write(self, event: TraceEvent) -> None:
        del event


def _llm_step_recipe() -> Recipe:
    nodes = [
        Node(id="n-llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n-end", type=NodeType.END, params={}),
    ]
    edges = [Edge(**{"from": "n-llm", "to": "n-end"})]
    return Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(system_prompt="x", model="m", tool_whitelist=[_TOOL_NAME]),
        dag=Dag(nodes=nodes, edges=edges),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def test_interpreter_emits_cost_matching_cost_of_tokens() -> None:
    result = await interpreter.run(
        _llm_step_recipe(),
        session_context=default_session_context(),
        kb_search=EmptyKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )
    assert result.events, "no event emitted — fixture setup is wrong, not what this test pins"
    for event in result.events:
        assert event.cost == cost_of(event.tokens), (
            f"{event.node_id}: cost={event.cost} != cost_of(tokens)={cost_of(event.tokens)} — "
            "price source drifted from the single-source table in studio_contracts.cost"
        )


async def test_interpreter_cost_nonzero_on_real_tokens() -> None:
    """`FixtureLLM("smoke-01")` returns real text for a real prompt — tokens are non-zero, so a
    mutant that silently falls back to the old `_NO_COST = 0.0` constant must fail this."""
    result = await interpreter.run(
        _llm_step_recipe(),
        session_context=default_session_context(),
        kb_search=EmptyKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )
    llm_events = [e for e in result.events if e.node_type is NodeType.LLM_STEP]
    assert llm_events, "no llm-step event — fixture setup is wrong, not what this test pins"
    for event in llm_events:
        assert event.tokens.prompt > 0 or event.tokens.completion > 0
        assert event.cost != 0.0


async def test_agent_loop_emits_cost_matching_cost_of_tokens() -> None:
    """Covers all 4 `TraceEvent` sites in `run_agent_loop()` in one run: final-answer LLM
    event, kb-retrieve event, tool-call LLM event, tool-call event."""
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            'TOOL_CALL: {"tool":"calculator","params":{"expression":"1+1"}}',
            "Câu trả lời cuối cùng.",
        ]
    )
    kb = _RecordingKbSearch()

    class _RecordingDispatch:
        async def dispatch(self, tool: str, params: dict[str, object]) -> object:
            del tool, params
            return {"result": 2}

    class _CollectingTraceWriter:
        def __init__(self) -> None:
            self.events: list[TraceEvent] = []

        async def write(self, event: TraceEvent) -> None:
            self.events.append(event)

    writer = _CollectingTraceWriter()
    result = await agent_loop.run_agent_loop(
        Recipe(
            agent_id="agent-loop-cost-test",
            tenant_id=ANKOR_ID,
            agent_config=AgentConfig(system_prompt="", model="", tool_whitelist=["calculator", "kb_search"]),
            dag=Dag(nodes=[], edges=[]),
            kb_binding=KbBinding(kb_id="kb-1", scope="test/scope"),
            golden_set_ref="golden-1",
            scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
        ),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=writer,
        question="q",
        tool_dispatch=_RecordingDispatch(),
    )
    assert result.events, "no event emitted — fixture setup is wrong, not what this test pins"
    assert {e.node_type for e in result.events} == {NodeType.LLM_STEP, NodeType.KB_RETRIEVE, NodeType.TOOL_CALL}
    for event in result.events:
        assert event.cost == cost_of(event.tokens), (
            f"{event.node_id}: cost={event.cost} != cost_of(tokens)={cost_of(event.tokens)} — "
            "price source drifted from the single-source table in studio_contracts.cost"
        )


async def test_agent_loop_cost_nonzero_on_real_tokens() -> None:
    llm = _ScriptedLLM(["Câu trả lời trực tiếp, đủ dài để có token thật."])
    result = await agent_loop.run_agent_loop(
        Recipe(
            agent_id="agent-loop-cost-test-2",
            tenant_id=ANKOR_ID,
            agent_config=AgentConfig(system_prompt="", model="", tool_whitelist=["calculator"]),
            dag=Dag(nodes=[], edges=[]),
            kb_binding=KbBinding(kb_id="kb-1", scope="test/scope"),
            golden_set_ref="golden-1",
            scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
        ),
        session_context=_session(),
        kb_search=_RecordingKbSearch(),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
        question="Câu hỏi?",
    )
    assert len(result.events) == 1
    event = result.events[0]
    assert event.tokens.prompt > 0 or event.tokens.completion > 0
    assert event.cost != 0.0
