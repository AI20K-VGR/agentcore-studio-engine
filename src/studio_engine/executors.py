"""6 node-executor stubs (spec AIE-1, R-SPEC A2) — one class per closed
`NodeType` (umbrella-contract.md:62-73). Every class below is an INTERFACE
STUB: the constructor wires the collaborator Protocol(s) each node type
consumes at runtime, but `execute()` is `NotImplementedError` — the real
dispatch body is AIE-1's own OJT deliverable. Filling it in ahead of time
(here, on this phase) is exactly the anti-pattern the phase's own risk table
calls out: "Executor làm xanh hộ (impl logic)".
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from studio_contracts import LLM, EmbeddingService, KbSearch, Node


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
        raise NotImplementedError("spec AIE-1: kb-retrieve executor body — see R-SPEC A2/A3 (fence-EXECUTOR above)")


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
        raise NotImplementedError("spec AIE-1: llm-step executor body — see R-SPEC A2")


class ConditionExecutor:
    """`condition` node — branches on `edges[].when` evaluated against the
    upstream node's output (e.g. a verdict/score). SWE co-owns the `when`
    expression's grammar (recipe schema/graph-lint); AIE-1 owns evaluating it
    at runtime here (spec AIE-1, R-SPEC A2)."""

    async def execute(self, node: Node) -> object:
        raise NotImplementedError("spec AIE-1: condition executor body — see R-SPEC A2")


class ToolCallExecutor:
    """`tool-call` node — dispatches a tool stub strictly within
    `agent_config.tool_whitelist` (rule-verdict/matching). SWE co-owns
    whitelist enforcement at the recipe-validator layer; a tool outside the
    whitelist must never execute here either (defense in depth, spec AIE-1,
    R-SPEC A2)."""

    async def execute(self, node: Node) -> object:
        raise NotImplementedError("spec AIE-1: tool-call executor body — see R-SPEC A2")


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
        raise NotImplementedError("spec AIE-1: end executor body — see R-SPEC A2")
