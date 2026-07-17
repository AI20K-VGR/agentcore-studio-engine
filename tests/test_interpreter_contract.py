"""RED-by-design (spec AIE-1, R-SPEC A2) — `interpreter.run()` and all 6 node
executors are `NotImplementedError` bodies until AIE-1 fills in the real
implementation (a follow-on deliverable, not this phase's). `xfail(strict=
False)` is the builtin pytest marker (no `pyproject.toml`/`ini` edit needed)
so the suite stays green while staying honest that the behavior is not yet
built. This is the same failure mode the phase's risk table calls out for a
premature-green executor ("Executor làm xanh hộ") — the guard here is that
this file's assertions stay meaningful (assert `NotImplementedError`
specifically) rather than a bare pass-through xfail.
"""

from __future__ import annotations

import pytest
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
from studio_engine.executors import (
    ConditionExecutor,
    EndExecutor,
    HitlPauseExecutor,
    KbRetrieveExecutor,
    LlmStepExecutor,
    ToolCallExecutor,
)


class _FakeKbSearch:
    async def search(self, query: str, tenant: str, section_roles: list[str], top_k: int) -> list[KbSearchResultItem]:
        del query, tenant, section_roles, top_k
        return []


class _FakeLLM:
    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return ""


class _FakeEmbedding:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        del texts
        return []


class _FakeTraceWriter:
    async def write(self, event: TraceEvent) -> None:
        del event


def _minimal_recipe() -> Recipe:
    node = Node(id="n1", type=NodeType.END, params={})
    return Recipe(
        agent_id="agent-1",
        tenant="ankor",
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[]),
        dag=Dag(nodes=[node], edges=[]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


@pytest.mark.xfail(reason="spec AIE-1 fills interpreter/executors", strict=False)
async def test_run_not_implemented() -> None:
    """KHOÁ: `interpreter.run()` body is `NotImplementedError` (spec AIE-1) —
    the walk/dispatch/emit logic is not this phase's job to fill in."""
    with pytest.raises(NotImplementedError):
        await interpreter.run(_minimal_recipe(), trace_writer=_FakeTraceWriter())


@pytest.mark.xfail(reason="spec AIE-1 fills interpreter/executors", strict=False)
async def test_each_executor_not_implemented() -> None:
    """KHOÁ: all 6 node executors are `NotImplementedError` bodies (spec
    AIE-1) — one assert per node type, matching the 6 closed `NodeType`
    values so a silently-filled-in executor would break this test."""
    node = Node(id="n1", type=NodeType.END, params={})

    with pytest.raises(NotImplementedError):
        await KbRetrieveExecutor(kb_search=_FakeKbSearch()).execute(node)
    with pytest.raises(NotImplementedError):
        await LlmStepExecutor(llm=_FakeLLM(), embedding=_FakeEmbedding()).execute(node)
    with pytest.raises(NotImplementedError):
        await ConditionExecutor().execute(node)
    with pytest.raises(NotImplementedError):
        await ToolCallExecutor().execute(node)
    with pytest.raises(NotImplementedError):
        await HitlPauseExecutor().execute(node)
    with pytest.raises(NotImplementedError):
        await EndExecutor().execute(node)
