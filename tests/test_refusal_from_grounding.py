"""Day 6 spine fix — `refused` is derived from GROUNDING, not from a magic token.

Three signals have been tried for `refused`; the first two are measurably
wrong against the real golden set (`packages/kb/golden/smoke-5.yaml`):

1. `refused = not retrieved_chunks` (commit `71caeb8`, and still what
   `evalhub/docs/scorecard-v0.md` §2.7 line 174 records as agreed). It claims
   "SC-04 chéo-tenant: tenant chặn → rỗng → refused=True". Measured against
   DE's `StaticKbSearch` on the real Callisto corpus, SC-04 retrieval is NOT
   empty — it returns 3 same-tenant chunks
   (`ankor-leave-001#c2`, `ankor-expense-001#c5`, `ankor-leave-001#c1`),
   because the fence correctly drops every Borea chunk while the token-overlap
   ranker still matches ankor docs on the common words in "Hạn mức **chi của**
   Borea **là bao nhiêu**?". So SC-04 would score `refused=False` → refusal
   branch FAIL. §2.7's premise is false, not merely stale.

2. `refused = (answer.strip() == "[[REFUSED]]")`. The sentinel exists nowhere
   outside this package — no prompt in the workspace asks a model to emit it —
   so `refused` is permanently False on any real answer, and "Xin lỗi.
   [[REFUSED]]" already misses the exact match. SC-04 and SC-05 both FAIL.

3. `refused = not citations` (this module). A citation survives only if it is
   BOTH bracket-cited by the answer AND present in `retrieved_chunks`, so
   "grounded nothing" is exactly "could not answer from what it was given".
   It stays STRUCTURAL — no NLP guessing at prose, which
   `studio_evalhub.agent_runner.AgentAnswer.refused` forbids — and needs no
   token nobody produces.

The seam is unchanged: `final_state[<llm node>]["refused"]` still carries the
flag, so the `studio_app` adapter and `studio_evalhub` need no edit. Only the
derivation moved, which `scorecard-v0.md` §2.7 must be updated to record.

Known limitation, stated rather than hidden: a model that answers correctly but
forgets the brackets is scored as a refusal. That is the same signal
`citation_accuracy` already rests on, so it fails one way, not two.
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
from studio_engine.demo_stubs import EmptyEmbedding
from studio_engine.executors import LlmStepExecutor
from test_session_context_tenant_wall import default_session_context

ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")


def _chunk(chunk_id: str, text: str = "nội dung", tenant_id: UUID = ANKOR_ID) -> KbSearchResultItem:
    return KbSearchResultItem(chunk_id=chunk_id, text=text, score=0.9, tenant_id=tenant_id, section_role="public")


class _ReplayLLM:
    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return self.answer

    def __init__(self, answer: str) -> None:
        self.answer = answer


class _NoOpTraceWriter:
    async def write(self, event: TraceEvent) -> None:
        del event


async def _llm_output(answer: str, chunks: list[KbSearchResultItem]) -> dict[str, object]:
    """Simulates a walk where `kb-retrieve` DID run upstream (`has_kb_upstream:
    True` — engine#37): every case in this module is SC-01..SC-05's shape,
    which all assume a real retrieval step happened. The "no kb-retrieve at
    all" shape gets its own test below, through the real walk instead of this
    direct-executor helper."""
    node = Node(
        id="n2",
        type=NodeType.LLM_STEP,
        params={"query": "q", "retrieved_chunks": chunks, "has_kb_upstream": True},
    )
    result = await LlmStepExecutor(_ReplayLLM(answer), EmptyEmbedding()).execute(node)
    assert isinstance(result, dict)
    return result


async def test_refused_false_when_answer_cites_a_retrieved_chunk() -> None:
    """The answerable path (SC-01/02/03): the answer grounds a real chunk, so
    the agent did not refuse."""
    out = await _llm_output("Báo trước 3 ngày làm việc. [ankor-leave-001#c1]", [_chunk("ankor-leave-001#c1")])

    assert out["citations"] == ["ankor-leave-001#c1"]
    assert out["refused"] is False


async def test_refused_true_when_chunks_were_retrieved_but_none_answered() -> None:
    """SC-04's real shape, and the case both earlier signals get wrong: the
    fence dropped every Borea chunk, retrieval still handed back 3 unrelated
    ankor chunks, and the model correctly says it has no information and cites
    nothing. `not retrieved_chunks` would read False here (3 chunks present)."""
    out = await _llm_output(
        "Các đoạn trích không có thông tin về hạn mức chi của Borea.",
        [_chunk("ankor-leave-001#c2"), _chunk("ankor-expense-001#c5"), _chunk("ankor-leave-001#c1")],
    )

    assert out["citations"] == []
    assert out["refused"] is True


async def test_refused_true_when_nothing_was_retrieved() -> None:
    """SC-05's shape: the role fence emptied retrieval outright. Nothing can be
    grounded, so nothing can be cited, so it is a refusal."""
    out = await _llm_output("Không có thông tin trong phạm vi được phép.", [])

    assert out["citations"] == []
    assert out["refused"] is True


async def test_refused_false_when_no_kb_retrieve_upstream_at_all() -> None:
    """engine#37 repro: a walk with NO `kb-retrieve` node at all
    (`agentcore-studio-web#14`'s standalone "Chatbot LLM Trực Tiếp" mode,
    `llm-step -> end`) reaches `LlmStepExecutor` with `retrieved_chunks == []`
    for the SAME reason `test_refused_true_when_nothing_was_retrieved` above
    does — but the two must NOT read the same: that test's `kb-retrieve` DID
    run and legitimately found nothing (a fence case); this walk never had a
    `kb-retrieve` to run in the first place, so there is no fence-refusal
    branch to fail. Real answer, empty citations (nothing was ever offered to
    cite), and `refused` must be False — through the real walk
    (`interpreter.run()`), not the direct-executor helper, so `has_kb_upstream`
    is genuinely walk-derived rather than hand-set."""
    nodes = [
        Node(id="n1", type=NodeType.LLM_STEP, params={"prompt": "Xin chào, hôm nay thời tiết thế nào?"}),
        Node(id="n2", type=NodeType.END, params={}),
    ]
    recipe = Recipe(
        agent_id="agent-standalone-chat",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(system_prompt="x", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=[Edge(from_="n1", to="n2")]),
        # Required by the `Recipe` schema even though this DAG has no
        # `kb-retrieve` node to use it — never dereferenced on this walk.
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="engine37-standalone-llm-step",
        scorecard_threshold=ScorecardThreshold(success=0.9, citation_accuracy=0.95),
    )

    result = await interpreter.run(
        recipe,
        session_context=default_session_context(),
        kb_search=_MeasuredKbSearch([]),
        llm=_ReplayLLM("Hôm nay Hà Nội nắng, khoảng 30 độ."),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    llm_output = result.final_state["n1"]
    assert isinstance(llm_output, dict)
    assert llm_output["answer"] == "Hôm nay Hà Nội nắng, khoảng 30 độ."
    assert llm_output["citations"] == []
    assert llm_output["refused"] is False


async def test_refused_true_when_the_only_bracket_is_ungrounded() -> None:
    """The false-GREEN case the sentinel design was reaching for: retrieval was
    fenced to empty and the model fabricated an answer WITH a confident-looking
    bracket. The grounding filter drops the fabricated id, so `citations` is
    empty and the run is still a refusal — a hallucination cannot buy its way
    out of the refusal branch."""
    out = await _llm_output("Hạn mức của Borea là 77 triệu. [borea-expense-001#c1]", [])

    assert out["citations"] == []
    assert out["refused"] is True


async def test_refused_true_when_bracket_names_a_chunk_from_another_tenant() -> None:
    """Cross-tenant leak attempt: the model brackets a Borea id that retrieval
    never returned. It is not in `retrieved_chunks`, so it is not a citation —
    and `no_leak` on the evalhub side therefore has nothing to fail on."""
    out = await _llm_output("Theo tài liệu Borea. [borea-expense-001#c1]", [_chunk("ankor-leave-001#c1")])

    assert out["citations"] == []
    assert out["refused"] is True


# ── The 5 golden cases, end-to-end through the walk ──────────────────────────
# `retrieved` per case is MEASURED from DE's `StaticKbSearch` over the real
# Callisto corpus, not assumed — that measurement is what disproved §2.7's
# SC-04 claim. The engine cannot import `studio_kb` (`.importlinter` quadrant
# fence), so the measurement is replayed here as a double.
#
# Honest scope: this pins the ENGINE's derivation given a well-behaved model.
# It does not prove a real LLM refuses on SC-04 — that is what the fixture
# recordings and the smoke-eval on the live wiring are for.

_GOLDEN = [
    pytest.param(
        ["ankor-leave-001#c1", "ankor-leave-001#c3"],
        "Báo trước 3 ngày làm việc. [ankor-leave-001#c1]",
        False,
        id="SC-01-same-tenant-same-role",
    ),
    pytest.param(
        ["borea-leave-001#c1", "borea-leave-001#c3"],
        "Báo trước 7 ngày làm việc. [borea-leave-001#c1]",
        False,
        id="SC-02-other-tenant-own-kb",
    ),
    pytest.param(
        ["ankor-expense-001#c2"],
        "Tối đa 20 triệu đồng. [ankor-expense-001#c2]",
        False,
        id="SC-03-role-scoped-chunk",
    ),
    pytest.param(
        ["ankor-leave-001#c2", "ankor-expense-001#c5", "ankor-leave-001#c1"],
        "Không có thông tin về Borea.",
        True,
        id="SC-04-cross-tenant-retrieval-NOT-empty",
    ),
    pytest.param(
        [],
        "Không có thông tin trong phạm vi được phép.",
        True,
        id="SC-05-cross-role-retrieval-empty",
    ),
]


class _MeasuredKbSearch:
    def __init__(self, chunk_ids: list[str]) -> None:
        self._chunk_ids = chunk_ids

    async def search(
        self, query: str, tenant_id: UUID, section_roles: list[str], top_k: int
    ) -> list[KbSearchResultItem]:
        del query, section_roles, top_k
        return [_chunk(cid, tenant_id=tenant_id) for cid in self._chunk_ids]


@pytest.mark.parametrize(("retrieved", "answer", "expect_refused"), _GOLDEN)
async def test_golden_case_refusal_flag_matches_its_label(
    retrieved: list[str], answer: str, expect_refused: bool
) -> None:
    """All 5 smoke cases come back with the refusal flag their label demands —
    the whole point of the derivation change. Under `not retrieved_chunks`
    SC-04 fails; under the sentinel SC-04 and SC-05 both fail."""
    nodes = [
        Node(id="n1", type=NodeType.KB_RETRIEVE, params={"query": "q", "section_roles": ["public"], "top_k": 3}),
        Node(id="n2", type=NodeType.LLM_STEP, params={"temperature": 0.0}),
        Node(id="n3", type=NodeType.END, params={}),
    ]
    recipe = Recipe(
        agent_id="agent-golden",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(system_prompt="x", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=[Edge(from_="n1", to="n2"), Edge(from_="n2", to="n3")]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="callisto-smoke-5-v0",
        scorecard_threshold=ScorecardThreshold(success=0.9, citation_accuracy=0.95),
    )

    result = await interpreter.run(
        recipe,
        session_context=default_session_context(),
        kb_search=_MeasuredKbSearch(retrieved),
        llm=_ReplayLLM(answer),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    llm_output = result.final_state["n2"]
    assert isinstance(llm_output, dict)
    assert llm_output["refused"] is expect_refused
