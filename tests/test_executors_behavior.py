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

import pytest
from studio_contracts import Node, NodeType, Tokens
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM, WhitelistToolDispatch
from studio_engine.executors import (
    ConditionExecutor,
    EndExecutor,
    HitlPauseExecutor,
    KbRetrieveExecutor,
    LlmStepExecutor,
    ToolCallExecutor,
)

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "llm_step" / "smoke-01.json"


async def test_kb_retrieve_returns_empty_stub() -> None:
    """`EmptyKbSearch` stub always returns `[]` — executor must pass it
    through unchanged (fence-EXECUTOR: never widen/re-derive on this side)."""
    node = Node(
        id="n1",
        type=NodeType.KB_RETRIEVE,
        params={"query": "leave policy", "tenant": "ankor", "section_roles": ["public"], "top_k": 5},
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
        params={"prompt": fixture["request"]["prompt"], "kwargs": fixture["request"]["kwargs"]},
    )
    result = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)
    assert isinstance(result, dict)
    assert result["answer"] == fixture["response"]
    assert result["tokens"] == Tokens(prompt=0, completion=0)
    assert result["citations"] == ["chunk-001"]


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
