"""6 node-executor stubs (spec AIE-1, R-SPEC A2) — one class per closed
`NodeType` (umbrella-contract.md:62-73). The constructor of each wires the
collaborator Protocol(s) that node type consumes at runtime. As of
plan `260806-0938-d14-aie1-node-executors-grid-prep` (D14 phase 1),
`ConditionExecutor` and `HitlPauseExecutor` are filled bodies (grammar
evaluation and pause-shaped output, respectively) — the same status the
other 4 executors already had. The one remaining `NotImplementedError` is a
genuine, still-open seam: `ToolCallExecutor`'s `dispatcher=None`
defense-in-depth branch (see its own docstring below).
"""

from __future__ import annotations

import json
import operator
import re
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from studio_contracts import LLM, EmbeddingService, KbSearch, KbSearchResultItem, Node, Tokens

# Stub-grade citation extraction (spec AIE-1, phase-1 risk table) — a simple
# `[chunk_id]` bracket regex, NOT a real citation parser. Good enough for the
# Day 3 fixture-replay demo; YAGNI on anything smarter here. Character class
# includes `#` for DE's real chunk_id shape `{doc_id}#c{n}`
# (packages/kb/docs/callisto-doc-schema.md:209) — a class without it silently
# drops every real chunk_id match, only ever working by accident on the
# synthetic hyphen-only `chunk-NNN` ids used in this repo's own fixtures.
_CITATION_RE = re.compile(r"\[([\w#-]+)\]")

# Prompt scaffold for `llm-step`. AIE-1 builds this, not the recipe author:
# `umbrella-contract.md:63` assigns `llm-step` to AIE-1, and SWE's published
# recipe (`studio_workbench.builder_d4`) declares the node as
# `params={"temperature": 0.0}` with no `prompt` — so before this the executor
# sent the LLM an empty string and the retrieved chunks never reached the model
# at all, making every citation (and therefore `citation_accuracy`) unreachable.
#
# The second line is load-bearing for `refused` (see `LlmStepExecutor.execute`):
# a run counts as a refusal exactly when it grounds no citation, so the model
# has to be told to withhold citations when the excerpts do not answer. That is
# NOT the INV-1 fence — permission is decided at retrieval
# (`umbrella-contract.md:71`) and the model is never asked to withhold data it
# was already handed; it is only told not to cite what does not answer.
_PROMPT_HEADER = (
    "Bạn là trợ lý nội bộ. Chỉ dùng các đoạn trích dưới đây để trả lời, "
    "và trích dẫn chunk_id trong ngoặc vuông.\n"
    "Nếu các đoạn trích không chứa câu trả lời, hãy nói rõ là không có thông tin "
    "và KHÔNG trích dẫn gì."
)
_NO_EXCERPT = "(không có đoạn trích nào được truy xuất)"


def build_prompt(query: str, chunks: list[KbSearchResultItem], instructions: str = "") -> str:
    """Render the `llm-step` prompt from the walk's question and its grounding.

    Each chunk opens with its `chunk_id` alone on a line, in bracket form,
    followed by its text; excerpts are blank-line separated. Bracket form
    because that is the exact token the answer is expected to echo back and
    `_CITATION_RE` later extracts — pasting only chunk TEXT would read fine to a
    human and make every citation impossible. Id on its OWN line because real
    Callisto chunks are multi-line markdown (heading, then blank-line-separated
    paragraphs): rendered inline as `[id] text`, the start of the next excerpt
    is indistinguishable from a paragraph break inside the current one.

    Empty `chunks` says so explicitly rather than leaving a blank gap — empty
    retrieval is a valid result, not an error (`kb-search.v0.md` §6.1), and the
    model still has to be told there was nothing to read.

    `instructions` (Day 7, `recipe.agent_config.instructions`) is optional and
    defaults to `""` so every pre-Day-7 2-arg call site keeps its exact prior
    output; when given, it is prepended ahead of the fixed `_PROMPT_HEADER`.
    """
    excerpts = "\n\n".join(f"[{chunk.chunk_id}]\n{chunk.text}" for chunk in chunks) if chunks else _NO_EXCERPT
    header = f"{instructions}\n\n{_PROMPT_HEADER}" if instructions else _PROMPT_HEADER
    return f"{header}\n\n{excerpts}\n\nCâu hỏi: {query}"


@runtime_checkable
class NodeExecutor(Protocol):
    """Structural shape every node executor conforms to. `interpreter.run()`
    dispatches one instance of this per `node.type` (see `registry.py`)."""

    async def execute(self, node: Node) -> object: ...


class KbRetrieveExecutor:
    """`kb-retrieve` node — fence-EXECUTOR (R-SPEC A3, AIE-1's own layer of
    the 3-layer fence: Tenant-Wall=SWE, fence-DATA=DE, fence-EXECUTOR=AIE-1).

    Contract the real `execute()` body honors (as of Day 17, plan
    `260811-1121-d17-aie1-section-roles-server-resolve`, phase 1):
    - `section_roles` IS resolved server-side and passed into
      `KbSearch.search(...)` — but NOT by this class. `interpreter.run()`
      (`interpreter.py`, in the `NodeType.KB_RETRIEVE` branch) overwrites
      `node.params["section_roles"]` with `session_context.system_roles` BEFORE
      dispatching to this executor, the same pattern it already used for
      `tenant_id`. By the time `execute()` below reads
      `node.params.get("section_roles", ...)`, a client-declared override
      has already been discarded upstream — this class's own read is
      unconditional (see its docstring below) and relies entirely on that
      upstream override for the T6 fence. A client-declared `section_roles`
      therefore never reaches `KbSearch.search(...)` through the real
      `interpreter.run()` walk; it can only be observed by constructing and
      calling this executor DIRECTLY, bypassing the session fence (defense-
      in-depth boundary, still fails closed — see `execute()`'s docstring).
    - The executor must NEVER retrieve unfiltered/over-scoped chunks and
      then filter them post-hoc via the LLM. That is the umbrella-contract's
      explicitly forbidden anti-pattern ("nhờ LLM đừng nói" — fake fence).
    - `KbSearch` (fence-DATA, DE-owned) already fails closed at retrieval;
      this executor's only fence duty is to never undermine that guarantee
      by widening or re-deriving `section_roles` on the client/executor side.
    - The result's cited `chunk_id`s flow into the emitted `TraceEvent.citations`.

    Trạng thái 2026-08-12 (D17 flip, `kb#19`): seam chính thức
    `KbSearchService.search()` (`packages/kb/src/studio_kb/search.py`) đã
    un-ratchet — không còn raise `NotImplementedError`, nay uỷ quyền một dòng
    sang `PgKbSearch.search` (`postgres.py`), impl fenced retrieval thật đã
    nằm trên spine từ D13. `.importlinter` vẫn cấm `studio_engine` import
    `studio_kb` trực tiếp, nên điểm nối `KbSearch` thật vẫn chỉ xảy ra ở
    `apps/studio` (composition root, Day 6) — KHÔNG ở đây. Điểm flip e2e khi
    owner điền assertion thật (hôm nay cả hai còn `pytest.skip`, chưa khoá gì):
    `tests/e2e/test_lifecycle.py::test_step_2_attach_tools_and_kb_scope` và
    `tests/e2e/test_lifecycle.py::test_step_5_fence_proof_zero_leak_money_shot`.
    """

    def __init__(self, kb_search: KbSearch) -> None:
        self._kb_search = kb_search

    async def execute(self, node: Node) -> object:
        """Output shape (v0 stub): the raw `list[KbSearchResultItem]` from
        `KbSearch.search(...)`, passed through unchanged — no post-hoc
        filtering/widening on this side (fence-EXECUTOR duty above). `query`/
        `top_k` are read as-given from `node.params`; `[]` when
        `section_roles` is absent/malformed is `[]` deny-all, not a
        wildcard — there is no `PermissionError` branch for it the way there
        is for `tenant_id` below, because unlike a missing tenant identity
        (which has no safe fallback value), a missing/malformed
        `section_roles` already reads as "no chunk role matches" at
        retrieval (`static_search.py`: `allowed = set(section_roles)`, empty
        set matches nothing).

        Neither `tenant_id` NOR `section_roles` is trusted as-given from
        `node.params` in the real walk (Day 8 INV-1 / Day 17 T6):
        `interpreter.run()` (`interpreter.py`, `NodeType.KB_RETRIEVE` branch)
        always overwrites both with `session_context.tenant_id`/
        `session_context.system_roles` before dispatch — server-resolved, never
        client-declared. This executor's own reads below (`node.params.get(
        "tenant_id")`, `node.params.get("section_roles", [])`) are exactly
        as-given from whatever `node.params` it is handed; the fence is
        entirely in `interpreter.run()`'s override, not here.
        `tenant_id` additionally fails closed with `PermissionError` when the
        post-override value still isn't a real `UUID` (absent, or a slug
        string) — SOMETHING upstream skipped the injection, so this executor
        refuses to make up a value: the earlier nil-UUID sentinel fallback
        was fail-closed only by luck (0 rows happened to match), not by
        contract, and a wiring bug behind it would read indistinguishably
        from "this tenant has no chunks". This is defense-in-depth — normal
        dispatch through `interpreter.run()` never reaches the raise; it
        only fires if this executor is constructed and called directly,
        bypassing the session fence. `section_roles` has no equivalent raise
        (QĐ-7, plan `260811-1121-d17`): `[]` is already the correct
        fail-closed value, so a raise here would be cargo-cult, not a real
        gap."""
        raw_query = node.params.get("query", "")
        raw_tenant_id = node.params.get("tenant_id")
        raw_roles = node.params.get("section_roles", [])
        raw_top_k = node.params.get("top_k", 5)

        if not isinstance(raw_tenant_id, UUID):
            # Report only the caller-supplied value's TYPE, never the value
            # itself — it is unvalidated data that could otherwise flow into
            # logs/error bodies verbatim.
            raise PermissionError(
                "kb-retrieve node.params['tenant_id'] must be a real UUID sourced from "
                "session_context (INV-1) — executor never sets/derives tenant identity itself. "
                f"Got a value of type {type(raw_tenant_id).__name__!r}."
            )

        query = raw_query if isinstance(raw_query, str) else str(raw_query)
        section_roles = [str(role) for role in raw_roles] if isinstance(raw_roles, list) else []
        top_k = raw_top_k if isinstance(raw_top_k, int) else int(str(raw_top_k))
        return await self._kb_search.search(query, raw_tenant_id, section_roles, top_k)


# D18 (kit#116) gateway-flag — the set of `LLM` impl CLASS NAMES this module
# recognizes as a known test/demo double, checked by name rather than
# `isinstance` against `LLM` (every impl, stub or real, satisfies that
# Protocol structurally, so `isinstance` cannot tell them apart). The name
# check also means this module never has to `import` `demo_stubs` (or any
# other double's home module) — corrected reason (D18 review S-1, an earlier
# version of this comment mis-cited a circular-import risk): `demo_stubs.py`
# imports only `json`/`pathlib`/`uuid`/`studio_contracts`, nothing that
# imports this module back, so there is no real import cycle to avoid here.
# The actual reason is layering, not cycles — `demo_stubs.py`'s own docstring
# names it Day-3 demo/test scaffolding, and a production module (this one)
# should not import a test/demo module regardless of whether doing so would
# cycle.
# Deliberately NOT trying to positively recognize a real gateway impl (none
# has shipped yet) — the safe direction is to default UNKNOWN impls to
# `"gateway"`, never to under-claim `"stub"` for an impl this set doesn't
# name. Add a name here only when a new known double lands.
#
# 3 names recognized as of D18 review H-1 — all fixture/fake replay, no live
# model call, per `demo_stubs.py`'s own INV-4 statement (CI replays recorded
# fixtures only, no live model call is permitted) and R-6 (no gateway impl
# ships in this kit, `docs/system-architecture.md` §6):
# - `FixtureLLM` (`demo_stubs.py`) — VCR-style replay of a recorded fixture
#   file; ignores the prompt entirely.
# - `_GoldenAwareLLM` (`scripts/run_golden_batch.py`) — a pure function of
#   its constructor-injected golden-set `expected_citation` label and the
#   `[chunk_id]` brackets it reads back out of the prompt it was actually
#   given; no network/model call (`complete()` body: regex + set membership).
# - `ExtractiveFakeLLM` (`apps/studio/src/studio_app/providers/fakes.py` —
#   out-of-repo-scope for this package to modify; its CLASS NAME string is
#   still safe to list here since classification is by name, not import) — a
#   pure function of the prompt's first `[chunk_id]` excerpt; no
#   network/model call.
_KNOWN_STUB_LLM_CLASS_NAMES = frozenset({"FixtureLLM", "_GoldenAwareLLM", "ExtractiveFakeLLM"})


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
        Tokens(prompt=N, completion=M), "citations": [...], "refused": <bool>,
        "llm_source": <"stub"|"gateway">}`. `embedding` is wired via
        constructor-DI but unused here — Day 3's recipe never calls for an
        embed step (Day 7 is the real usage).

        `tokens` (D19, kit#121): counted by whitespace-split
        (`len(text.split())`) on `prompt` for `.prompt` and on `answer` for
        `.completion` — NOT a real BPE/model tokenizer, since no gateway
        impl ships in this kit (R-6). Deterministic and good enough for
        internal cost-lineage; DE (who computes `cost` from this `tokens`
        output, design locked at `DL-11.A1-5` — executor only emits
        `tokens`, sink computes `cost`) needs to price against this exact
        unit, not a real model's token count. Here `prompt` is the local
        variable below (`declared_prompt or build_prompt(...)`) — the FULL
        rendered string actually sent to `LLM.complete()`, including the
        `_PROMPT_HEADER` scaffold and every retrieved chunk's full text, NOT
        `node.params["prompt"]` directly (a recipe author typically never
        declares it — see the module-level comment above `_PROMPT_HEADER`);
        the two can differ by an order of magnitude once chunks are
        retrieved, and DE prices cost off this count. Whitespace-split also
        under-counts Vietnamese text relative to a real BPE tokenizer — the
        exact ratio is `[ASSUMED]`, not measured (no tokenizer ships in this
        kit, R-6) — and would read a space-less script's entire answer as a
        single token. A known unit mismatch, not a bug, but DE should not
        treat this count as tokenizer-equivalent when pricing.

        `llm_source` (D18, kit#116, judge-consumption stability): a
        classification flag, NOT a live gateway integration — `"stub"` when
        `type(self._llm).__name__` is a known test/demo double
        (`_KNOWN_STUB_LLM_CLASS_NAMES`, currently `FixtureLLM`,
        `_GoldenAwareLLM`, and `ExtractiveFakeLLM` — see that set's own
        comment for why each qualifies), else `"gateway"`. The classification
        is by CLASS NAME, not `isinstance`
        against `LLM` (every impl, stub or real, satisfies that Protocol
        structurally) — name is the only signal this module has for "which
        concrete collaborator was injected". Default is deliberately
        `"gateway"`: an impl this set doesn't recognize is reported as the
        more consequential value (a judge/consumer reading `"stub"` would
        assume fixture-replay determinism a real gateway call does not
        have), never silently mislabeled `"stub"`.

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
        `kb-retrieve` in this walk, or retrieval was fenced/blocked), `[]` is a
        valid result (contract §6.1, `packages/kb/docs/contracts/kb-search.v0.md:172-175`)
        and there is nothing to ground a citation against, so `citations` is
        `[]` — NOT the raw extraction. An earlier cut fell back to the raw
        `[chunk_id]` extraction here, which let an ungrounded (hallucinated)
        marker leak into the trace as a "real" citation and made the smoke-eval
        score a false-positive citation-accuracy. Grounding is required: no
        retrieved chunk → no citation.

        `refused` (Day 6, for `studio_evalhub`'s smoke-eval fail-closed refusal
        branch — SC-04 cross-tenant / SC-05 cross-role in
        `packages/kb/golden/smoke-5.yaml`): `not citations`. A citation is
        already both grounded and actually referenced, so "grounded nothing" is
        exactly "could not answer from what it was given". This stays
        STRUCTURAL — no NLP guessing at prose, which
        `studio_evalhub.agent_runner.AgentAnswer.refused` forbids.

        `not citations` alone is only correct when a `kb-retrieve` actually ran
        upstream in this walk — `retrieved_chunks=[]` means the SAME thing
        whether `kb-retrieve` returned nothing or never ran at all, and this
        executor cannot tell those apart from `retrieved_chunks` by itself
        (engine#37). `agentcore-studio-web#14` shipped a first-class standalone
        mode — a single `llm-step` node with no `kb-retrieve` upstream ("Chatbot
        LLM Trực Tiếp") — so `citations` is always `[]` there and `not
        citations` alone would score every real answer a refusal. `has_kb_upstream`
        (interpreter-threaded, see the matching comment in `interpreter.py`
        next to `last_kb_output`) gates that: `refused = has_kb_upstream and
        not citations`. A walk with no `kb-retrieve` never enters the
        fence-refusal branch this flag exists to measure (SC-04/SC-05 below),
        so `False` there is the correct answer, not a fallback.

        Two earlier signals were tried and are measurably wrong on the real
        golden set (see `tests/test_refusal_from_grounding.py`):

        - `not retrieved_chunks` (commit `71caeb8`, still recorded as agreed in
          `evalhub/docs/scorecard-v0.md` §2.7:174). Measured against DE's
          `StaticKbSearch` on the Callisto corpus, SC-04 retrieval is NOT empty
          — the fence drops every Borea chunk while the token-overlap ranker
          still returns 3 ankor chunks on the common words in the query — so
          SC-04 reads `refused=False` and the refusal branch fails. §2.7's
          premise is false, not merely stale; it needs updating.
        - `answer.strip() == "[[REFUSED]]"`. That sentinel exists nowhere
          outside this package and no prompt in the workspace asks a model to
          emit it, so `refused` was permanently `False` on any real answer.

        Known limitation, stated rather than hidden: a model that answers
        correctly but omits the brackets is scored as a refusal. That is the
        same signal `citation_accuracy` already rests on, so such an answer
        fails one way rather than two."""
        raw_prompt = node.params.get("prompt", "")
        raw_query = node.params.get("query", "")
        raw_kwargs = node.params.get("kwargs", {})
        raw_chunks = node.params.get("retrieved_chunks", [])
        raw_instructions = node.params.get("instructions", "")
        raw_model = node.params.get("model", "")
        raw_has_kb_upstream = node.params.get("has_kb_upstream", False)

        kwargs: dict[str, object] = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else {}
        retrieved_chunks: list[KbSearchResultItem] = raw_chunks if isinstance(raw_chunks, list) else []
        # engine#37: walk-threaded (see `interpreter.py`'s `has_kb_upstream`) —
        # never trust a non-bool here, same defensive coercion as every other
        # `raw_*` param in this method.
        has_kb_upstream = raw_has_kb_upstream if isinstance(raw_has_kb_upstream, bool) else bool(raw_has_kb_upstream)
        # A recipe-declared prompt is a deliberate author choice and wins; the
        # VCR fixtures record one. Otherwise AIE-1 builds it — the published
        # recipe declares none.
        declared_prompt = raw_prompt if isinstance(raw_prompt, str) else str(raw_prompt)
        query = raw_query if isinstance(raw_query, str) else str(raw_query)
        instructions = raw_instructions if isinstance(raw_instructions, str) else str(raw_instructions)
        model = raw_model if isinstance(raw_model, str) else str(raw_model)
        prompt = declared_prompt or build_prompt(query, retrieved_chunks, instructions)
        # `model` (Day 7, `recipe.agent_config.model`) is forwarded into
        # `kwargs["model"]` only when the recipe hasn't already declared its
        # own `kwargs["model"]` — a recipe-declared kwarg is a deliberate
        # author choice and wins, same precedence as `declared_prompt` above.
        # No current `LLM` impl reads this key (`FixtureLLM.complete` ignores
        # all kwargs; `apps/studio/providers/gemini.py::GeminiProvider.complete`
        # also discards kwargs, using its own `self._model` instead) — this
        # only pre-wires the seam so a future `GatewayLLM` can read it without
        # another interpreter/executor change.
        if model and "model" not in kwargs:
            kwargs["model"] = model

        answer = await self._llm.complete(prompt, **kwargs)
        # A citation survives only if it is BOTH bracket-cited by the answer AND
        # present in `retrieved_chunks`. Empty `retrieved_chunks` therefore
        # grounds nothing and cites nothing — no fallback to raw extraction,
        # which would let an ungrounded (hallucinated) marker into the trace as
        # a "real" citation and score a false-positive citation-accuracy.
        retrieved_ids = {chunk.chunk_id for chunk in retrieved_chunks}
        citations = [cid for cid in _CITATION_RE.findall(answer) if cid in retrieved_ids]
        llm_source = "stub" if type(self._llm).__name__ in _KNOWN_STUB_LLM_CLASS_NAMES else "gateway"
        return {
            "answer": answer,
            "tokens": Tokens(prompt=len(prompt.split()), completion=len(answer.split())),
            "citations": citations,
            "refused": has_kb_upstream and not citations,
            "llm_source": llm_source,
        }


# `when` grammar v0 (engine-local, free-to-change — DEC-A/DL-12.A1-1): exactly
# 3 tokens, `"<field> <op> <literal>"`. Regex is intentionally linear (no
# nested quantifiers, requirement 8) — 0.85ms measured on a 200k-char input
# (red-team round 1), and the hard 200-char cutoff below (requirement 6)
# means it never sees an input anywhere near that size in practice anyway.
# `op` alternation order is load-bearing: `<=`/`>=`/`!=` MUST be listed
# before their 1-char prefix (`<`/`>`/... ) or e.g. `"a <= 1"` silently
# parses as op `<` with a garbage RHS `"= 1"`.
_WHEN_MAX_LEN = 200
_WHEN_RE = re.compile(r'^(?P<field>[A-Za-z_]\w*)\s*(?P<op>==|!=|<=|>=|<|>)\s*(?P<value>"[^"]*"|\S+)$')
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<=": operator.le,
    ">=": operator.ge,
    "<": operator.lt,
    ">": operator.gt,
}
# Sentinel distinct from `None`/any real params value — tells "the `state`
# key is entirely absent from `node.params`" (no upstream node ran yet, this
# `condition` is the first node in the DAG) apart from "the key is present
# and its value happens to be an empty dict" (requirement 7 step 4, F8).
_NO_STATE = object()


def _parse_literal(raw: str) -> object:
    """Resolve a `when` value token to a real Python value — NEVER `eval`/
    `exec`/`ast.literal_eval` (requirement 4). `json.loads` handles both the
    quoted-string and bare-word forms (`"PASS"` -> `"PASS"`; `false` -> the
    Python `bool` `False`); anything that isn't valid JSON falls back to the
    literal raw string, which is only ever used for comparison/echo, never
    executed.

    Both exception types matter (requirement 6, F2 — measured 2026-08-06 on
    this repo's CPython 3.14.4): `json.JSONDecodeError` is a `ValueError`
    subclass, but `json.loads` recurses on deeply-nested input and raises a
    bare `RecursionError` instead once nesting is deep enough — a payload
    like `"["*20000` is NOT caught by `except ValueError` alone. The caller
    additionally cuts `when` off at `_WHEN_MAX_LEN` BEFORE any regex/JSON
    processing (belt 1); this `except` clause is belt 2, in case a future
    change to the grammar ever calls `_parse_literal` on an un-length-checked
    value token."""
    try:
        return json.loads(raw)
    except ValueError, RecursionError:
        return raw


def _condition_result(when: str | None, result: bool | None, reason: str) -> dict[str, object]:
    """The ONE place `ConditionExecutor.execute`'s output dict is built (DRY
    — the alternative is repeating the same 3-key literal at up to 7 return
    sites). `when` is truncated to `_WHEN_MAX_LEN` on the way out (requirement
    9, F9): the echoed value flows straight into `TraceEvent.outputs`, and
    `Edge.when` (`packages/contracts/src/studio_contracts/recipe.py:45`) is
    client-declared data with no length limit of its own — the echo must
    never re-introduce the same unbounded-size hazard the length check above
    exists to close off."""
    return {"when": when[:_WHEN_MAX_LEN] if when is not None else None, "result": result, "reason": reason}


class ConditionExecutor:
    """`condition` node — branches on `edges[].when` evaluated against the
    upstream node's output (e.g. a verdict/score). SWE co-owns the `when`
    expression's grammar (recipe schema/graph-lint); AIE-1 owns evaluating it
    at runtime here (spec AIE-1, R-SPEC A2).

    Grammar v0 (engine-local, free-to-change — DEC-A/DL-12.A1-1, NOT the
    grammar SWE's recipe-validator will eventually standardize on): exactly
    3 whitespace-separated tokens, `"<field> <op> <literal>"`.
    - `field`: `[A-Za-z_]\\w*`, looked up via `state[field]` dict subscript
      — NEVER `getattr` (requirement 4; a subscript lookup makes a
      dunder-shaped field name harmless, where `getattr` on user-declared
      data would not be).
    - `op`: one of `==`, `!=`, `<=`, `>=`, `<`, `>`, dispatched through the
      whitelisted `_OPS` table (`operator.eq`/`ne`/`le`/`ge`/`lt`/`gt`) —
      never a dynamic/string-eval'd operator.
    - `literal`: a bare token (no whitespace) or a double-quoted string;
      resolved via `_parse_literal` (`json.loads` + raw-string fallback).

    Output invariant (requirement 2, locked by `test_result_is_bool_iff_
    reason_is_ok`): `execute` ALWAYS returns `{"when": <str|None>, "result":
    <bool|None>, "reason": <str>}`, and `result` is a `bool` IF AND ONLY IF
    `reason == "ok"` — every other `reason` means "could not evaluate",
    which is deliberately distinct from "evaluated to False". `reason` is
    one of: `"ok"`, `"no-when-declared"`, `"when-too-long"`,
    `"unparsable-grammar"`, `"no-upstream-output"`, `"state-not-a-dict"`,
    `"field-missing"`, `"type-mismatch"`.

    Fail-closed contract for a future router (requirement 2): ANY
    `reason != "ok"` MUST be treated as "do not branch" — `None` is falsy in
    a boolean context, so a caller that just checks truthiness of `result`
    already gets this for free.

    Safety (requirement 4-6): `execute` NEVER calls `eval`/`exec`/
    `ast.literal_eval`/`getattr`-on-user-data, and NEVER raises. Every
    failure mode (bad grammar, missing/wrong-shaped `state`, a `TypeError`
    from comparing incompatible types) becomes a `reason` string instead —
    `interpreter.py:250`'s `await executors[node_type].execute(node)` has no
    surrounding `try/except`, so a raise here would escape mid-walk and kill
    the whole run after earlier nodes already wrote their `TraceEvent`s (the
    truncated-run hazard `interpreter.py:310-318` warns about). A malformed
    `when` therefore reports `reason="unparsable-grammar"` with
    `result=None` — never a silent `False`.

    `reason` is an engine-local implementation detail, not a public/frozen
    API (same status as `RunResult`, `interpreter.py:100-105`) — free to
    change without a contract bump. The one thing anything outside this
    module may rely on is the fail-closed rule above.

    Limitation, stated rather than hidden: this executor only EVALUATES the
    expression. `interpreter.py` (phase 2 of this plan) does not yet branch
    the walk on this result — that wiring is out of scope here."""

    async def execute(self, node: Node) -> object:
        raw_when = node.params.get("when")
        when = raw_when if isinstance(raw_when, str) else None
        if not when:
            return _condition_result(None, None, "no-when-declared")
        if len(when) > _WHEN_MAX_LEN:
            return _condition_result(when, None, "when-too-long")

        match = _WHEN_RE.match(when)
        if match is None:
            return _condition_result(when, None, "unparsable-grammar")

        raw_state = node.params.get("state", _NO_STATE)
        if raw_state is _NO_STATE:
            return _condition_result(when, None, "no-upstream-output")
        if not isinstance(raw_state, dict):
            return _condition_result(when, None, "state-not-a-dict")

        field = match.group("field")
        if field not in raw_state:
            return _condition_result(when, None, "field-missing")

        op = match.group("op")
        literal = _parse_literal(match.group("value"))
        try:
            outcome = _OPS[op](raw_state[field], literal)
            result = bool(outcome)
        except Exception:
            # Broad on purpose (D14 #96 review C-1): both the comparison AND
            # the `bool()` coercion below it can raise. A plain `TypeError`
            # covers the common int-vs-str case, but `Edge.when`'s literal
            # and `state`'s values are both client-declared /
            # composition-root-supplied data whose types are not statically
            # known — a custom `__gt__`/`__eq__`/`__bool__` can raise
            # anything (e.g. `ValueError`, or a numpy array making `bool()`
            # itself ambiguous). This module's own "NEVER raises" contract
            # (docstring above) means every failure mode here — not just
            # `TypeError` — must fail closed into `reason="type-mismatch"`,
            # never escape to `interpreter.py`'s dispatch call (no
            # try/except there, by design — R-4).
            return _condition_result(when, None, "type-mismatch")
        return _condition_result(when, result, "ok")


@runtime_checkable
class ToolDispatch(Protocol):
    """Engine-local seam for the `tool-call` node's dispatcher collaborator
    (NOT a `studio_contracts` protocol — `packages/contracts` has no
    tool-dispatch seam; this is `studio_engine`'s own, same as `NodeExecutor`
    above). `ToolCallExecutor`'s constructor is not frozen by any contract
    test, so this DI param was free to add here (unlike
    `KbRetrieveExecutor`/`LlmStepExecutor`, whose constructors are locked).

    `dispatch` takes `params` (engine#32) — the tool's own args (e.g.
    `calculator`'s `expression`, `current_datetime`'s `mode`/`from_date`/
    `to_date`) live in `node.params` alongside `"tool"`, and a real
    dispatcher needs them; the tool name alone (pre-engine#32 shape) cannot
    carry them."""

    async def dispatch(self, tool: str, params: dict[str, object]) -> object: ...


class WhitelistGuardedDispatch:
    """Belt 2 (spec AIE-1, R-SPEC A2) must hold structurally no matter what
    the caller injects — not merely by caller convention (finding
    @dholmes0207, engine#35 review: once a caller supplies its own
    `tool_dispatch`, `recipe.agent_config.tool_whitelist` previously went
    nowhere inside this package, and `ToolCallExecutor`'s own docstring
    claim — "the dispatcher RAISES for a tool outside its whitelist" —
    silently became false for that caller). Wraps whichever `ToolDispatch`
    `run()` ends up with (injected or the `WhitelistToolDispatch` fallback)
    so the whitelist check always runs inside `packages/engine`, before the
    inner dispatcher ever sees the tool name. Wrapping the fallback stub too
    is deliberate redundancy (same list, checked twice) — one code path, no
    special-casing which dispatcher source needs the guard.

    Moved here from `interpreter.py` (engine#33 phase 2, made module-public —
    was module-private with a leading underscore) so `agent_loop.run_agent_loop()`
    (phase 3) can reuse it without importing a private name across modules;
    logic is unchanged, copied verbatim."""

    def __init__(self, inner: ToolDispatch, whitelist: list[str]) -> None:
        self._inner = inner
        self._whitelist = whitelist

    async def dispatch(self, tool: str, params: dict[str, object]) -> object:
        if tool not in self._whitelist:
            raise ValueError(f"tool not in whitelist: {tool}")
        return await self._inner.dispatch(tool, params)


class ToolCallExecutor:
    """`tool-call` node — dispatches a tool strictly within
    `agent_config.tool_whitelist` (rule-verdict/matching). SWE co-owns
    whitelist enforcement at the recipe-validator layer; a tool outside the
    whitelist must never execute here either (defense in depth, spec AIE-1,
    R-SPEC A2)."""

    def __init__(self, dispatcher: ToolDispatch | None = None) -> None:
        self._dispatcher = dispatcher

    async def execute(self, node: Node) -> object:
        """Output shape depends on the dispatcher: real dispatchers (engine#32
        — `apps/studio`'s `RealToolDispatch`) return e.g. `{"expression",
        "result"}` for `calculator`; the engine-internal fallback stub
        (`WhitelistToolDispatch`) returns `{"tool": <name>, "status":
        "stub-dispatched"}`. Either way the dispatcher RAISES for a tool
        outside its whitelist (defense-in-depth — the recipe-validator layer
        is the primary whitelist enforcement, this is the second belt, same
        pattern as the closed-`NodeType` registry guard). No dispatcher wired
        at construction (`dispatcher=None`, the default — kept so
        `ToolCallExecutor()`'s pre-phase-1 0-arg call shape stays valid, per
        `test_tool_call_no_dispatcher_still_not_implemented`) still raises
        `NotImplementedError`, same as before this phase filled the body.
        `node.params` (minus nothing — the whole dict, `"tool"` key
        included) is passed through to `dispatch()` so the tool's own args
        reach the dispatcher (engine#32)."""
        if self._dispatcher is None:
            raise NotImplementedError(
                "spec AIE-1: tool-call executor requires a dispatcher collaborator — see R-SPEC A2"
            )
        raw_tool = node.params.get("tool", "")
        tool = raw_tool if isinstance(raw_tool, str) else str(raw_tool)
        return await self._dispatcher.dispatch(tool, node.params)


class HitlPauseExecutor:
    """`hitl-pause` node — dừng-chờ-người first-class (INV-2): the real body
    pauses the run, emits a pause `TraceEvent`, and yields control back to
    the playground for an external approval before the interpreter resumes
    along this node's downstream edge. SWE wires the playground-side
    approval UI; AIE-1 owns this pause/emit/yield executor body (spec AIE-1,
    R-SPEC A2).

    Output shape (v0): `{"paused": True, "status": "pending_approval"}` — a
    real pause-SHAPED value, JSON-serializable so it can flow into
    `TraceEvent.outputs` like every other executor's output. This does NOT
    actually pause anything: `interpreter.py`'s walk loop (phase 2 of this
    plan, and INV-2 more broadly) has no knowledge of this executor and does
    not stop, wait, or yield on it — that wiring (halt the walk, persist a
    resumable run state, resume on external approval) is a separate,
    still-open deliverable. This class's only job right now is producing the
    output shape a future pause/resume mechanism will key off of."""

    async def execute(self, node: Node) -> object:
        del node
        return {"paused": True, "status": "pending_approval"}


class EndExecutor:
    """`end` node — terminal node: the real body emits the run's final
    `TraceEvent` and assembles the result the interpreter returns from
    `run()` (spec AIE-1, R-SPEC A2)."""

    async def execute(self, node: Node) -> object:
        """Output shape (v0 stub): `{"terminated": True}` — the terminal
        marker `interpreter.run()` (phase 2) uses to stop walking the DAG."""
        del node
        return {"terminated": True}
