"""6 node-executor stubs (spec AIE-1, R-SPEC A2) — one class per closed
`NodeType` (umbrella-contract.md:62-73). Every class below is an INTERFACE
STUB: the constructor wires the collaborator Protocol(s) each node type
consumes at runtime, but `execute()` is `NotImplementedError` — the real
dispatch body is AIE-1's own OJT deliverable. Filling it in ahead of time
(here, on this phase) is exactly the anti-pattern the phase's own risk table
calls out: "Executor làm xanh hộ (impl logic)".
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from studio_contracts import LLM, EmbeddingService, KbSearch, Node, Tokens

# Stub-grade citation extraction (spec AIE-1, phase-1 risk table) — a simple
# `[chunk_id]` bracket regex, NOT a real citation parser. Good enough for the
# Day 3 fixture-replay demo; YAGNI on anything smarter here.
_CITATION_RE = re.compile(r"\[([\w-]+)\]")


@runtime_checkable
class NodeExecutor(Protocol):
    """Structural shape every node executor conforms to. `interpreter.run()`
    dispatches one instance of this per `node.type` (see `registry.py`)."""

    async def execute(self, node: Node) -> object: ...


class KbRetrieveExecutor:
    """`kb-retrieve` node — fence-EXECUTOR (R-SPEC A3, AIE-1's own layer of
    the 3-layer fence: Tenant-Wall=SWE, fence-DATA=DE, fence-EXECUTOR=AIE-1).

    Contract the real `execute()` body MUST honor:
    - `section_roles` MUST be resolved server-side (from the run's
      session/tenant context) and passed into `KbSearch.search(...)`
      UNCHANGED — a client-declared `section_roles` override must be
      ignored. Accepting a client override here is exactly the T6
      label-spoof this fence exists to stop.
    - The executor must NEVER retrieve unfiltered/over-scoped chunks and
      then filter them post-hoc via the LLM. That is the umbrella-contract's
      explicitly forbidden anti-pattern ("nhờ LLM đừng nói" — fake fence).
    - `KbSearch` (fence-DATA, DE-owned) already fails closed at retrieval;
      this executor's only fence duty is to never undermine that guarantee
      by widening or re-deriving `section_roles` on the client/executor side.
    - The result's cited `chunk_id`s flow into the emitted `TraceEvent.citations`.
    """

    def __init__(self, kb_search: KbSearch) -> None:
        self._kb_search = kb_search

    async def execute(self, node: Node) -> object:
        """Output shape (v0 stub): the raw `list[KbSearchResultItem]` from
        `KbSearch.search(...)`, passed through unchanged — no post-hoc
        filtering/widening on this side (fence-EXECUTOR duty above). `query`/
        `tenant`/`section_roles`/`top_k` are read as-given from `node.params`;
        Day 3 has no real server-side session/tenant context to resolve
        `section_roles` from, so this stub passes through whatever the node
        carries rather than re-deriving it — real context-threading lands
        alongside the real `KbSearch` impl (P5)."""
        raw_query = node.params.get("query", "")
        raw_tenant = node.params.get("tenant", "")
        raw_roles = node.params.get("section_roles", [])
        raw_top_k = node.params.get("top_k", 5)

        query = raw_query if isinstance(raw_query, str) else str(raw_query)
        tenant = raw_tenant if isinstance(raw_tenant, str) else str(raw_tenant)
        section_roles = [str(role) for role in raw_roles] if isinstance(raw_roles, list) else []
        top_k = raw_top_k if isinstance(raw_top_k, int) else int(str(raw_top_k))
        return await self._kb_search.search(query, tenant, section_roles, top_k)


class LlmStepExecutor:
    """`llm-step` node — calls `LLM.complete` (gateway-stub client, per-agent
    x env) over the prompt/context (including any cited chunk carried from an
    upstream `kb-retrieve`), embeds via `EmbeddingService` where the recipe
    calls for it, and must extract `chunk_id` back out into a citation on the
    emitted `TraceEvent.citations` (spec AIE-1, R-SPEC A2)."""

    def __init__(self, llm: LLM, embedding: EmbeddingService) -> None:
        self._llm = llm
        self._embedding = embedding

    async def execute(self, node: Node) -> object:
        """Output shape (v0 stub): `{"answer": <LLM.complete str>, "tokens":
        Tokens(0, 0), "citations": [<chunk_id extracted from the answer
        text>]}`. Citation extraction is a stub-grade `\\[chunk_id\\]` bracket
        regex (`_CITATION_RE`) — good enough for the Day 3 fixture-replay
        demo, not a real citation parser (YAGNI). `tokens` is hardcoded to
        `Tokens(0, 0)`: Day 3's `LLM` collaborator is a fixture replay with no
        real token accounting; real usage lands with the gateway-stub client.
        `embedding` is wired via constructor-DI but unused here — Day 3's
        recipe never calls for an embed step (Day 7 is the real usage)."""
        raw_prompt = node.params.get("prompt", "")
        raw_kwargs = node.params.get("kwargs", {})

        prompt = raw_prompt if isinstance(raw_prompt, str) else str(raw_prompt)
        kwargs: dict[str, object] = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else {}

        answer = await self._llm.complete(prompt, **kwargs)
        citations = _CITATION_RE.findall(answer)
        return {"answer": answer, "tokens": Tokens(prompt=0, completion=0), "citations": citations}


class ConditionExecutor:
    """`condition` node — branches on `edges[].when` evaluated against the
    upstream node's output (e.g. a verdict/score). SWE co-owns the `when`
    expression's grammar (recipe schema/graph-lint); AIE-1 owns evaluating it
    at runtime here (spec AIE-1, R-SPEC A2)."""

    async def execute(self, node: Node) -> object:
        raise NotImplementedError("spec AIE-1: condition executor body — see R-SPEC A2")


@runtime_checkable
class ToolDispatch(Protocol):
    """Engine-local seam for the `tool-call` node's dispatcher collaborator
    (NOT a `studio_contracts` protocol — `packages/contracts` has no
    tool-dispatch seam; this is `studio_engine`'s own, same as `NodeExecutor`
    above). `ToolCallExecutor`'s constructor is not frozen by
    `test_interpreter_contract.py`, so this DI param was free to add here
    (unlike `KbRetrieveExecutor`/`LlmStepExecutor`, whose constructors are
    locked)."""

    async def dispatch(self, tool: str) -> object: ...


class ToolCallExecutor:
    """`tool-call` node — dispatches a tool stub strictly within
    `agent_config.tool_whitelist` (rule-verdict/matching). SWE co-owns
    whitelist enforcement at the recipe-validator layer; a tool outside the
    whitelist must never execute here either (defense in depth, spec AIE-1,
    R-SPEC A2)."""

    def __init__(self, dispatcher: ToolDispatch | None = None) -> None:
        self._dispatcher = dispatcher

    async def execute(self, node: Node) -> object:
        """Output shape (v0 stub): `{"tool": <name>, "status":
        "stub-dispatched"}` for a tool in the dispatcher's whitelist; the
        dispatcher RAISES for a tool outside it (defense-in-depth — the
        recipe-validator layer is the primary whitelist enforcement, this is
        the second belt, same pattern as the closed-`NodeType` registry
        guard). No dispatcher wired at construction (`dispatcher=None`, the
        default — kept so `ToolCallExecutor()`'s pre-phase-1 0-arg call shape
        stays valid, per `test_interpreter_contract.py::
        test_each_executor_not_implemented`, frozen/not this phase's to
        touch) still raises `NotImplementedError`, same as before this
        phase filled the body."""
        if self._dispatcher is None:
            raise NotImplementedError(
                "spec AIE-1: tool-call executor requires a dispatcher collaborator — see R-SPEC A2"
            )
        raw_tool = node.params.get("tool", "")
        tool = raw_tool if isinstance(raw_tool, str) else str(raw_tool)
        return await self._dispatcher.dispatch(tool)


class HitlPauseExecutor:
    """`hitl-pause` node — dừng-chờ-người first-class (INV-2): the real body
    pauses the run, emits a pause `TraceEvent`, and yields control back to
    the playground for an external approval before the interpreter resumes
    along this node's downstream edge. SWE wires the playground-side
    approval UI; AIE-1 owns this pause/emit/yield executor body (spec AIE-1,
    R-SPEC A2)."""

    async def execute(self, node: Node) -> object:
        raise NotImplementedError("spec AIE-1: hitl-pause executor body — see R-SPEC A2, INV-2")


class EndExecutor:
    """`end` node — terminal node: the real body emits the run's final
    `TraceEvent` and assembles the result the interpreter returns from
    `run()` (spec AIE-1, R-SPEC A2)."""

    async def execute(self, node: Node) -> object:
        """Output shape (v0 stub): `{"terminated": True}` — the terminal
        marker `interpreter.run()` (phase 2) uses to stop walking the DAG."""
        del node
        return {"terminated": True}
