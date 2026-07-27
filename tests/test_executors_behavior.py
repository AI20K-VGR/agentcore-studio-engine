"""Behavioral tests for the 4 filled node-executor bodies (phase 1, spec
AIE-1, plan `260722-0956-day3-interpreter-3node`).

Real teeth per `docs/code-standards.md` §4.1: every assertion below pins a
concrete stub-shaped value (not a bare `pytest.raises(NotImplementedError)`)
— that form is reserved for the 2 still-unfilled executors
(`test_condition_hitl_still_not_implemented`), which is the ONLY test here
allowed to use it (scope-fence: Condition/Hitl stay out of Day 3 scope).
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from studio_contracts import KbSearchResultItem, Node, NodeType, Tokens
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM, WhitelistToolDispatch
from studio_engine.executors import (
    REFUSAL_SENTINEL,
    ConditionExecutor,
    EndExecutor,
    HitlPauseExecutor,
    KbRetrieveExecutor,
    LlmStepExecutor,
    ToolCallExecutor,
)

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "llm_step" / "smoke-01.json"
# Team-wide canonical UUID for tenant "ankor" — same value as
# packages/workbench/tests/test_wiring_d4.py:14 and
# apps/studio/tests/test_trace_writer.py:14.
ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")


class _HashChunkIdLLM:
    """Test-local `LLM` double replaying a fixed answer that cites a
    real-shaped DE chunk_id (`{doc_id}#c{n}`, `packages/kb/docs/
    callisto-doc-schema.md:209`) — `FixtureLLM`'s `smoke-01.json` only ever
    used a synthetic hyphen-only id (`chunk-001`), which never exercised the
    `#` character in `_CITATION_RE`."""

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return "Nhân viên báo trước 3 ngày làm việc. [ankor-leave-001#c1]"


class _RefusingLLM:
    """Test `LLM` double whose WHOLE answer is exactly `REFUSAL_SENTINEL` — the
    agent's DECLARED refusal (what a real gateway emits when it declines). The
    executor must read this by exact-match, never by NLP-guessing the prose and
    never by inferring from chunk presence."""

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return REFUSAL_SENTINEL


async def test_kb_retrieve_returns_empty_stub() -> None:
    """`EmptyKbSearch` stub always returns `[]` — executor must pass it
    through unchanged (fence-EXECUTOR: never widen/re-derive on this side)."""
    node = Node(
        id="n1",
        type=NodeType.KB_RETRIEVE,
        params={"query": "leave policy", "tenant_id": ANKOR_ID, "section_roles": ["public"], "top_k": 5},
    )
    result = await KbRetrieveExecutor(EmptyKbSearch()).execute(node)
    assert result == []


async def test_llm_step_replays_fixture_answer() -> None:
    """`FixtureLLM("smoke-01")` replays `tests/fixtures/llm_step/smoke-01.json`.
    `citations` must be a REAL regex extraction of `[chunk-001]` out of the
    response text — an implementation that doesn't actually extract must FAIL
    this assertion, not silently pass (`answer` alone is not enough teeth)."""
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    node = Node(
        id="n2",
        type=NodeType.LLM_STEP,
        params={
            "prompt": fixture["request"]["prompt"],
            "kwargs": fixture["request"]["kwargs"],
            # `chunk-001` is BOTH retrieved (here) AND bracket-cited by the
            # fixture answer, so the grounded-citation rule keeps it. Grounding
            # is now REQUIRED: with empty `retrieved_chunks` there is nothing to
            # cite against and `citations` would be `[]` (see
            # `test_llm_step_empty_retrieved_chunks_yields_no_ungrounded_citation`).
            "retrieved_chunks": [
                KbSearchResultItem(
                    chunk_id="chunk-001",
                    text="Nhân viên tenant ankor được nghỉ phép năm 12 ngày.",
                    score=0.9,
                    tenant_id=ANKOR_ID,
                    section_role="public",
                )
            ],
        },
    )
    result = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)
    assert isinstance(result, dict)
    assert result["answer"] == fixture["response"]
    assert result["tokens"] == Tokens(prompt=0, completion=0)
    assert result["citations"] == ["chunk-001"]
    # A real fixture answer (not the sentinel) is content, not a refusal → False.
    assert result["refused"] is False


async def test_llm_step_refused_true_when_agent_declares_sentinel() -> None:
    """`refused` reads the agent's DECLARED signal — an answer that is exactly
    `REFUSAL_SENTINEL` — NOT `not retrieved_chunks` (the 71caeb8 bug that
    conflated "retrieval empty" with "agent refused"). Non-empty
    `retrieved_chunks` are present ON PURPOSE here: they prove the flag ignores
    chunk presence — the agent refused even though grounding was available.
    This fixes the false-RED where a correct refusal on a non-empty walk was
    scored as not-refused. A declared refusal carries NO citations."""
    node = Node(
        id="n2r",
        type=NodeType.LLM_STEP,
        params={
            "prompt": "x",
            "kwargs": {},
            "retrieved_chunks": [
                KbSearchResultItem(
                    chunk_id="ankor-leave-001#c1",
                    text="Báo trước tối thiểu 3 ngày làm việc.",
                    score=0.9,
                    tenant_id=ANKOR_ID,
                    section_role="public",
                )
            ],
        },
    )
    result = await LlmStepExecutor(_RefusingLLM(), EmptyEmbedding()).execute(node)
    assert isinstance(result, dict)
    assert result["refused"] is True
    assert result["citations"] == []


async def test_llm_step_not_refused_when_agent_answers_despite_no_chunks() -> None:
    """The false-GREEN the 71caeb8 structural signal let through: retrieval
    returns nothing, but the agent ANSWERS anyway (hallucinates) instead of
    declaring the sentinel. `refused` MUST be `False` so the evalhub refusal
    branch (SC-04/SC-05, `packages/kb/golden/smoke-5.yaml`) scores this
    fabrication as a FAIL — not a green refusal. Old logic marked it `True`
    (chunks empty) and let the made-up answer pass."""
    node = Node(id="n2h", type=NodeType.LLM_STEP, params={"prompt": "x", "kwargs": {}})
    result = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)
    assert isinstance(result, dict)
    assert result["refused"] is False


async def test_llm_step_not_refused_on_normal_grounded_answer() -> None:
    """A real grounded answer (not the sentinel) with non-empty
    `retrieved_chunks` → `refused` `False`. Same outcome as before the fix but
    now for the RIGHT reason: the answer is not the declared refusal token,
    independent of chunk presence."""
    node = Node(
        id="n2d",
        type=NodeType.LLM_STEP,
        params={
            "prompt": "x",
            "kwargs": {},
            "retrieved_chunks": [
                KbSearchResultItem(
                    chunk_id="ankor-leave-001#c1",
                    text="Báo trước tối thiểu 3 ngày làm việc.",
                    score=0.9,
                    tenant_id=ANKOR_ID,
                    section_role="public",
                )
            ],
        },
    )
    result = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)
    assert isinstance(result, dict)
    assert result["refused"] is False


async def test_llm_step_citation_regex_handles_real_de_chunk_id_format() -> None:
    """`_CITATION_RE` must extract a real DE-shaped chunk_id (`{doc_id}#c{n}`,
    e.g. `ankor-leave-001#c1`) out of `[...]` brackets, not just the
    synthetic hyphen-only `chunk-NNN` ids every other fixture in this repo
    happens to use. A character class that excludes `#` silently drops the
    match entirely (`[]`, not an error) — this must FAIL on that bug."""
    # The chunk is grounded (present in `retrieved_chunks`) so the
    # grounded-citation rule keeps it — this isolates the regex behavior from
    # the grounding filter (empty chunks would yield `[]` regardless).
    node = Node(
        id="n2b",
        type=NodeType.LLM_STEP,
        params={
            "prompt": "x",
            "kwargs": {},
            "retrieved_chunks": [
                KbSearchResultItem(
                    chunk_id="ankor-leave-001#c1",
                    text="Nhân viên báo trước 3 ngày làm việc.",
                    score=0.9,
                    tenant_id=ANKOR_ID,
                    section_role="public",
                )
            ],
        },
    )
    result = await LlmStepExecutor(_HashChunkIdLLM(), EmptyEmbedding()).execute(node)
    assert isinstance(result, dict)
    assert result["citations"] == ["ankor-leave-001#c1"]


async def test_llm_step_empty_retrieved_chunks_yields_no_ungrounded_citation() -> None:
    """When `retrieved_chunks` is empty (e.g. `kb-retrieve` returned nothing,
    or the tenant fence blocked retrieval), there is NOTHING to ground a
    citation against — any `[chunk_id]` the LLM brackets is ungrounded and MUST
    be dropped. `citations` must be `[]`, never the raw extraction. Guards the
    false-positive citation-accuracy the smoke-eval hit when an ungrounded
    marker leaked into the trace as a "real" citation. The LLM here brackets
    `[ankor-leave-001#c1]` but no chunk was retrieved, so nothing may be
    cited."""
    node = Node(id="n2e", type=NodeType.LLM_STEP, params={"prompt": "x", "kwargs": {}})
    result = await LlmStepExecutor(_HashChunkIdLLM(), EmptyEmbedding()).execute(node)
    assert isinstance(result, dict)
    assert result["citations"] == []


async def test_tool_call_dispatches_whitelisted() -> None:
    """A whitelisted tool dispatches to the stub-dispatched marker; a
    non-whitelisted tool raises (defense-in-depth per `ToolCallExecutor`
    docstring — never execute a tool outside the whitelist)."""
    node = Node(id="n3", type=NodeType.TOOL_CALL, params={"tool": "search_docs"})
    result = await ToolCallExecutor(WhitelistToolDispatch(["search_docs"])).execute(node)
    assert result == {"tool": "search_docs", "status": "stub-dispatched"}

    bad_node = Node(id="n3b", type=NodeType.TOOL_CALL, params={"tool": "delete_everything"})
    with pytest.raises(ValueError, match="delete_everything"):
        await ToolCallExecutor(WhitelistToolDispatch(["search_docs"])).execute(bad_node)


async def test_tool_call_no_dispatcher_still_not_implemented() -> None:
    """`ToolCallExecutor()` (0-arg, `dispatcher=None` default) must still raise
    `NotImplementedError` — the pre-phase-1 call shape (locked previously by
    `test_interpreter_contract.py::test_each_executor_not_implemented`, which
    was removed in phase 2). Guards `executors.py`'s `if self._dispatcher is
    None: raise NotImplementedError(...)` branch against silently regressing
    to e.g. `return {}` — that branch is otherwise unreachable/untested."""
    node = Node(id="n3c", type=NodeType.TOOL_CALL, params={"tool": "search_docs"})
    with pytest.raises(NotImplementedError):
        await ToolCallExecutor().execute(node)


async def test_end_terminates() -> None:
    node = Node(id="n4", type=NodeType.END, params={})
    result = await EndExecutor().execute(node)
    assert result == {"terminated": True}


async def test_condition_hitl_still_not_implemented() -> None:
    """Scope-fence (KHOÁ): Day 3 does NOT prematurely implement these 2
    executors — spec-contract form (`pytest.raises(NotImplementedError)`) is
    correct here because this locks the current STUB state, not a business
    property."""
    node = Node(id="n5", type=NodeType.CONDITION, params={})
    with pytest.raises(NotImplementedError):
        await ConditionExecutor().execute(node)
    with pytest.raises(NotImplementedError):
        await HitlPauseExecutor().execute(node)
