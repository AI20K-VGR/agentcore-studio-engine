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

from studio_contracts import LLM, EmbeddingService, KbSearch, KbSearchResultItem, Node, Tokens

# Stub-grade citation extraction (spec AIE-1, phase-1 risk table) — a simple
# `[chunk_id]` bracket regex, NOT a real citation parser. Good enough for the
# Day 3 fixture-replay demo; YAGNI on anything smarter here. Character class
# includes `#` for DE's real chunk_id shape `{doc_id}#c{n}`
# (packages/kb/docs/callisto-doc-schema.md:209) — a class without it silently
# drops every real chunk_id match, only ever working by accident on the
# synthetic hyphen-only `chunk-NNN` ids used in this repo's own fixtures.
_CITATION_RE = re.compile(r"\[([\w#-]+)\]")


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

    Trạng thái 2026-07-23 (DE-blocked): `KbSearch.search(...)` ở trên vẫn được
    gọi qua interface — nhưng impl thật (`packages/kb/src/studio_kb/search.py
    ::KbSearchService.search`) vẫn `raise NotImplementedError` (DE chưa xong,
    pin bằng `packages/kb/tests/test_search_contract.py`). `.importlinter`
    cấm `studio_engine` import `studio_kb` trực tiếp, nên điểm nối `KbSearch`
    thật chỉ có thể xảy ra ở `apps/studio` (composition root, Day 6) — KHÔNG
    ở đây. Điểm flip khi DE xong:
    `tests/e2e/test_lifecycle.py::test_step_2_attach_tools_and_kb_scope` và
    `tests/e2e/test_lifecycle.py::test_step_5_fence_proof_zero_leak_money_shot`.
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
        Tokens(0, 0), "citations": [...], "refused": <bool>}`. `tokens` is
        hardcoded to `Tokens(0, 0)`: Day 3's `LLM` collaborator is a fixture
        replay with no real token accounting; real usage lands with the
        gateway-stub client. `embedding` is wired via constructor-DI but
        unused here — Day 3's recipe never calls for an embed step (Day 7 is
        the real usage).

        Citations (Day 4 threading, spec AIE-1, grounded): `node.params["retrieved_chunks"]`
        carries the `KbSearchResultItem` list `interpreter.run()` threaded in
        from the upstream `kb-retrieve` node's real output (see
        `interpreter.py::run`). When non-empty, a citation must be BOTH
        retrieved AND actually bracket-cited in the answer text: extract
        `\\[chunk_id\\]` mentions (`_CITATION_RE`, in answer order) and keep
        only the ones present in `retrieved_chunks`. Citing "everything
        retrieved" regardless of what the answer references is a real bug
        (found against DE's `StaticKbSearch`, which returns `top_k` chunks
        per query, not 1) — it would cite chunks the LLM never used. When
        `retrieved_chunks` is empty (or absent, e.g. no upstream
        `kb-retrieve` in this walk), `[]` is a valid result (contract §6.1,
        `packages/kb/docs/contracts/kb-search.v0.md:172-175`) and there is
        nothing to filter against, so citations fall back to the raw
        extraction (unchanged, keeps Day 3's
        `test_llm_step_replays_fixture_answer` green).

        `refused` (added D4 follow-up, for `studio_evalhub`'s smoke-eval
        fail-closed refusal branch — SC-04 cross-tenant / SC-05 cross-role in
        `packages/kb/golden/smoke-5.yaml`): `True` iff `retrieved_chunks` is
        empty. This is a STRUCTURAL signal, not a guess on the answer's free
        text (`studio_evalhub.agent_runner.AgentAnswer.refused`'s docstring
        explicitly forbids inferring refusal from prose). It is sound because
        the permission fence lives at retrieval (umbrella-contract §1: "KHÔNG
        được nhờ LLM 'đừng nói'") — a fenced/no-data query surfaces here as
        `kb-retrieve` returning `[]`, and the hardcoded 4-node walk (R2) always
        runs `kb-retrieve` before `llm-step`, so every case in this engine's
        current scope is KB-grounded and "nothing retrieved" ⟺ "must refuse,
        not hallucinate" (umbrella-contract §1). Known gap this does NOT
        cover (tracked, not this field's job): an LLM that hallucinates an
        answer despite empty `retrieved_chunks` is still marked `refused` here
        — catching that is the separate "nhánh trả-lời-được không kiểm rò rỉ"
        gap noted in `packages/kb/docs/format.md` §9b."""
        raw_prompt = node.params.get("prompt", "")
        raw_kwargs = node.params.get("kwargs", {})
        raw_chunks = node.params.get("retrieved_chunks", [])

        prompt = raw_prompt if isinstance(raw_prompt, str) else str(raw_prompt)
        kwargs: dict[str, object] = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else {}
        retrieved_chunks: list[KbSearchResultItem] = raw_chunks if isinstance(raw_chunks, list) else []

        answer = await self._llm.complete(prompt, **kwargs)
        extracted = _CITATION_RE.findall(answer)
        if retrieved_chunks:
            retrieved_ids = {chunk.chunk_id for chunk in retrieved_chunks}
            citations = [chunk_id for chunk_id in extracted if chunk_id in retrieved_ids]
        else:
            citations = extracted
        return {
            "answer": answer,
            "tokens": Tokens(prompt=0, completion=0),
            "citations": citations,
            "refused": not retrieved_chunks,
        }


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
    above). `ToolCallExecutor`'s constructor is not frozen by any contract
    test, so this DI param was free to add here (unlike
    `KbRetrieveExecutor`/`LlmStepExecutor`, whose constructors are locked)."""

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
