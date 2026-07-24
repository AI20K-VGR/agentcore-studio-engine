"""Behavioral tests for real `TraceEvent` emission in `interpreter.run()`
(spec AIE-1, Day 5, plan `260724-0924-day5-trace-event-population`).

Teeth per `docs/code-standards.md` §4.1: assertions pin concrete values
(event count, order, field values, monotonic `ts`), not a bare presence
check — a stub that appends empty/placeholder events must FAIL these.
"""

from __future__ import annotations

from uuid import UUID

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

_TOOL_NAME = "search_docs"
ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")


class _RecordingTraceWriter:
    """Spy `TraceWriter` double — records every `write()` call in order, so
    a test can assert exactly which events were emitted and how many times
    (distinct from `_NoOpTraceWriter` elsewhere, which discards everything)."""

    def __init__(self) -> None:
        self.written: list[TraceEvent] = []

    async def write(self, event: TraceEvent) -> None:
        self.written.append(event)


def _four_node_recipe() -> Recipe:
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    return Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[_TOOL_NAME]),
        dag=Dag(nodes=nodes, edges=[]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def _run(writer: _RecordingTraceWriter) -> interpreter.RunResult:
    return await interpreter.run(
        _four_node_recipe(),
        kb_search=EmptyKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=writer,
    )


async def test_run_emits_exactly_four_events_in_node_order() -> None:
    writer = _RecordingTraceWriter()
    result = await _run(writer)

    assert [e.node_id for e in result.events] == ["n_kb", "n_llm", "n_tool", "n_end"]
    assert [e.node_type for e in result.events] == [
        NodeType.KB_RETRIEVE,
        NodeType.LLM_STEP,
        NodeType.TOOL_CALL,
        NodeType.END,
    ]


async def test_trace_writer_write_called_once_per_node_matching_events() -> None:
    writer = _RecordingTraceWriter()
    result = await _run(writer)

    assert len(writer.written) == 4
    assert writer.written == result.events


async def test_all_events_share_run_id_and_recipe_identity() -> None:
    writer = _RecordingTraceWriter()
    result = await _run(writer)

    assert len(result.events) == 4
    for event in result.events:
        assert event.run_id == result.run_id
        assert event.agent_id == "agent-1"
        assert event.tenant_id == ANKOR_ID


async def test_event_timestamps_strictly_increase() -> None:
    writer = _RecordingTraceWriter()
    result = await _run(writer)

    timestamps = [e.ts for e in result.events]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == 4


async def test_llm_step_event_carries_tokens_and_citations_from_executor_output() -> None:
    writer = _RecordingTraceWriter()
    result = await _run(writer)

    llm_event = next(e for e in result.events if e.node_id == "n_llm")
    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_event.tokens == llm_output["tokens"]
    assert llm_event.citations == llm_output["citations"]


async def test_kb_retrieve_event_outputs_wraps_raw_list_in_dict() -> None:
    writer = _RecordingTraceWriter()
    result = await _run(writer)

    kb_event = next(e for e in result.events if e.node_id == "n_kb")
    assert kb_event.outputs == {"chunks": []}


async def test_non_llm_events_have_zero_tokens_and_no_citations() -> None:
    writer = _RecordingTraceWriter()
    result = await _run(writer)

    for event in result.events:
        if event.node_id == "n_llm":
            continue
        assert event.tokens.prompt == 0
        assert event.tokens.completion == 0
        assert event.citations is None
