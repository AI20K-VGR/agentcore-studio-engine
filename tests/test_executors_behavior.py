"""Behavioral tests for the filled node-executor bodies (phase 1 of Day 3,
spec AIE-1, plan `260722-0956-day3-interpreter-3node`; `ConditionExecutor`
and `HitlPauseExecutor` bodies filled later, plan
`260806-0938-d14-aie1-node-executors-grid-prep`).

Real teeth per `docs/code-standards.md` §4.1: every assertion below pins a
concrete stub-shaped value (not a bare `pytest.raises(NotImplementedError)`)
— that form is now valid for exactly ONE test in this repo,
`test_tool_call_no_dispatcher_still_not_implemented` (the `dispatcher=None`
defense-in-depth branch, genuinely still unfilled). `ConditionExecutor`'s
own `when`-grammar behavior is covered in `test_condition_when_grammar.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from studio_contracts import KbSearchResultItem, Node, NodeType, Tokens
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM, WhitelistToolDispatch
from studio_engine.executors import (
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


async def test_kb_retrieve_raises_when_tenant_id_absent() -> None:
    """Day 8 fail-closed (INV-1): a `kb-retrieve` node whose `params` carry
    no `tenant_id` at all must raise `PermissionError`, NOT silently return
    `[]`. Before this phase the executor fell back to a nil-UUID sentinel —
    indistinguishable from "this tenant genuinely has 0 chunks", which is
    exactly the fail-closed-by-luck-not-by-contract bug this phase kills."""
    node = Node(id="n1", type=NodeType.KB_RETRIEVE, params={"query": "leave policy"})
    with pytest.raises(PermissionError):
        await KbRetrieveExecutor(EmptyKbSearch()).execute(node)


async def test_kb_retrieve_raises_when_tenant_id_is_slug_not_uuid() -> None:
    """A `tenant_id` present but shaped as a client-declared slug (`"ankor"`,
    not a real `UUID`) must also raise — the executor never coerces a slug
    into a tenant identity itself; only a real `UUID` (session-supplied via
    `interpreter.run`) is accepted."""
    node = Node(id="n1b", type=NodeType.KB_RETRIEVE, params={"query": "x", "tenant_id": "ankor"})
    with pytest.raises(PermissionError):
        await KbRetrieveExecutor(EmptyKbSearch()).execute(node)


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
    # D19 (kit#121): tokens is a real whitespace-split count of the built
    # prompt (= fixture["request"]["prompt"], declared) and the LLM's answer
    # (= fixture["response"]) — no longer the hardcoded Tokens(0, 0). Counts
    # derived directly from the fixture text via `len(text.split())`, same
    # rule `LlmStepExecutor.execute` uses.
    assert result["tokens"] == Tokens(
        prompt=len(fixture["request"]["prompt"].split()), completion=len(fixture["response"].split())
    )
    assert result["citations"] == ["chunk-001"]
    # A real fixture answer (not the sentinel) is content, not a refusal → False.
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


async def test_hitl_pause_returns_pause_shaped_output() -> None:
    """T13 — `HitlPauseExecutor` returns a real pause-shaped dict. It does
    NOT actually pause anything (the interpreter has no idea this executor
    exists, per its docstring's INV-2 limitation) — this test pins only the
    output shape, JSON-serializable so it can flow into `TraceEvent.outputs`
    the same as every other executor's output."""
    node = Node(id="n5", type=NodeType.HITL_PAUSE, params={})
    result = await HitlPauseExecutor().execute(node)
    assert result == {"paused": True, "status": "pending_approval"}
    json.dumps(result)
