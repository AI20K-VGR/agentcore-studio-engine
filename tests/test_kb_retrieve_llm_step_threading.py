"""Behavioral test — `kb-retrieve` output threads into `llm-step` input
inside `interpreter.run()` (phase 1, spec AIE-1, plan
`260723-1110-day4-kb-search-wiring-prep`).

DE (`packages/kb/src/studio_kb/search.py::KbSearchService.search`) is still
`NotImplementedError` (Day 4 blocked, see `KbRetrieveExecutor` docstring) —
this test proves the wiring with an internal `FixtureKbSearch` double, not
DE's real impl. `FixtureKbSearch` lives HERE (not `demo_stubs.py`, which is
Day-3 CLI-demo-only scaffolding, phase risk table mitigation M) so it is not
mistaken for `EmptyKbSearch`.

Teeth (`docs/code-standards.md` §4.1): the fixture LLM's recorded answer
text already carries a DIFFERENT bracketed id (`[chunk-001]`, from
`tests/fixtures/llm_step/smoke-01.json`) than the chunk `FixtureKbSearch`
actually returns (`chunk-042`) — so a passthrough regex-only implementation
that never threads `n_kb`'s real output into `n_llm` fails this assertion.
Only real output->input threading makes `chunk-042` (not `chunk-001`) show
up in `n_llm`'s citations.
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
from studio_engine.demo_stubs import EmptyEmbedding, FixtureLLM

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
    proves `LlmStepExecutor` preserves `retrieved_chunks` order/completeness
    in `citations` rather than only handling the single-chunk case."""

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


async def test_llm_step_citation_uses_real_chunk_from_kb_retrieve() -> None:
    """`n_llm`'s citation must carry `chunk-042` — the chunk `n_kb` (via
    `FixtureKbSearch`) actually returned — not `chunk-001`, the id baked
    into the `FixtureLLM("smoke-01")` fixture answer text."""
    result = await interpreter.run(
        _four_node_recipe(),
        kb_search=FixtureKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    kb_output = result.final_state["n_kb"]
    assert isinstance(kb_output, list)
    assert kb_output[0].chunk_id == _REAL_CHUNK_ID

    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == [_REAL_CHUNK_ID]


async def test_llm_step_citations_preserve_order_for_multiple_chunks() -> None:
    """2 retrieved chunks -> citations must carry both `chunk_id`s, in the
    same order `kb-retrieve` returned them — not just the single-chunk case."""
    result = await interpreter.run(
        _four_node_recipe(),
        kb_search=MultiChunkFixtureKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["citations"] == ["chunk-100", "chunk-101"]
