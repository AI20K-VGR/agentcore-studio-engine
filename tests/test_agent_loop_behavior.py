"""Phase 3 (plan `260823-1249-engine33-agent-loop`, engine#33) — tests-before for
`studio_engine.agent_loop.run_agent_loop()`: the 1-LLM-N-tool loop that replaces
DAG-walk. Local doubles only (no `studio_kb` import — `.importlinter` forbids
`studio_engine` importing `studio_kb`, tiền lệ `test_cross_tenant_refusal_audit.py:41-46`).
`ANKOR_ID`/`BOREA_ID`/`_FrozenSessionContext` imported from
`test_session_context_tenant_wall.py` — the shared canonical double.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from studio_contracts import (
    AgentConfig,
    Dag,
    KbBinding,
    KbSearchResultItem,
    NodeType,
    Recipe,
    ScorecardThreshold,
)
from studio_engine import RunResult, agent_loop
from studio_engine.agent_protocol import Observation, build_agent_prompt
from studio_engine.demo_stubs import EmptyEmbedding, FixtureError, ToolCallingFixtureLLM
from test_session_context_tenant_wall import ANKOR_ID, BOREA_ID, _FrozenSessionContext


def _recipe(
    tool_whitelist: list[str] | None = None,
    tenant_id: UUID = ANKOR_ID,
    system_prompt: str = "",
    model: str = "",
) -> Recipe:
    """Loop is DAG-blind (K8) — `dag` is always empty here on purpose; L12
    below asserts that directly, every other test just relies on it."""
    return Recipe(
        agent_id="agent-loop-test",
        tenant_id=tenant_id,
        agent_config=AgentConfig(
            system_prompt=system_prompt, model=model, tool_whitelist=tool_whitelist or ["calculator"]
        ),
        dag=Dag(nodes=[], edges=[]),
        kb_binding=KbBinding(kb_id="kb-1", scope="test/scope"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


def _session(tenant_id: UUID = ANKOR_ID, system_roles: list[str] | None = None) -> _FrozenSessionContext:
    return _FrozenSessionContext(
        tenant_id=tenant_id, user="test-user", system_roles=system_roles if system_roles is not None else ["public"]
    )


def _chunk(chunk_id: str, text: str = "text", tenant_id: UUID = ANKOR_ID, role: str = "public") -> KbSearchResultItem:
    return KbSearchResultItem(chunk_id=chunk_id, text=text, score=0.9, tenant_id=tenant_id, section_role=role)


def _state_entry(result: RunResult, key: str) -> dict[str, object]:
    """`RunResult.final_state` is typed `dict[str, object]` — every entry this
    loop ever writes is actually a `dict[str, object]` (never a bare
    `list`/scalar the way `interpreter.run()`'s `kb-retrieve` entries can be),
    so this narrows once for every test below instead of repeating an
    `isinstance` assert inline."""
    value = result.final_state[key]
    assert isinstance(value, dict)
    return value


def _first_state_entry(result: RunResult) -> dict[str, object]:
    return _state_entry(result, next(iter(result.final_state)))


def _last_state_entry(result: RunResult) -> dict[str, object]:
    return _state_entry(result, list(result.final_state)[-1])


class _ScriptedLLM:
    """Replays `responses` in order, 1 per `complete()` call. Records every
    prompt received (`self.prompts`) and the call count (`self.calls`)."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls = 0
        self.prompts: list[str] = []

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del kwargs
        self.prompts.append(prompt)
        response = self._responses[self.calls]
        self.calls += 1
        return response


class _RecordingKbSearch:
    """`KbSearch` double — records every `(query, tenant_id, section_roles,
    top_k)` it actually received, returns a fixed `chunks` list regardless of
    query (this module's tests don't need relevance filtering, only fence
    parity — `test_agent_loop_fence.py`, phase 4, owns the real scoped-filter
    double)."""

    def __init__(self, chunks: list[KbSearchResultItem] | None = None) -> None:
        self.calls: list[tuple[str, UUID, list[str], int]] = []
        self._chunks = chunks if chunks is not None else []

    async def search(
        self, query: str, tenant_id: UUID, section_roles: list[str], top_k: int
    ) -> list[KbSearchResultItem]:
        self.calls.append((query, tenant_id, list(section_roles), top_k))
        return list(self._chunks)


class _RecordingDispatch:
    """`ToolDispatch` double — records every `(tool, params)` dispatched."""

    def __init__(self, result: object = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._result = result if result is not None else {"status": "ok"}

    async def dispatch(self, tool: str, params: dict[str, object]) -> object:
        self.calls.append((tool, dict(params)))
        return self._result


class _CollectingTraceWriter:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def write(self, event: object) -> None:
        self.events.append(event)


# --- Turn sequencing / trace shape -------------------------------------------


async def test_final_answer_on_first_turn_stops() -> None:
    llm = _ScriptedLLM(["Câu trả lời trực tiếp."])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=_RecordingKbSearch(),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="Câu hỏi?",
    )
    assert llm.calls == 1
    assert len(result.events) == 1
    assert result.events[0].node_type is NodeType.LLM_STEP


async def test_kb_search_then_answer_event_sequence() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Câu trả lời sau khi tra cứu.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert [e.node_type for e in result.events] == [NodeType.LLM_STEP, NodeType.KB_RETRIEVE, NodeType.LLM_STEP]


async def test_citations_only_on_final_llm_event() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Câu trả lời có căn cứ [doc#c1].",
            # engine#43: a citation-bearing FinalAnswer now triggers a 3rd
            # `llm.complete()` call (the faithfulness-verify pass) — without
            # this response `_ScriptedLLM` raises IndexError on that call.
            "CO",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert result.events[0].citations is None
    assert result.events[1].citations is None
    assert isinstance(result.events[2].citations, list)


# --- engine#43 (HB2-25): faithfulness-verify ---------------------------------
# `ground_citations` proves PROVENANCE only (cited id was retrieved) — it
# cannot prove SUBJECT match (the cited chunk actually answers what was
# asked). A 2nd LLM call, fired only when `citations` is non-empty, catches
# that gap. All scripted-LLM lists below need a 3rd response for any turn
# whose FinalAnswer carries a `[chunk_id]` bracket.


async def test_faithfulness_verify_skipped_when_no_citations() -> None:
    llm = _ScriptedLLM(["Trả lời chay."])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=_RecordingKbSearch(chunks=[]),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert llm.calls == 1
    final = _last_state_entry(result)
    assert final["citations"] == []


async def test_faithfulness_verify_khong_verdict_strips_citation_and_refuses() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Câu trả lời có căn cứ [doc#c1].",
            "KHONG",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    final = _last_state_entry(result)
    assert final["citations"] == []
    assert final["refused"] is True


async def test_faithfulness_verify_co_verdict_keeps_citation() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Câu trả lời có căn cứ [doc#c1].",
            "CO",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    final = _last_state_entry(result)
    assert final["citations"] == ["doc#c1"]
    assert final["refused"] is False


async def test_faithfulness_verify_diacritic_co_regression() -> None:
    # Regression: `.strip().upper().startswith("CO")` (the naive form) does
    # NOT match "CÓ" — this must not silently strip a valid citation.
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Câu trả lời có căn cứ [doc#c1].",
            "CÓ",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    final = _last_state_entry(result)
    assert final["citations"] == ["doc#c1"]


async def test_faithfulness_verify_hb2_25_shape() -> None:
    """Direct-traceability test for the bug this PR fixes: session scoped to
    Ankor, question about "Borea", model answers from Ankor's own chunk —
    `ground_citations` alone would score this grounded; the faithfulness
    verify is what catches the subject mismatch."""
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"Borea P1 incident"}}',
            "Sự cố P1 của Borea cần xử lý trong dưới 1 giờ [ankor-engineering-incident#c6].",
            "KHONG",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("ankor-engineering-incident#c6", text="P1 MTTR target: dưới 1 giờ.")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="Sự cố P1 của Borea cần xử lý trong bao lâu?",
    )
    final = _last_state_entry(result)
    assert final["citations"] == []
    assert final["refused"] is True


async def test_tool_call_turn_maps_to_TOOL_CALL_node_type() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"calculator","params":{"expression":"3+5"}}',
            "Kết quả là 8.",
        ]
    )
    dispatch = _RecordingDispatch(result={"expression": "3+5", "result": 8})
    result = await agent_loop.run_agent_loop(
        _recipe(tool_whitelist=["calculator"]),
        session_context=_session(),
        kb_search=_RecordingKbSearch(),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
        tool_dispatch=dispatch,
    )
    assert result.events[1].node_type is NodeType.TOOL_CALL
    assert dispatch.calls[0][0] == "calculator"
    assert dispatch.calls[0][1].get("tool") == "calculator"


async def test_observation_threaded_into_next_prompt() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Câu trả lời.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1", text="NOI_DUNG_DAC_TRUNG")])
    await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert "NOI_DUNG_DAC_TRUNG" in llm.prompts[1]


async def test_exactly_one_final_state_entry_has_answer_key() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            'TOOL_CALL: {"tool":"calculator","params":{"expression":"1+1"}}',
            "Câu trả lời cuối.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    dispatch = _RecordingDispatch()
    result = await agent_loop.run_agent_loop(
        _recipe(tool_whitelist=["calculator"]),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
        tool_dispatch=dispatch,
    )
    keys = list(result.final_state)
    entries_with_answer = []
    for k in keys:
        value = result.final_state[k]
        if isinstance(value, dict) and "answer" in value:
            entries_with_answer.append(k)
    assert len(entries_with_answer) == 1
    assert entries_with_answer[0] == keys[-1]


async def test_all_events_carry_session_tenant() -> None:
    llm = _ScriptedLLM(["Trả lời ngay."])
    result = await agent_loop.run_agent_loop(
        _recipe(tenant_id=BOREA_ID),
        session_context=_session(tenant_id=ANKOR_ID),
        kb_search=_RecordingKbSearch(),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert all(e.tenant_id == ANKOR_ID for e in result.events)
    assert not any(e.tenant_id == BOREA_ID for e in result.events)


async def test_ts_strictly_increasing() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Trả lời.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    timestamps = [e.ts for e in result.events]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)


async def test_inputs_hash_non_empty_and_differs_per_turn() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Trả lời.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    hashes = [e.inputs_hash for e in result.events]
    assert all(hashes)
    assert len(set(hashes)) == len(hashes)


async def test_run_id_stable_across_events() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Trả lời.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert len({e.run_id for e in result.events}) == 1


async def test_node_type_never_outside_three_values() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            'TOOL_CALL: {"tool":"calculator","params":{"expression":"1+1"}}',
            "Trả lời.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    dispatch = _RecordingDispatch()
    result = await agent_loop.run_agent_loop(
        _recipe(tool_whitelist=["calculator"]),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
        tool_dispatch=dispatch,
    )
    assert {e.node_type for e in result.events} <= {NodeType.LLM_STEP, NodeType.KB_RETRIEVE, NodeType.TOOL_CALL}


async def test_empty_dag_recipe_still_works() -> None:
    recipe = _recipe()
    assert recipe.dag.nodes == []
    assert recipe.dag.edges == []
    llm = _ScriptedLLM(["Trả lời."])
    result = await agent_loop.run_agent_loop(
        recipe,
        session_context=_session(),
        kb_search=_RecordingKbSearch(),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert len(result.events) == 1


def test_source_never_reads_recipe_dag() -> None:
    """Anti-tamper (K8) — grep source, không đọc bằng mắt."""
    from pathlib import Path

    source = Path(agent_loop.__file__).read_text(encoding="utf-8")
    assert "recipe.dag" not in source


# --- max_turns bounds / exhaustion -------------------------------------------


async def test_max_turns_out_of_range_raises_value_error() -> None:
    llm_low = _ScriptedLLM(["never used"])
    with pytest.raises(ValueError):
        await agent_loop.run_agent_loop(
            _recipe(),
            session_context=_session(),
            kb_search=_RecordingKbSearch(),
            llm=llm_low,
            embedding=EmptyEmbedding(),
            trace_writer=_CollectingTraceWriter(),
            question="q",
            max_turns=0,
        )
    assert llm_low.calls == 0

    llm_high = _ScriptedLLM(["never used"])
    with pytest.raises(ValueError):
        await agent_loop.run_agent_loop(
            _recipe(),
            session_context=_session(),
            kb_search=_RecordingKbSearch(),
            llm=llm_high,
            embedding=EmptyEmbedding(),
            trace_writer=_CollectingTraceWriter(),
            question="q",
            max_turns=agent_loop._MAX_TURNS_CEILING + 1,
        )
    assert llm_high.calls == 0

    # Positive teeth against off-by-one: the ceiling itself must still run.
    llm_boundary = _ScriptedLLM(["Trả lời ngay."])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=_RecordingKbSearch(),
        llm=llm_boundary,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
        max_turns=agent_loop._MAX_TURNS_CEILING,
    )
    assert llm_boundary.calls == 1
    assert len(result.events) == 1


async def test_exhaustion_raises_with_partial() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"y"}}',
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    trace = _CollectingTraceWriter()
    with pytest.raises(agent_loop.AgentLoopExhausted) as excinfo:
        await agent_loop.run_agent_loop(
            _recipe(),
            session_context=_session(),
            kb_search=kb,
            llm=llm,
            embedding=EmptyEmbedding(),
            trace_writer=trace,
            question="q",
            max_turns=2,
        )
    exc = excinfo.value
    assert exc.turns == 2
    assert len(exc.partial.events) == 3  # 2*max_turns - 1 (M3)
    assert llm.calls == 2
    assert len(trace.events) == 3


async def test_last_turn_does_not_dispatch() -> None:
    """M3 teeth: the last permitted turn raises BEFORE dispatch — no
    side-effect for a result nobody will ever read."""
    llm = _ScriptedLLM(['TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}'])
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    with pytest.raises(agent_loop.AgentLoopExhausted):
        await agent_loop.run_agent_loop(
            _recipe(),
            session_context=_session(),
            kb_search=kb,
            llm=llm,
            embedding=EmptyEmbedding(),
            trace_writer=_CollectingTraceWriter(),
            question="q",
            max_turns=1,
        )
    assert kb.calls == []


# --- Dispatch details ---------------------------------------------------------


async def test_top_k_normalised() -> None:
    cases: list[tuple[object, int]] = [(9999, 10), (0, 5), (-3, 5), ("abc", 5), (None, 5), (3, 3)]
    for raw_top_k, expected in cases:
        payload = json.dumps({"tool": "kb_search", "params": {"query": "x", "top_k": raw_top_k}})
        llm = _ScriptedLLM([f"TOOL_CALL: {payload}", "Trả lời."])
        kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
        await agent_loop.run_agent_loop(
            _recipe(),
            session_context=_session(),
            kb_search=kb,
            llm=llm,
            embedding=EmptyEmbedding(),
            trace_writer=_CollectingTraceWriter(),
            question="q",
        )
        assert kb.calls[0][3] == expected, f"top_k={raw_top_k!r} expected {expected}, got {kb.calls[0][3]}"


async def test_fenced_flag_when_zero_chunks() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Không có thông tin.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    kb_event = result.events[1]
    assert kb_event.node_type is NodeType.KB_RETRIEVE
    assert kb_event.outputs.get("fenced") is True
    assert kb_event.outputs.get("chunks") == []


async def test_non_whitelisted_tool_propagates() -> None:
    llm = _ScriptedLLM(['TOOL_CALL: {"tool":"shell_exec","params":{}}'])
    with pytest.raises(ValueError, match="tool not in whitelist"):
        await agent_loop.run_agent_loop(
            _recipe(tool_whitelist=["calculator"]),
            session_context=_session(),
            kb_search=_RecordingKbSearch(),
            llm=llm,
            embedding=EmptyEmbedding(),
            trace_writer=_CollectingTraceWriter(),
            question="q",
        )


async def test_malformed_tool_call_becomes_final_answer_and_stops() -> None:
    raw = "TOOL_CALL: {hỏng"
    llm = _ScriptedLLM([raw])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=_RecordingKbSearch(),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert len(result.events) == 1
    out = _first_state_entry(result)
    assert out["signal"] == "final-answer-malformed"
    assert out["answer"] == raw


def test_run_agent_loop_exported_from_package() -> None:
    from studio_engine import run_agent_loop as exported

    assert exported is agent_loop.run_agent_loop


# --- refused semantics (A5) ---------------------------------------------------


async def test_tool_only_answer_is_not_refused() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"calculator","params":{"expression":"3+5"}}',
            "Kết quả là 8.",
        ]
    )
    dispatch = _RecordingDispatch(result={"expression": "3+5", "result": 8})
    result = await agent_loop.run_agent_loop(
        _recipe(tool_whitelist=["calculator"]),
        session_context=_session(),
        kb_search=_RecordingKbSearch(),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
        tool_dispatch=dispatch,
    )
    final = _last_state_entry(result)
    assert final["citations"] == []
    assert final["refused"] is False


async def test_empty_kb_retrieval_answer_is_refused() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Không có thông tin.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    final = _last_state_entry(result)
    assert final["citations"] == []
    assert final["refused"] is True


async def test_no_tool_direct_answer_is_not_refused() -> None:
    """engine#37 (caught by `agentcore-studio-engine#39` review): this used to
    assert `refused is True` here — a direct conversational answer that never
    calls ANY tool, not even `kb_search`, is the exact standalone-chatbot
    shape `agentcore-studio-web#14` shipped (and `interpreter.py`'s
    `has_kb_upstream` fix already corrected for the DAG-walk path). This loop
    is the one actually wired into production (`routes/chat.py`,
    `agentcore-studio-app#48`), so it needed the same correction:
    `used_kb_search` must be False here (kb_search never ran), so `refused`
    must be False too — a turn with no tool call at all never entered the
    fence-refusal branch this flag measures."""
    llm = _ScriptedLLM(["Trả lời chay không dùng tool."])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=_RecordingKbSearch(),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    final = _last_state_entry(result)
    assert final["citations"] == []
    assert final["refused"] is False


# --- Observation cap / pre-fence audit / chunk filtering ----------------------


async def test_observation_truncated_at_cap() -> None:
    long_text = "x" * 10_000
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Trả lời.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1", text=long_text)])
    await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert "…[cắt bớt]" in llm.prompts[1]


async def test_kb_observation_params_are_pre_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    """M7 teeth: `Observation.params` threaded into the next prompt-build call
    must still be the LLM's ORIGINAL (pre-fence) request, even though
    `kb.search` itself only ever sees the session's fenced tenant."""
    captured: list[list[Observation]] = []

    def _capture(*, system_prompt: str, question: str, tool_names: list[str], observations: list[Observation]) -> str:
        captured.append(list(observations))
        return build_agent_prompt(
            system_prompt=system_prompt, question=question, tool_names=tool_names, observations=observations
        )

    monkeypatch.setattr(agent_loop, "build_agent_prompt", _capture)

    payload = json.dumps({"tool": "kb_search", "params": {"query": "q", "tenant_id": str(BOREA_ID)}})
    llm = _ScriptedLLM([f"TOOL_CALL: {payload}", "Trả lời."])
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(tenant_id=ANKOR_ID),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    # kb.search itself only ever saw the SESSION's tenant.
    assert kb.calls[0][1] == ANKOR_ID
    # but the observation threaded into turn 2's prompt-build call still
    # carries the LLM's original (unfenced) request.
    second_call_observations = captured[1]
    assert second_call_observations[0].params.get("tenant_id") == str(BOREA_ID)


async def test_non_kb_result_items_filtered_out_of_trace() -> None:
    llm = _ScriptedLLM(
        [
            'TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}',
            "Trả lời.",
        ]
    )
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1"), "not-a-chunk"])  # type: ignore[list-item]
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    kb_event = result.events[1]
    chunks_out = kb_event.outputs["chunks"]
    assert isinstance(chunks_out, list)
    assert len(chunks_out) == 1
    first_chunk = chunks_out[0]
    assert isinstance(first_chunk, dict)
    assert first_chunk["chunk_id"] == "doc#c1"


# --- ToolCallingFixtureLLM (H3/R2) — drives the tool-call branch from a real,
# non-test-local double -------------------------------------------------------


async def test_tool_calling_fixture_llm_drives_the_loop() -> None:
    llm = ToolCallingFixtureLLM("tool-call-01")
    kb = _RecordingKbSearch(chunks=[_chunk("doc#c1")])
    result = await agent_loop.run_agent_loop(
        _recipe(),
        session_context=_session(),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert [e.node_type for e in result.events] == [NodeType.LLM_STEP, NodeType.KB_RETRIEVE, NodeType.LLM_STEP]


async def test_tool_calling_fixture_llm_fails_loud_when_out_of_responses() -> None:
    # engine#43: `tool-call-01.json` now has 3 responses (a faithfulness-verify
    # verdict was appended as the 3rd) — exhausting it takes 3 successful
    # calls, not 2.
    llm = ToolCallingFixtureLLM("tool-call-01")
    await llm.complete("p1")
    await llm.complete("p2")
    await llm.complete("p3")
    with pytest.raises(FixtureError):
        await llm.complete("p4")
