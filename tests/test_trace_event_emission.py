"""Behavioral tests for real `TraceEvent` emission in `interpreter.run()`
(spec AIE-1, Day 5, plan `260724-0924-day5-trace-event-population`).

Teeth per `docs/code-standards.md` §4.1: assertions pin concrete values
(event count, order, field values, monotonic `ts`), not a bare presence
check — a stub that appends empty/placeholder events must FAIL these.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from studio_contracts import (
    AgentConfig,
    Dag,
    Edge,
    KbBinding,
    KbSearch,
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
        dag=Dag(
            nodes=nodes,
            edges=[
                Edge(from_="n_kb", to="n_llm"),
                Edge(from_="n_llm", to="n_tool"),
                Edge(from_="n_tool", to="n_end"),
            ],
        ),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def _run(writer: _RecordingTraceWriter, kb_search: KbSearch | None = None) -> interpreter.RunResult:
    return await interpreter.run(
        _four_node_recipe(),
        session_context=default_session_context(),
        kb_search=kb_search or EmptyKbSearch(),
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


class _CitationForgingToolDispatch:
    """Một `ToolDispatch` trả về key `"citations"`.

    KHÔNG phải kịch bản bịa: `ToolCallExecutor.execute` trả **thẳng** dict của
    `ToolDispatch.dispatch()` (`executors.py:314`), mà `ToolDispatch` là seam
    NGOÀI — tool do người khác viết. Một tool đặt key trùng tên là chuyện bình
    thường, không phải mã độc.
    """

    def __init__(self, whitelist: list[str]) -> None:
        self._whitelist = whitelist

    async def dispatch(self, tool: str) -> object:
        return {"tool": tool, "citations": ["chunk-BIA-001", "chunk-BIA-002"]}


async def test_c1_chi_llm_step_duoc_mang_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    """KHÓA C-1 (`docs/contracts/trace-citations.v0.md`) — `TraceEvent.citations`
    chỉ được điền cho `llm-step`; mọi node khác PHẢI để `None`.

    Trước D11 interpreter nhấc `citations` từ output của **bất kỳ** node trả dict,
    nên "chỉ llm-step" là hành vi tình cờ đúng — không có gì bắt buộc nó. Mutation
    M1 của @dholmes0207 đã đo: cho `kb-retrieve` phát citations thì **evalhub vẫn
    xanh 42 passed**, tức không lớp nào bên tiêu thụ phát hiện được.

    Vỡ C-1 hỏng theo kiểu tệ nhất — không ném lỗi, không log: marker bịa vào trace
    như trích dẫn thật, `citation_accuracy` (evalhub) ăn điểm giả, và `refused =
    not citations` (`executors.py:260`) đọc ngược.
    """
    monkeypatch.setattr(interpreter, "WhitelistToolDispatch", _CitationForgingToolDispatch)
    writer = _RecordingTraceWriter()

    result = await _run(writer)

    by_type = {e.node_type: e for e in result.events}
    assert by_type[NodeType.TOOL_CALL].citations is None, "tool-call mang được citations — C-1 vỡ"
    assert by_type[NodeType.KB_RETRIEVE].citations is None
    assert by_type[NodeType.END].citations is None

    # Chặn, KHÔNG xoá: dữ liệu tool trả vẫn truy được nguyên vẹn ở `outputs`.
    # Nếu không có dòng này thì "sửa" bằng cách vứt hẳn key đi cũng qua bài trên.
    assert by_type[NodeType.TOOL_CALL].outputs["citations"] == ["chunk-BIA-001", "chunk-BIA-002"]


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
    assert kb_event.outputs == {"chunks": [], "fenced": True}


async def test_kb_retrieve_event_has_no_fenced_key_when_chunks_present() -> None:
    """0 chunk hợp lệ → `fenced: True` (test ở trên). Ngược lại — có ít nhất
    1 chunk thật — key `fenced` phải VẮNG MẶT, không phải `False`: additive
    tối thiểu (`docs/code-standards.md` §6), consumer đọc bằng `.get("fenced")`.
    Chặn một impl gắn `fenced` VÔ ĐIỀU KIỆN (impl như vậy vẫn làm test rỗng ở
    trên xanh, nhưng làm test này đỏ)."""
    writer = _RecordingTraceWriter()
    result = await _run(writer, kb_search=FixtureKbSearch())

    kb_event = next(e for e in result.events if e.node_id == "n_kb")
    chunks = kb_event.outputs["chunks"]
    assert isinstance(chunks, list)
    assert len(chunks) == 1
    assert "fenced" not in kb_event.outputs


class _ListReturningToolDispatch:
    """Một `ToolDispatch` trả về LIST rỗng. Không phải kịch bản bịa:
    `ToolCallExecutor.execute` trả THẲNG giá trị của `dispatcher.dispatch()`
    (`executors.py:466`), mà `ToolDispatch` là seam NGOÀI — tool do người
    khác viết, trả list là chuyện bình thường (cùng lập luận
    `_CitationForgingToolDispatch`, `:123-130`)."""

    def __init__(self, whitelist: list[str]) -> None:
        self._whitelist = whitelist

    async def dispatch(self, tool: str) -> object:
        del tool
        return []


async def test_list_returning_tool_call_event_never_carries_fenced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test có răng cho cổng `node_type` (KHÔNG theo hình dạng output).
    Node `tool-call` cũng có thể đi vào nhánh `isinstance(output, list)` khi
    dispatch trả list — nếu enrichment suy `fenced` từ hình dạng `output`
    thay vì `node_type is NodeType.KB_RETRIEVE`, event `tool-call` này sẽ
    mang một bản ghi hàng-rào GIẢ trong audit trail, dù nó chưa từng gọi
    `kb.search`. Đúng lớp lỗi C-1 (`interpreter.py:392-401`)."""
    monkeypatch.setattr(interpreter, "WhitelistToolDispatch", _ListReturningToolDispatch)
    writer = _RecordingTraceWriter()

    result = await _run(writer)

    by_type = {e.node_type: e for e in result.events}
    assert by_type[NodeType.TOOL_CALL].outputs == {"chunks": []}
    assert "fenced" not in by_type[NodeType.TOOL_CALL].outputs


async def test_all_event_outputs_are_json_serializable() -> None:
    """Regression pin (code-review finding): `TraceEvent.outputs` must never
    carry a raw pydantic object (e.g. `Tokens`) — `PgTraceWriter.write()`
    (`apps/studio/src/studio_app/obs/trace_writer.py`) serializes `outputs`
    via `psycopg.types.json.Jsonb`, which calls `json.dumps` under the hood
    and would raise `TypeError` on a non-JSON-safe value."""
    writer = _RecordingTraceWriter()
    result = await _run(writer)

    for event in result.events:
        json.dumps(event.outputs)  # must not raise


async def test_non_llm_events_have_zero_tokens_and_no_citations() -> None:
    writer = _RecordingTraceWriter()
    result = await _run(writer)

    for event in result.events:
        if event.node_id == "n_llm":
            continue
        assert event.tokens.prompt == 0
        assert event.tokens.completion == 0
        assert event.citations is None
