"""Phase 2 (plan `260811-1121-d17-aie1-section-roles-server-resolve`) — end-to-end
DoD #3: a cross-tenant question (the answer lives in a DIFFERENT tenant's KB)
must yield `kb.search() == []` -> the walk CONTINUES to `llm-step` (no raise,
no early stop, `kb-search.v0.md` §6.1) -> `refused is True` -> and a full
3-`TraceEvent` audit trail, every event carrying the SESSION's `tenant_id`,
`kb-retrieve`'s own event carrying `outputs["chunks"] == []`.

**This is a PIN, not red->green.** The behavior under test already exists:
`test_refusal_from_grounding.py:111-117` pins "empty retrieval -> refused"
(SC-05's shape) and `test_session_context_tenant_wall.py:193-211` pins
"every `TraceEvent.tenant_id` comes from the session, not the recipe" (D8
INV-1). What was missing was ONE test wiring both of those into the actual
cross-tenant scenario (issue #111), against a `KbSearch` double that FILTERS
FOR REAL on `(tenant_id, section_roles)` instead of returning a fixed `[]` —
a fixed-`[]` double would make every exclusion assertion here vacuously true
even if the real fence were broken. `docs/code-standards.md` §4.1 calls this
"positive teeth": a fence test needs a case that PROVES the double can also
return non-empty (`test_same_tenant_question_...` below), or a leaky
production bug reads identically to a correct one under this suite.

Deliberately NOT a copy of `test_refusal_from_grounding.py`: that module's
`_MeasuredKbSearch` returns a FIXED chunk-id list per case and never asserts
on `TraceEvent`/tenant — it pins the `refused` DERIVATION in isolation. This
module's double filters on `(tenant_id, section_roles)` like the real
`StaticKbSearch` (`packages/kb/src/studio_kb/static_search.py`) does, and
asserts the full audit trail — it pins the cross-tenant SCENARIO end-to-end.

Recipe `tenant_id=BOREA_ID` (client-declared) and the `kb-retrieve` node's
own `section_roles=["finance"]` are BOTH deliberately made to disagree with
the session's `roles=["public"]` used across every test below — this is
LOAD-BEARING, not incidental tidiness. If the node's declared
`section_roles` were also `["public"]`, all 3 tests here would stay green
even with Phase 1's `interpreter.py` override reverted outright: ANKOR has
no `public` chunk in `_CORPUS` either way, so the money-shot would read
`refused=True` whether or not the session's roles actually won. Keeping the
values apart is what gives `test_cross_tenant_question_yields_empty_retrieval_refusal_and_audit_trail`
teeth against Phase 1's OWN fix (`section_roles` server-resolution), not just
against the older Day 8 tenant-wall — see `M-roles` in this phase's
teeth-check for the mutation that proves it.

Local double only (QĐ-6): no `studio_kb` import, no `pytest.importorskip` —
a skip-wrapped import would silently SKIP this whole module inside the
`agentcore-studio-engine` quadrant (`.importlinter` forbids `studio_engine`
importing `studio_kb`), exactly the trap
`test_golden_batch_correctness.py:12-17` documents avoiding.
"""

from __future__ import annotations

import json
import re
from uuid import UUID

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
from studio_engine.demo_stubs import EmptyEmbedding
from test_session_context_tenant_wall import ANKOR_ID, BOREA_ID, _FrozenSessionContext

# Same bracket-citation shape `executors.py::_CITATION_RE` extracts from the
# rendered prompt (`build_prompt`, `executors.py:54-76`) — reused idiom,
# `scripts/run_golden_batch.py:69`.
_CITATION_RE = re.compile(r"\[([\w#-]+)\]")

# (chunk_id, tenant_id, section_role, text) — a 2-row corpus, one row per
# tenant, that is ALSO sized to go red under `M-roles` (see module docstring):
# the ankor row's role is "finance", matching the node's declared
# `section_roles=["finance"]` rather than the session's `["public"]`.
_CORPUS = (
    ("borea-leave-001#c1", BOREA_ID, "public", "Nhân viên Borea báo trước 7 ngày làm việc."),
    ("ankor-expense-001#c2", ANKOR_ID, "finance", "Hạn mức chi 20 triệu đồng."),
)


def _item(chunk_id: str, tenant_id: UUID, section_role: str, text: str) -> KbSearchResultItem:
    return KbSearchResultItem(chunk_id=chunk_id, text=text, score=0.9, tenant_id=tenant_id, section_role=section_role)


class _ScopedCorpusKbSearch:
    """`KbSearch` double that filters `_CORPUS` for REAL on
    `(tenant_id, section_roles)` — the same fence `StaticKbSearch`
    (`packages/kb/src/studio_kb/static_search.py`) applies — but kept local
    (no `studio_kb` import, see module docstring). Does NOT filter on
    relevance: `kb.search` fences SCOPE, not relevance
    (`kb-search.v0.md` §6.1a) — a relevance ranker belongs to a different
    double, not this one.

    `self.calls` records every `(tenant_id, section_roles)` pair actually
    handed to `search()` — the negative assertions below read this to prove
    `BOREA_ID` (the recipe's client-declared tenant) never reaches the
    retrieval boundary, only the session's `ANKOR_ID` does.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, list[str]]] = []

    async def search(
        self,
        query: str,
        tenant_id: UUID,
        section_roles: list[str],
        top_k: int,
    ) -> list[KbSearchResultItem]:
        del query
        self.calls.append((tenant_id, list(section_roles)))
        allowed = set(section_roles)
        return [
            _item(cid, tid, role, text) for cid, tid, role, text in _CORPUS if tid == tenant_id and role in allowed
        ][:top_k]


class _EchoBracketLLM:
    """`LLM` double that cites back exactly the `[chunk_id]` brackets the
    REAL rendered prompt offers (extracted with `_CITATION_RE`, same idiom
    `scripts/run_golden_batch.py::_GoldenAwareLLM` uses) — never a hardcoded
    `refused`/`citations` value. When `build_prompt` (`executors.py:51,74`)
    has nothing to offer it renders `_NO_EXCERPT` instead, which contains no
    brackets at all: this double then answers with no citation, and
    `LlmStepExecutor` derives `citations=[]`/`refused=True` from that through
    its OWN real logic (`executors.py:280-293`), not from anything this
    double decided directly. This is the condition that makes the test
    measure real interpreter/executor behavior instead of the double's
    opinion of it.

    `self.prompts` records every prompt actually received, so a test can
    assert on prompt CONTENT (e.g. the `_NO_EXCERPT` marker) — proof
    `llm-step` really was handed an empty `retrieved_chunks`, not merely
    that its output happens to look that way.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del kwargs
        self.prompts.append(prompt)
        offered_ids = list(dict.fromkeys(_CITATION_RE.findall(prompt)))
        if not offered_ids:
            return "Không có đoạn trích nào khớp câu hỏi, không thể trả lời."
        cited = " ".join(f"[{cid}]" for cid in offered_ids)
        return f"Theo tài liệu đã truy xuất, câu trả lời có căn cứ tại {cited}."


class _NoOpTraceWriter:
    async def write(self, event: TraceEvent) -> None:
        del event


def _cross_tenant_recipe() -> Recipe:
    """`kb-retrieve -> llm-step -> end`. `tenant_id=BOREA_ID` is the
    CLIENT-declared value on the recipe document (same role
    `_kb_then_end_recipe`'s `recipe_tenant_id` plays in
    `test_session_context_tenant_wall.py`) — every test below pairs this
    with a session carrying `ANKOR_ID` or `BOREA_ID` to prove which one
    actually reaches the retrieval boundary. The node's own
    `section_roles=["finance"]` is deliberately NOT `["public"]` — see the
    module docstring "load-bearing" note."""
    nodes = [
        Node(
            id="n_kb",
            type=NodeType.KB_RETRIEVE,
            params={"query": "Hạn mức chi của Borea là bao nhiêu?", "section_roles": ["finance"], "top_k": 5},
        ),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    return Recipe(
        agent_id="agent-cross-tenant",
        tenant_id=BOREA_ID,
        agent_config=AgentConfig(system_prompt="x", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=[Edge(from_="n_kb", to="n_llm"), Edge(from_="n_llm", to="n_end")]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/finance"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def test_cross_tenant_question_yields_empty_retrieval_refusal_and_audit_trail() -> None:
    """Money-shot (issue #111). Session = `ANKOR_ID` + `roles=["public"]` —
    the recipe claims `BOREA_ID` and the node claims `section_roles=["finance"]`,
    both of which must be ignored in favor of the session's values
    (Day 8 INV-1 / Day 17 T6). Neither combination matches any `_CORPUS` row
    for `ANKOR_ID`, so retrieval comes back empty, the walk still runs all 3
    nodes to `end`, and `llm-step` reads that emptiness as a refusal — every
    emitted `TraceEvent` carries the SESSION's tenant, never the recipe's."""
    kb_search = _ScopedCorpusKbSearch()
    llm = _EchoBracketLLM()
    recipe = _cross_tenant_recipe()

    result = await interpreter.run(
        recipe,
        session_context=_FrozenSessionContext(tenant_id=ANKOR_ID, user="test-user", roles=["public"]),
        kb_search=kb_search,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    # Dương: retrieval ran, the walk reached `end`, and the refusal + audit
    # trail are all shaped as the DoD demands.
    assert set(result.final_state) == {"n_kb", "n_llm", "n_end"}
    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["refused"] is True
    assert llm_output["citations"] == []
    assert len(result.events) == 3
    assert [event.node_type.value for event in result.events] == ["kb-retrieve", "llm-step", "end"]
    for event in result.events:
        assert event.tenant_id == ANKOR_ID
    kb_event = result.events[0]
    assert kb_event.node_type is NodeType.KB_RETRIEVE
    assert kb_event.outputs["chunks"] == []

    # Âm: the recipe's client-declared tenant never reaches the retrieval
    # boundary, never shows up on any audit event, and no Borea chunk leaks
    # anywhere in the run's outputs — checked across BOTH `final_state` and
    # the full trace, not `final_state` alone.
    assert kb_search.calls[0][0] != BOREA_ID
    assert not any(event.tenant_id == BOREA_ID for event in result.events)
    trace_blob = json.dumps(result.final_state, default=str) + "".join(
        event.model_dump_json() for event in result.events
    )
    assert "borea-leave-001#c1" not in trace_blob


async def test_same_tenant_question_does_retrieve_and_does_not_refuse() -> None:
    """Positive teeth for `_ScopedCorpusKbSearch` itself (docs/code-standards.md
    §4.1's "positive teeth" pattern, `packages/kb/tests/test_leak.py:50-51`).
    Same recipe, same double class — only the SESSION changes, to `BOREA_ID` +
    `roles=["public"]`, which now matches the corpus's own Borea/public row.
    Without this test, a double that always returns `[]` would make the
    money-shot above pass for the wrong reason (nothing ever retrieved,
    fence never actually exercised)."""
    kb_search = _ScopedCorpusKbSearch()
    llm = _EchoBracketLLM()
    recipe = _cross_tenant_recipe()

    result = await interpreter.run(
        recipe,
        session_context=_FrozenSessionContext(tenant_id=BOREA_ID, user="test-user", roles=["public"]),
        kb_search=kb_search,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    # Dương: retrieval is genuinely non-empty and the answer is grounded.
    kb_output = result.final_state["n_kb"]
    assert isinstance(kb_output, list)
    assert kb_output != []
    assert [item.chunk_id for item in kb_output] == ["borea-leave-001#c1"]
    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == ["borea-leave-001#c1"]
    assert llm_output["refused"] is False
    assert kb_search.calls[0] == (BOREA_ID, ["public"])

    # Âm: the ankor/finance row still must not leak into a borea-scoped call.
    assert "ankor-expense-001#c2" not in [item.chunk_id for item in kb_output]


async def test_empty_retrieval_does_not_raise_and_llm_step_receives_empty_chunks() -> None:
    """Pins §6.1's contract directly: an empty `kb-retrieve` result is a
    VALID input the walk passes straight through to `llm-step` — not an
    error branch. Reuses the money-shot's empty-retrieval session
    (`ANKOR_ID` + `["public"]`) but asserts on the MECHANISM (no raise,
    `final_state["n_kb"] == []`, the `_NO_EXCERPT` marker actually present in
    the prompt `llm-step` received) rather than the refusal outcome the first
    test already covers."""
    kb_search = _ScopedCorpusKbSearch()
    llm = _EchoBracketLLM()
    recipe = _cross_tenant_recipe()

    result = await interpreter.run(
        recipe,
        session_context=_FrozenSessionContext(tenant_id=ANKOR_ID, user="test-user", roles=["public"]),
        kb_search=kb_search,
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    # Dương: the walk did not raise (we are past `await interpreter.run(...)`
    # already), `kb-retrieve`'s own output is `[]`, and `llm-step` really was
    # handed 0 chunks — proven via the rendered prompt it received, not just
    # the shape of what it returned.
    assert result.final_state["n_kb"] == []
    assert len(llm.prompts) == 1
    assert "(không có đoạn trích nào được truy xuất)" in llm.prompts[0]

    # Âm: no bracket-shaped citation token leaked into the prompt despite 0
    # retrieved chunks (there is nothing to echo back).
    assert _CITATION_RE.findall(llm.prompts[0]) == []
