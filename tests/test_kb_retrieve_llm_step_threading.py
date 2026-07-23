"""Behavioral test — `kb-retrieve` output threads into `llm-step` input
inside `interpreter.run()`, and `llm-step`'s citation is GROUNDED: only a
chunk_id that is BOTH (a) actually retrieved by `kb-retrieve` AND (b)
actually bracket-cited in the LLM's answer text ends up in `citations`
(spec AIE-1, plan `260723-1110-day4-kb-search-wiring-prep`, phase 1 +
same-day follow-up fix).

DE (`packages/kb/src/studio_kb/search.py::KbSearchService.search`) is still
`NotImplementedError` (Day 4 blocked, see `KbRetrieveExecutor` docstring) —
this test proves the wiring with test-local `KbSearch`/`LLM` doubles, not
DE's real impl. `FixtureKbSearch`/`MultiChunkFixtureKbSearch` live HERE (not
`demo_stubs.py`, which is Day-3 CLI-demo-only scaffolding, phase risk table
mitigation M) so they are not mistaken for `EmptyKbSearch`.

Follow-up fix context (found via a manual connectivity probe against DE's
real `StaticKbSearch`, which returns `top_k` chunks per query, not just 1):
phase 1's first cut made `citations` = ALL retrieved chunk_ids, unconditionally
— correct with a 1-chunk double, but wrong the moment more than 1 chunk comes
back, since it cites chunks the LLM's answer never actually referenced. The
fix intersects retrieval with the answer's own `[chunk_id]` bracket mentions;
`_AnsweringLLM` gives full control over that bracket text per test so this
is provable without depending on the Day-3 `smoke-01.json` fixture content.
"""

from __future__ import annotations

from studio_contracts import (
    AgentConfig,
    Dag,
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

_TOOL_NAME = "search_docs"
_REAL_CHUNK_ID = "chunk-042"


class FixtureKbSearch:
    """Test-local `KbSearch` double — always returns one real-shaped
    `KbSearchResultItem` (`chunk_id="chunk-042"`), regardless of the query
    args. Distinct from `demo_stubs.EmptyKbSearch` (always `[]`); this phase
    needs a NON-empty result to prove threading, not just pass-through."""

    async def search(
        self,
        query: str,
        tenant: str,
        section_roles: list[str],
        top_k: int,
    ) -> list[KbSearchResultItem]:
        del query, tenant, section_roles, top_k
        return [
            KbSearchResultItem(
                chunk_id=_REAL_CHUNK_ID,
                text="Nhân viên tenant ankor được nghỉ phép năm 12 ngày.",
                score=0.91,
                tenant="ankor",
                section_role="public",
            )
        ]


class MultiChunkFixtureKbSearch:
    """Test-local `KbSearch` double returning 2 chunks in a fixed order —
    proves `LlmStepExecutor` handles more than the single-chunk case, and
    (paired with `_AnsweringLLM`) that it filters to only what the answer
    text actually cites rather than blindly listing every retrieved chunk."""

    async def search(
        self,
        query: str,
        tenant: str,
        section_roles: list[str],
        top_k: int,
    ) -> list[KbSearchResultItem]:
        del query, tenant, section_roles, top_k
        return [
            KbSearchResultItem(
                chunk_id="chunk-100",
                text="Nhân viên tenant ankor được nghỉ phép năm 12 ngày.",
                score=0.91,
                tenant="ankor",
                section_role="public",
            ),
            KbSearchResultItem(
                chunk_id="chunk-101",
                text="Có thể gộp tối đa 5 ngày phép sang năm sau.",
                score=0.85,
                tenant="ankor",
                section_role="public",
            ),
        ]


class _AnsweringLLM:
    """Test-local `LLM` double — replays a caller-supplied answer string
    verbatim, so a test can control exactly which `[chunk_id]` brackets (if
    any) appear in the text, independent of `demo_stubs.FixtureLLM`'s fixed
    `smoke-01.json` content."""

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return self._answer


class _NoOpTraceWriter:
    async def write(self, event: TraceEvent) -> None:
        del event


def _four_node_recipe() -> Recipe:
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    return Recipe(
        agent_id="agent-1",
        tenant="ankor",
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[_TOOL_NAME]),
        dag=Dag(nodes=nodes, edges=[]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def _run(kb_search: object, llm: object) -> interpreter.RunResult:
    return await interpreter.run(
        _four_node_recipe(),
        kb_search=kb_search,  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )


async def test_llm_step_cites_chunk_that_is_both_retrieved_and_referenced() -> None:
    """`n_llm`'s citation must carry `chunk-042` when the answer text
    actually brackets-cites it AND `n_kb` (via `FixtureKbSearch`) actually
    retrieved it — the positive case: threading + grounding both hold."""
    result = await _run(
        kb_search=FixtureKbSearch(),
        llm=_AnsweringLLM("Nhân viên được nghỉ phép năm 12 ngày. [chunk-042]"),
    )

    kb_output = result.final_state["n_kb"]
    assert isinstance(kb_output, list)
    assert kb_output[0].chunk_id == _REAL_CHUNK_ID

    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == [_REAL_CHUNK_ID]
    assert llm_output["refused"] is False


async def test_llm_step_does_not_cite_a_retrieved_chunk_the_answer_never_mentions() -> None:
    """`chunk-042` is retrieved by `n_kb`, but the LLM's answer text never
    brackets-cites it (it brackets an unrelated, un-retrieved id instead) —
    `citations` must be `[]`. Guards the real bug found against DE's real
    `StaticKbSearch`: an implementation that treats "retrieved" as "cited"
    would wrongly return `["chunk-042"]` here."""
    result = await _run(
        kb_search=FixtureKbSearch(),
        llm=_AnsweringLLM("Đây là câu trả lời không trích chunk nào liên quan. [chunk-999]"),
    )

    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == []


async def test_llm_step_citations_filter_to_only_referenced_among_multiple_retrieved() -> None:
    """2 chunks retrieved (`chunk-100`, `chunk-101`), but the answer text
    only brackets-cites `chunk-101` — `citations` must be exactly
    `["chunk-101"]`, NOT both. This is the direct regression test for the
    over-citation bug: `kb-retrieve` returning >1 chunk must not make every
    retrieved chunk show up as cited regardless of what the LLM actually
    used."""
    result = await _run(
        kb_search=MultiChunkFixtureKbSearch(),
        llm=_AnsweringLLM("Có thể gộp tối đa 5 ngày phép sang năm sau. [chunk-101]"),
    )

    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == ["chunk-101"]


async def test_llm_step_citations_preserve_answer_order_for_multiple_cited_chunks() -> None:
    """2 chunks retrieved AND both cited, in the ORDER they appear in the
    answer text (not retrieval order) — proves citations follow what the
    LLM actually wrote, not just a filtered replay of `retrieved_chunks`."""
    result = await _run(
        kb_search=MultiChunkFixtureKbSearch(),
        llm=_AnsweringLLM("Trước tiên [chunk-101], sau đó cũng đúng theo [chunk-100]."),
    )

    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == ["chunk-101", "chunk-100"]
