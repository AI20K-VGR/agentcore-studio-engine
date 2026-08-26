"""Phase 4 (plan `260823-1249-engine33-agent-loop`, engine#33) — REAL security/
business-behavior tests for `run_agent_loop()`: cross-tenant/cross-role fence
teeth, cap termination, and fence-helper parity with `interpreter.run()`.
Every case here follows `docs/code-standards.md` §4.1 "positive teeth": an
EXCLUSION assertion (wrong tenant/role never leaks) paired with an INCLUSION
assertion (right tenant/role still sees real data) in the SAME module, so an
impl that returns `[]` unconditionally cannot pass the exclusion half alone.

Template this module follows (read before touching fixture shapes):
`test_cross_tenant_refusal_audit.py` — did this for `interpreter.run()`, with
`_ScopedCorpusKbSearch` filtering FOR REAL on `(tenant_id, section_roles)`
(`:89-119`) and `_EchoBracketLLM` citing back only the brackets the REAL
rendered prompt actually offered (`:122-139`). No `NotImplementedError`
raises, no `xfail`, no `skip` anywhere in this module (`docs/code-standards.md`
§4.1) — every test here runs for real and can go RED on a broken impl.
"""

from __future__ import annotations

import asyncio
import json
import re
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
from studio_engine import RunResult, agent_loop, interpreter
from studio_engine.demo_stubs import EmptyEmbedding
from test_session_context_tenant_wall import ANKOR_ID, BOREA_ID, _FrozenSessionContext

# Same bracket-citation shape `executors.py::_CITATION_RE` extracts
# (`executors.py:30`) — repo idiom, already copied in
# `test_cross_tenant_refusal_audit.py:73` and `agent_protocol.py`.
_CITATION_RE = re.compile(r"\[([\w#-]+)\]")

# 2-row corpus, roles DELIBERATELY mismatched between tenants AND against the
# sessions used below (`test_cross_tenant_refusal_audit.py:75-82`'s exact
# pattern, same reason): if both rows carried the same role, every negative
# assertion here would stay green even with the fence torn out — ANKOR would
# have no chunk under any role either way. Mismatch is what gives this module
# teeth against a REMOVED fence, not just a wrong-tenant one.
_CORPUS = (
    ("borea-leave-001#c1", BOREA_ID, "public", "Nhân viên Borea báo trước 7 ngày làm việc."),
    ("ankor-expense-001#c2", ANKOR_ID, "finance", "Hạn mức chi 20 triệu đồng."),
)


def _item(chunk_id: str, tenant_id: UUID, section_role: str, text: str) -> KbSearchResultItem:
    return KbSearchResultItem(chunk_id=chunk_id, text=text, score=0.9, tenant_id=tenant_id, section_role=section_role)


class _ScopedCorpusKbSearch:
    """Filters `_CORPUS` for REAL on `(tenant_id, section_roles)` — same fence
    `StaticKbSearch` applies, kept local (no `studio_kb` import,
    `.importlinter`). `self.calls` records every `(tenant_id, section_roles,
    top_k)` actually received."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, list[str], int]] = []

    async def search(
        self, query: str, tenant_id: UUID, section_roles: list[str], top_k: int
    ) -> list[KbSearchResultItem]:
        del query
        self.calls.append((tenant_id, list(section_roles), top_k))
        allowed = set(section_roles)
        return [
            _item(cid, tid, role, text) for cid, tid, role, text in _CORPUS if tid == tenant_id and role in allowed
        ][:top_k]


class _ToolCallingLLM:
    """Turn 1: emits a scripted `TOOL_CALL: {"tool":"kb_search",...}` (params
    overridable — M8 uses this to inject a client-declared tenant/roles/top_k
    the way a prompt-injected chunk might). Turn 2+: cites back exactly the
    `[chunk_id]` brackets the REAL rendered prompt offers (idiom
    `_EchoBracketLLM`, `test_cross_tenant_refusal_audit.py:122-139`) — never a
    hardcoded `refused`/`citations` value, so this measures real loop/executor
    behavior, not the double's opinion of it."""

    def __init__(self, tool_call_params: dict[str, object] | None = None) -> None:
        self._tool_call_params = tool_call_params if tool_call_params is not None else {"query": "q"}
        self.prompts: list[str] = []
        self.calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del kwargs
        self.prompts.append(prompt)
        self.calls += 1
        if self.calls == 1:
            payload = json.dumps({"tool": "kb_search", "params": self._tool_call_params})
            return f"TOOL_CALL: {payload}"
        offered_ids = list(dict.fromkeys(_CITATION_RE.findall(prompt)))
        if not offered_ids:
            return "Không có đoạn trích nào khớp câu hỏi, không thể trả lời."
        cited = " ".join(f"[{cid}]" for cid in offered_ids)
        return f"Theo tài liệu đã truy xuất, câu trả lời có căn cứ tại {cited}."


class _AlternatingToolLLM:
    """Never answers — alternates `kb_search`/`calculator` tool calls forever.
    Used for M7 (cap counts LLM turns, not dispatched actions)."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        self.calls += 1
        if self.calls % 2 == 1:
            return 'TOOL_CALL: {"tool":"kb_search","params":{"query":"q"}}'
        return 'TOOL_CALL: {"tool":"calculator","params":{"expression":"1+1"}}'


class _LoopingLLM:
    """Never answers — always the same `kb_search` tool call. Used for M6."""

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return 'TOOL_CALL: {"tool":"kb_search","params":{"query":"q"}}'


class _RecordingCalculatorDispatch:
    async def dispatch(self, tool: str, params: dict[str, object]) -> object:
        return {"tool": tool, "status": "stub-dispatched"}


class _CollectingTraceWriter:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def write(self, event: TraceEvent) -> None:
        self.events.append(event)


def _loop_recipe(tool_whitelist: list[str] | None = None) -> Recipe:
    """DAG-blind (K8) — always empty `dag`, matching `run_agent_loop`'s own
    contract; only `agent_config`/`kb_binding` fields matter to the loop.

    engine#49 (A4 reversed) — default whitelist includes `kb_search` (this file's tests exercise
    kb_search fence behavior, not whitelist-narrowing)."""
    return Recipe(
        agent_id="agent-loop-fence-test",
        tenant_id=BOREA_ID,  # client-declared; must never be what kb.search sees
        agent_config=AgentConfig(
            system_prompt="", model="", tool_whitelist=tool_whitelist or ["calculator", "kb_search"]
        ),
        dag=Dag(nodes=[], edges=[]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/finance"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


def _dag_recipe() -> Recipe:
    """3-node `kb-retrieve -> llm-step -> end` recipe for `interpreter.run()`,
    used only by M9 (parity) — same shape as
    `test_cross_tenant_refusal_audit.py::_cross_tenant_recipe`."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={"query": "q", "section_roles": ["finance"], "top_k": 5}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    return Recipe(
        agent_id="agent-parity-test",
        tenant_id=BOREA_ID,
        agent_config=AgentConfig(system_prompt="", model="", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=[Edge(from_="n_kb", to="n_llm"), Edge(from_="n_llm", to="n_end")]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/finance"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


def _session(tenant_id: UUID, system_roles: list[str]) -> _FrozenSessionContext:
    return _FrozenSessionContext(tenant_id=tenant_id, user="test-user", system_roles=system_roles)


def _final_answer(result: RunResult) -> dict[str, object]:
    value = result.final_state[list(result.final_state)[-1]]
    assert isinstance(value, dict)
    return value


# --- (a) MONEY-SHOT cross-tenant ---------------------------------------------


async def test_cross_tenant_question_yields_zero_chunks_no_citation_and_refusal() -> None:
    """M1 — session=ANKOR_ID, recipe khai tenant_id=BOREA_ID (client-declared,
    must be ignored). Answer must come from ANKOR's own KB only — Borea's
    corpus row must never leak anywhere."""
    kb = _ScopedCorpusKbSearch()
    llm = _ToolCallingLLM(tool_call_params={"query": "borea?"})
    trace = _CollectingTraceWriter()

    result = await agent_loop.run_agent_loop(
        _loop_recipe(),
        session_context=_session(ANKOR_ID, ["public"]),  # ANKOR has no "public" row in _CORPUS
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=trace,
        question="Hạn mức chi của Borea là bao nhiêu?",
    )

    assert kb.calls[0][0] == ANKOR_ID
    assert kb.calls[0][0] != BOREA_ID
    kb_event = result.events[0] if result.events[0].node_type is NodeType.KB_RETRIEVE else result.events[1]
    assert kb_event.node_type is NodeType.KB_RETRIEVE
    assert kb_event.outputs["chunks"] == []
    assert kb_event.outputs.get("fenced") is True

    trace_blob = json.dumps(result.final_state, default=str) + "".join(e.model_dump_json() for e in result.events)
    assert "borea-leave-001#c1" not in trace_blob

    final = _final_answer(result)
    assert final["citations"] == []
    # A5: still True here — this run uses ONLY kb_search, no non-KB tool.
    assert final["refused"] is True


async def test_same_tenant_question_returns_chunk_and_citation() -> None:
    """M2 — POSITIVE TEETH for M1: without this, a `kb.search` double that
    always returns `[]` would make M1 pass for the wrong reason."""
    kb = _ScopedCorpusKbSearch()
    llm = _ToolCallingLLM(tool_call_params={"query": "ankor expense"})
    trace = _CollectingTraceWriter()

    result = await agent_loop.run_agent_loop(
        _loop_recipe(),
        session_context=_session(ANKOR_ID, ["finance"]),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=trace,
        question="Hạn mức chi của Ankor là bao nhiêu?",
    )

    assert kb.calls[0] == (ANKOR_ID, ["finance"], 5)
    final = _final_answer(result)
    assert final["citations"] == ["ankor-expense-001#c2"]
    assert final["refused"] is False


# --- (b) Section-role scoping -------------------------------------------------


async def test_session_roles_win_over_tool_declared_roles() -> None:
    """M3 — LLM declares `section_roles=["finance"]` on its `TOOL_CALL:`, but
    the SESSION carries `roles=["public"]`; the session must win."""
    kb = _ScopedCorpusKbSearch()
    llm = _ToolCallingLLM(tool_call_params={"query": "q", "section_roles": ["finance"]})

    await agent_loop.run_agent_loop(
        _loop_recipe(),
        session_context=_session(ANKOR_ID, ["public"]),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert kb.calls[0][1] == ["public"]
    assert "finance" not in kb.calls[0][1]


async def test_finance_session_sees_finance_chunk() -> None:
    """M4 — positive teeth for M3."""
    kb = _ScopedCorpusKbSearch()
    llm = _ToolCallingLLM(tool_call_params={"query": "q"})

    result = await agent_loop.run_agent_loop(
        _loop_recipe(),
        session_context=_session(ANKOR_ID, ["finance"]),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    final = _final_answer(result)
    assert "ankor-expense-001#c2" in final["citations"]  # type: ignore[operator]


async def test_malformed_session_roles_deny_all_not_widen() -> None:
    """M5 — a malformed session double (`roles` is a bare string, not a list)
    must fence-deny, never widen via `list("public")` -> 6 garbage roles
    (probe already recorded in `fence.py`'s docstring). The LLM ALSO declares
    a conflicting `section_roles=["finance"]` on its own `TOOL_CALL:` — this
    is what gives the test teeth against the override being dropped
    entirely (M-roles mutation): without a conflicting client-declared
    value to lose to, a dropped override and a correctly-applied one are
    observably identical (both end up `[]` here) and the mutation would go
    undetected."""
    kb = _ScopedCorpusKbSearch()
    llm = _ToolCallingLLM(tool_call_params={"query": "q", "section_roles": ["finance"]})

    await agent_loop.run_agent_loop(
        _loop_recipe(),
        session_context=_FrozenSessionContext(tenant_id=ANKOR_ID, user="u", system_roles="public"),  # type: ignore[arg-type]
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    assert kb.calls[0][1] == []
    assert kb.calls[0][1] != ["p", "u", "b", "l", "i", "c"]


# --- (c) Cap max_turns chặn lặp vô hạn THẬT ----------------------------------


async def test_looping_llm_terminates_at_cap() -> None:
    """M6 — `_LoopingLLM` never answers; the cap must terminate the loop for
    real, not merely be trusted to. Wrapped in `asyncio.wait_for` (no
    `pytest-timeout` in this workspace — stdlib equivalent, same effect):
    if the cap were ever removed, this test FAILS within 30s instead of
    hanging CI indefinitely."""
    kb = _ScopedCorpusKbSearch()
    llm = _LoopingLLM()
    trace = _CollectingTraceWriter()

    async def _run() -> None:
        with pytest.raises(agent_loop.AgentLoopExhausted) as excinfo:
            await agent_loop.run_agent_loop(
                _loop_recipe(),
                session_context=_session(ANKOR_ID, ["public"]),
                kb_search=kb,
                llm=llm,
                embedding=EmptyEmbedding(),
                trace_writer=trace,
                question="q",
                max_turns=3,
            )
        exc = excinfo.value
        assert exc.turns == 3
        assert len(exc.partial.events) == 5  # 2*max_turns - 1 (M3)
        assert len(trace.events) == 5
        assert len(kb.calls) == 2  # turns 1 and 2 only — NOT turn 3 (M3)

    await asyncio.wait_for(_run(), timeout=30)


async def test_cap_is_llm_calls_not_tool_calls() -> None:
    """M7 — the cap counts `llm.complete()` INVOCATIONS (A2), not dispatched
    tool actions: an alternating kb_search/calculator script still exhausts
    at exactly `max_turns` LLM calls even though total dispatched actions is
    1 fewer (the last turn raises before dispatch, M3)."""
    kb = _ScopedCorpusKbSearch()
    dispatch = _RecordingCalculatorDispatch()
    llm = _AlternatingToolLLM()

    with pytest.raises(agent_loop.AgentLoopExhausted) as excinfo:
        await agent_loop.run_agent_loop(
            _loop_recipe(tool_whitelist=["calculator", "kb_search"]),
            session_context=_session(ANKOR_ID, ["public"]),
            kb_search=kb,
            llm=llm,
            embedding=EmptyEmbedding(),
            trace_writer=_CollectingTraceWriter(),
            question="q",
            tool_dispatch=dispatch,
            max_turns=4,
        )
    assert llm.calls == 4
    assert excinfo.value.turns == 4
    # Total dispatched actions (kb + calculator) is 3, not 4 — the 4th turn's
    # tool call never dispatches (M3). This is the "not dispatch count" teeth.
    assert len(kb.calls) == 2  # turns 1, 3 (odd)


# --- (d) Fence không bị bỏ qua khi đi qua loop mới ---------------------------


async def test_llm_declared_tenant_and_roles_are_overridden() -> None:
    """M8 (INJECTION TEETH) — LLM emits a `TOOL_CALL:` declaring
    `tenant_id=<BOREA>`, `section_roles=["finance"]`, `top_k=9999` (simulating
    a prompt-injected chunk instructing the model to widen its own scope).
    `kb.search` must receive ONLY the session's values, clamped `top_k`."""
    kb = _ScopedCorpusKbSearch()
    llm = _ToolCallingLLM(
        tool_call_params={
            "query": "q",
            "tenant_id": str(BOREA_ID),
            "section_roles": ["finance"],
            "top_k": 9999,
        }
    )

    await agent_loop.run_agent_loop(
        _loop_recipe(),
        session_context=_session(ANKOR_ID, ["public"]),
        kb_search=kb,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )
    tenant_id, section_roles, top_k = kb.calls[0]
    assert tenant_id == ANKOR_ID
    assert tenant_id != BOREA_ID
    assert section_roles == ["public"]
    assert top_k == 10


async def test_loop_and_run_agree_on_fence_inputs() -> None:
    """M9 (parity) — same session + same fence semantics, `interpreter.run()`
    (DAG path) and `run_agent_loop()` (loop path) must hand `kb.search` the
    SAME `(tenant_id, section_roles)` pair. Chống '2 bản fence trôi nhau' ở
    tầng HÀNH VI, không chỉ ở tầng source (đó là M10)."""
    session = _session(ANKOR_ID, ["finance"])

    kb_dag = _ScopedCorpusKbSearch()

    class _EchoBracketLLM:
        async def complete(self, prompt: str, **kwargs: object) -> str:
            del kwargs
            offered = list(dict.fromkeys(_CITATION_RE.findall(prompt)))
            if not offered:
                return "Không có thông tin."
            return " ".join(f"[{cid}]" for cid in offered)

    await interpreter.run(
        _dag_recipe(),
        session_context=session,
        kb_search=kb_dag,
        llm=_EchoBracketLLM(),
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
    )

    kb_loop = _ScopedCorpusKbSearch()
    llm_loop = _ToolCallingLLM(tool_call_params={"query": "q"})
    await agent_loop.run_agent_loop(
        _loop_recipe(),
        session_context=session,
        kb_search=kb_loop,
        llm=llm_loop,
        embedding=EmptyEmbedding(),
        trace_writer=_CollectingTraceWriter(),
        question="q",
    )

    assert kb_dag.calls[0][0] == kb_loop.calls[0][0]  # tenant_id
    assert kb_dag.calls[0][1] == kb_loop.calls[0][1]  # section_roles


def test_both_paths_call_the_shared_fence_helper() -> None:
    """M10 (ANTI-TAMPER, idiom `packages/kb/tests/test_leak_meta.py`) — grep
    source: both `interpreter.py` and `agent_loop.py` must literally call
    `fenced_kb_params(`. Always green under a correct impl, never `xfail`."""
    from pathlib import Path

    interpreter_source = Path(interpreter.__file__).read_text(encoding="utf-8")
    agent_loop_source = Path(agent_loop.__file__).read_text(encoding="utf-8")
    assert "fenced_kb_params(" in interpreter_source
    assert "fenced_kb_params(" in agent_loop_source
