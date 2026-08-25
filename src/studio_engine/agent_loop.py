"""`run_agent_loop()` (engine#33) — the 1-LLM-N-tool agent loop: the model
itself decides which tool to call, when, how many times, and stops by simply
not emitting another `TOOL_CALL:` signal. This REPLACES DAG-walk for this
architecture; `interpreter.run()` (`interpreter.py:185-503`) is UNCHANGED and
stays the fallback 6-node walk (K2, issue #33's own hard requirement) — this
module never imports its behavior, only 2 of its collaborators
(`RunResult`, reused verbatim; `WhitelistToolDispatch`, the same
engine-internal fallback dispatcher `run()` falls back to).

Handoff to `agentcore-studio-app#44` (the repo that will wire this into real
HTTP routes) — 4 things that repo MUST know before calling this:

1. `question: str` is a NEW keyword-only, NO-default parameter — `run()` has
   no equivalent (it reads the question from the `kb-retrieve` node's
   `params["query"]`, which this loop is forbidden to have since it never
   reads the recipe's DAG field at all, K8/A3).
2. **3 distinct exception types** can escape this function, not 1:
   `AgentLoopExhausted` (ran out of `max_turns`, carries `.partial`/`.turns`),
   `ValueError` (a tool outside `agent_config.tool_whitelist` —
   `executors.WhitelistGuardedDispatch`), and `PermissionError` (the
   session's `tenant_id` was not a real `UUID` after fencing —
   `executors.py:170-178`, `KbRetrieveExecutor`). #44 must map all 3 to a
   proper HTTP error, not let any of them surface as a bare 500.
3. **This loop never emits a terminal event.** `run()` always ends on 1
   END-type event (`interpreter.py:487-488`); this loop has no `end`
   node, so its last event is always an `LLM_STEP` (the final-answer turn).
   A consumer that polls for that terminal node type to know "is this run
   done" will wait forever — read the returned `RunResult` (or catch
   `AgentLoopExhausted`) instead. This module only ever emits 3 of the 6
   closed `NodeType` values: `LLM_STEP`, `KB_RETRIEVE`, `TOOL_CALL` (K6).
4. `refused` on the final-answer entry uses a DIFFERENT formula than
   `LlmStepExecutor.execute` (`executors.py:364`, `not citations`) — see A5
   below. Comparing the 2 paths' refusal rate must read `citations` directly,
   never `refused`.

Design decisions locked by `plans/260823-1249-engine33-agent-loop/plan.md`
(A1-A5, DEC-2/3/4) — this module implements them, does not re-derive them:

- A1 (`agent_protocol.parse_agent_signal`): `TOOL_CALL:` is the ONE signal.
- A2: `max_turns` defaults to `DEFAULT_MAX_TURNS`, is keyword-only, and
  running out RAISES (`AgentLoopExhausted`) rather than returning a silently
  truncated `RunResult` — `run()`'s own docstring
  (`interpreter.py:491-499`) names exactly this failure mode as an ACCEPTED
  risk for the DAG-walk fallback (DEC-A); this loop is greenfield and does
  not inherit that defect.
- A3: `question` keyword-only, no default (see Handoff #1 above).
- A4: `kb_search` is ALWAYS available and is never gated by
  `tool_whitelist` — inferred from `run()`'s own wiring
  (`interpreter.py:251-261` pre-refactor / now via `fenced_kb_params`):
  `KbRetrieveExecutor` never receives a whitelist, only `ToolCallExecutor`
  does. `recipe.kb_binding` is a REQUIRED field on every `Recipe`
  (`recipe.py:106`), so `kb_search` is structurally always in scope.
- A5: `refused = used_kb_search and (not citations) and (not used_non_kb_tool)`
  — see the branch below for the full truth table this pins (money-shot
  cross-tenant case, calculator-only case, mixed case, no-tool case).
  `used_kb_search` (engine#37, added after PR review on
  `agentcore-studio-engine#39` caught the gap: `test_no_tool_direct_answer_is_refused`
  was locking a direct conversational answer — zero tool calls at all,
  including `kb_search` — as `refused=True`. That is the exact symptom
  engine#37 reports, just reached through this loop's OWN formula instead of
  `LlmStepExecutor`'s `not citations`: this is the real production path now
  (`agentcore-studio-app#48` moved `routes/chat.py`/`eval_adapter.py` off
  `interpreter.run()` onto this loop), so fixing only `interpreter.py` left
  the live symptom unfixed. `used_kb_search` gates the same way
  `has_kb_upstream` gates `interpreter.py`'s formula (see that module) — a
  turn that never called `kb_search` never entered the fence-refusal branch
  this flag exists to measure, so it must not read as one.

Deliberate cross-turn duplication in THIS file (plan.md "Trùng lặp CHẤP NHẬN",
accepted so `interpreter.py`/`executors.py` stay as untouched as possible per
K2) — each copy below carries a comment pointing at its source. 2 more items
of the same accepted-duplication list live in `agent_protocol.py`
(`[chunk_id]\n<text>` excerpt shape, `_CITATION_RE`, the belt-1/belt-2
tool-call-payload parse pattern) — see that module's own docstring, not
repeated here:
1. `ts` 1-microsecond bump — `interpreter.py:465-468`.
2. `isinstance(item, KbSearchResultItem)` filter building `outputs["chunks"]`
   — `interpreter.py:426`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from studio_contracts import (
    LLM,
    EmbeddingService,
    KbSearch,
    KbSearchResultItem,
    Node,
    NodeType,
    Recipe,
    Tokens,
    TraceEvent,
    TraceWriter,
)
from studio_contracts.cost import cost_of

from studio_engine.agent_protocol import (
    KB_SEARCH_TOOL,
    FinalAnswer,
    Observation,
    ToolCall,
    build_agent_prompt,
    build_faithfulness_prompt,
    ground_citations,
    parse_agent_signal,
    parse_faithfulness_verdict,
    render_kb_observation,
)
from studio_engine.demo_stubs import WhitelistToolDispatch
from studio_engine.executors import KbRetrieveExecutor, ToolCallExecutor, ToolDispatch, WhitelistGuardedDispatch
from studio_engine.fence import fenced_kb_params
from studio_engine.interpreter import RunResult
from studio_engine.session import SessionContext

# A2 (DEC-3) originally pinned this at 6 (worst-case realistic spine path: 5 LLM
# turns — kb_search -> kb_search refine -> calculator -> current_datetime ->
# answer — +1 slack). Bumped 6 -> 20 by explicit product decision (Trần Bá Đạt,
# 2026-08-25), NOT re-derived from a new worst-case-turn-count analysis — no
# measurement backs "20" the way "6" was measured. Now EQUAL to
# `_MAX_TURNS_CEILING` below, so DEFAULT_MAX_TURNS effectively IS the ceiling:
# no caller has headroom left to override upward anymore. Applies to every
# caller that omits `max_turns` — confirmed both are production paths:
# `apps/studio/src/studio_app/routes/chat.py` (live user chat) AND
# `apps/studio/src/studio_app/eval_adapter.py::run_case` (golden-set eval-gate,
# gates publish) — an eval batch's worst-case LLM-call count per case also
# rises ~3.3x, not just single-chat latency/cost.
DEFAULT_MAX_TURNS = 20
# M4/R9: hard ceiling on what a caller may override `max_turns` to — without
# this, `max_turns=10_000` turns the cap into a no-op (long-running, token-
# burning loop) even though a cap exists at all.
_MAX_TURNS_CEILING = 20
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 10
# M5: hard cap per observation, so prompt size grows at most linearly and
# boundedly (~`max_turns * _MAX_OBSERVATION_CHARS`) instead of unboundedly
# with every retrieved chunk's full text threaded into every later turn.
_MAX_OBSERVATION_CHARS = 2000
_TRUNCATION_SUFFIX = "…[cắt bớt]"


class AgentLoopExhausted(RuntimeError):
    """Raised when `max_turns` LLM calls happen without a final answer (A2,
    DEC-3) — never a silently truncated `RunResult` (see module docstring).
    `partial` carries every `TraceEvent` already emitted (all already
    `trace_writer.write()`-ten before this raises, so nothing is lost from an
    audit standpoint) and the accumulated `final_state` so far."""

    def __init__(self, message: str, *, partial: RunResult, turns: int) -> None:
        super().__init__(message)
        self.partial = partial
        self.turns = turns


def _bump_ts(last_ts: datetime | None) -> datetime:
    """Same monotonic-`ts` trick as `interpreter.py:465-468`: if `now` would
    not strictly exceed the previous event's timestamp (clock resolution on
    some platforms is coarser than this loop's turn rate), bump 1 microsecond
    past it instead."""
    now = datetime.now(UTC)
    if last_ts is not None and now <= last_ts:
        return last_ts + timedelta(microseconds=1)
    return now


def _hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def _hash_params(params: dict[str, object]) -> str:
    """Same shape as `interpreter.py:478`'s `inputs_hash` for non-KB nodes."""
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()


def _normalise_top_k(raw_top_k: object) -> int:
    """M2 — explicit, not `int(x or 5)` (that silently turns `0` into `5` via
    truthiness, which reads backwards to anyone auditing this later). Table:
    `9999 -> 10`, `0 -> 5`, `-3 -> 5`, `"abc" -> 5`, `None -> 5`, `3 -> 3`.
    `isinstance`-narrowed (not a blanket `int(raw_top_k)` + `except (TypeError,
    ValueError)`) so mypy strict can prove `int(...)` is called on a type it
    actually accepts — `None`/a dict/a list fall straight to the same default
    a `TypeError` would have produced anyway, just via the `else` branch."""
    if isinstance(raw_top_k, int | float | str):
        try:
            n = int(raw_top_k)
        except ValueError:
            n = _DEFAULT_TOP_K
    else:
        n = _DEFAULT_TOP_K
    if n < 1:
        n = _DEFAULT_TOP_K
    return min(n, _MAX_TOP_K)


def _truncate_observation(text: str) -> str:
    if len(text) <= _MAX_OBSERVATION_CHARS:
        return text
    return text[:_MAX_OBSERVATION_CHARS] + _TRUNCATION_SUFFIX


def _jsonsafe(value: object) -> object:
    """Same `Tokens`-aware JSON-safety pass `interpreter.py:458-463` applies
    to executor output dicts, so a `TOOL_CALL:` dispatcher result can flow
    into `TraceEvent.outputs` (later serialized via `Jsonb`) unchanged for
    every other shape."""
    if isinstance(value, Tokens):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonsafe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonsafe(item) for item in value]
    return value


async def _verify_citation_faithfulness(
    llm: LLM, *, question: str, citations: list[str], retrieved: list[KbSearchResultItem], **kwargs: object
) -> bool:
    """engine#43 (HB2-25) — second grounding pass, orthogonal to
    `ground_citations`: PROVENANCE (chunk_id was retrieved) says nothing
    about SUBJECT match (the cited chunk actually answers what was asked).
    Money-shot: scope `ankor/engineering`, question about "Borea", model
    answers from `ankor-engineering-incident#c6` — a REAL, RETRIEVED,
    validly-provenanced chunk (`ground_citations` returns it unchanged,
    citation_accuracy scores 1.0) whose subject is still wrong.

    1 extra `llm.complete()` call per final-answer turn that has >=1
    citation — skipped entirely (no call, no cost) when `citations` is
    empty, which is the common no-citation-refusal shape (SC-04/SC-05) this
    loop already emits constantly. NOT free on every turn — surfaced here
    plainly rather than hidden behind a flag: no feature flag exists for
    this yet, that tradeoff is left for PR review to weigh.

    Prompt-build/verdict-parse are PURE and live in `agent_protocol.py`
    (that module's own hard "no I/O, no async" contract, DEC-2/A1); this
    function is the async orchestration around them and belongs here, next
    to every other `llm.complete()` call this loop already makes. `**kwargs`
    is the SAME `{"model": ...}` dict the main-turn call already built —
    the verify call honors the same recipe-declared model."""
    if not citations:
        return True
    cited = [chunk for chunk in retrieved if chunk.chunk_id in set(citations)]
    prompt = build_faithfulness_prompt(question, cited)
    raw = await llm.complete(prompt, **kwargs)
    return parse_faithfulness_verdict(raw)


async def run_agent_loop(
    recipe: Recipe,
    *,
    session_context: SessionContext,
    kb_search: KbSearch,
    llm: LLM,
    embedding: EmbeddingService,
    trace_writer: TraceWriter,
    question: str,
    tool_dispatch: ToolDispatch | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> RunResult:
    """Run the agent loop to completion (a final answer) or raise
    `AgentLoopExhausted` after `max_turns` LLM calls. See module docstring for
    the full handoff contract, decision rationale, and accepted duplication.

    `embedding` is accepted but UNUSED this phase — wired via constructor-DI
    like `LlmStepExecutor.__init__` (`executors.py:229-231`, "unused here")
    so this call site stays shape-compatible with `interpreter.run()`'s and
    does not need a signature change once an embed-using turn exists.
    """
    if max_turns < 1 or max_turns > _MAX_TURNS_CEILING:
        raise ValueError(f"max_turns must be in [1, {_MAX_TURNS_CEILING}], got {max_turns!r}")

    run_id = str(uuid.uuid4())
    whitelist = recipe.agent_config.tool_whitelist
    kb_exec = KbRetrieveExecutor(kb_search)
    tool_exec = ToolCallExecutor(
        WhitelistGuardedDispatch(
            tool_dispatch if tool_dispatch is not None else WhitelistToolDispatch(whitelist),
            whitelist,
        )
    )
    tool_names = [KB_SEARCH_TOOL, *whitelist]

    observations: list[Observation] = []
    retrieved: list[KbSearchResultItem] = []
    used_non_kb_tool = False
    # engine#37 — twin flag to `used_non_kb_tool` above, same reason: gates
    # `refused` so a turn that never called `kb_search` at all cannot read as
    # a fence-refusal (there was no fence to trigger). See the A5 note in this
    # module's docstring for the full incident.
    used_kb_search = False
    events: list[TraceEvent] = []
    state: dict[str, object] = {}
    last_ts: datetime | None = None

    for i in range(1, max_turns + 1):
        prompt = build_agent_prompt(
            system_prompt=recipe.agent_config.system_prompt,
            question=question,
            tool_names=tool_names,
            observations=observations,
        )
        kwargs: dict[str, object] = {"model": recipe.agent_config.model} if recipe.agent_config.model else {}
        raw = await llm.complete(prompt, **kwargs)
        signal = parse_agent_signal(raw)
        tokens = Tokens(prompt=len(prompt.split()), completion=len(raw.split()))
        last_ts = _bump_ts(last_ts)
        llm_event_id = f"t{i}-llm"

        if isinstance(signal, FinalAnswer):
            citations = ground_citations(signal.text, retrieved)
            if citations and not await _verify_citation_faithfulness(
                llm, question=question, citations=citations, retrieved=retrieved, **kwargs
            ):
                # engine#43 (HB2-25): `ground_citations` only proves PROVENANCE
                # (the id was retrieved) — it does not prove the cited chunk
                # actually answers the question's SUBJECT. This 2nd LLM call
                # catches the "valid chunk_id, wrong subject" hallucination
                # `ground_citations` structurally cannot see. Stripping here
                # feeds straight into A5's `refused` formula below unchanged —
                # a faithfulness failure now reads exactly like "no citation
                # was grounded", the same refusal shape SC-04/SC-05 already use.
                citations = []
            # A5 (DEC-4): NOT `not citations` (that is `LlmStepExecutor`'s
            # DAG-walk formula, `executors.py:364`, which assumes the ONLY
            # path is `kb-retrieve -> llm-step` — false here, where a fully
            # valid answer can come entirely from a non-KB tool and cite
            # nothing). See plan.md A5 for the full 4-case truth table this
            # pins; do NOT "fix" this back to `not citations`.
            #
            # `used_kb_search and` (engine#37): a turn that never called
            # `kb_search` at all — including the standalone-chatbot shape,
            # zero tool calls, `agentcore-studio-web#14` — must not read as a
            # refusal either; `not citations` alone is true there for the same
            # reason it is true after a fenced-empty `kb_search`, and this
            # loop cannot tell those two apart without the flag.
            refused = used_kb_search and (not citations) and (not used_non_kb_tool)
            out: dict[str, object] = {
                "answer": signal.text,
                "citations": citations,
                "refused": refused,
                "signal": "final-answer-malformed" if signal.malformed_tool_call else "final-answer",
            }
            event = TraceEvent(
                event_id=str(uuid.uuid4()),
                run_id=run_id,
                agent_id=recipe.agent_id,
                tenant_id=session_context.tenant_id,
                node_id=llm_event_id,
                node_type=NodeType.LLM_STEP,
                ts=last_ts.isoformat(timespec="microseconds"),
                inputs_hash=_hash_prompt(prompt),
                outputs=out,
                tokens=tokens,
                cost=cost_of(tokens),
                citations=citations,
            )
            await trace_writer.write(event)
            events.append(event)
            state[llm_event_id] = out
            return RunResult(run_id=run_id, events=events, final_state=state)

        # ToolCall branch — K7: `citations=None` (only a final-answer event
        # ever carries citations). No `"answer"` key in `out` (R4/handoff #4:
        # `eval_adapter._llm_answer` picks the FIRST `final_state` entry with
        # an `"answer"` key, so a tool-call turn must never have one).
        assert isinstance(signal, ToolCall)
        tool_call_out: dict[str, object] = {
            "tool_call": {"tool": signal.tool, "params": signal.params},
            "raw": raw,
            "signal": "tool-call",
        }
        llm_event = TraceEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            agent_id=recipe.agent_id,
            tenant_id=session_context.tenant_id,
            node_id=llm_event_id,
            node_type=NodeType.LLM_STEP,
            ts=last_ts.isoformat(timespec="microseconds"),
            inputs_hash=_hash_prompt(prompt),
            outputs=tool_call_out,
            tokens=tokens,
            cost=cost_of(tokens),
            citations=None,
        )
        await trace_writer.write(llm_event)
        events.append(llm_event)
        state[llm_event_id] = tool_call_out

        # M3 — the LAST permitted turn raises BEFORE dispatch, never after: a
        # tool call has nothing left to be read back into (no turn `i+1`
        # exists), so executing it would be a real side effect (a KB query, a
        # calculator call) paid for nothing. `2*max_turns - 1` events total
        # when this fires (every earlier turn contributed 2, this one only 1).
        if i == max_turns:
            raise AgentLoopExhausted(
                f"agent loop exhausted after {max_turns} turn(s) without a final answer",
                partial=RunResult(run_id=run_id, events=events, final_state=state),
                turns=max_turns,
            )

        if signal.tool == KB_SEARCH_TOOL:
            top_k = _normalise_top_k(signal.params.get("top_k", _DEFAULT_TOP_K))
            fenced_params = fenced_kb_params({**signal.params, "top_k": top_k}, session_context)
            kb_node = Node(id=f"t{i}-kb-search", type=NodeType.KB_RETRIEVE, params=fenced_params)
            raw_result = await kb_exec.execute(kb_node)
            # engine#37: set as soon as `kb_search` actually ran, NOT gated on
            # `valid_chunks` being non-empty — a fenced-to-zero result (the
            # money-shot SC-04/SC-05 case, `test_empty_kb_retrieval_answer_is_refused`)
            # must still count as "kb_search ran and found nothing", same as
            # `interpreter.py`'s `has_kb_upstream` being set unconditionally
            # once `kb-retrieve` executes, not once it returns chunks.
            used_kb_search = True
            # Copy of `interpreter.py:425-426`'s filter (same outer `isinstance(...,
            # list)` guard, same inner `isinstance(item, KbSearchResultItem)`) — a
            # non-`KbSearchResultItem` element (a broken double, or a serialize
            # hazard) is silently dropped from the TRACE and from `retrieved`
            # (grounding) rather than raising mid-loop; `"fenced"` means "0 valid
            # chunks in this event", same semantics as `interpreter.py:434-439`
            # (not "the fence just blocked something").
            valid_items = (
                [item for item in raw_result if isinstance(item, KbSearchResultItem)]
                if isinstance(raw_result, list)
                else []
            )
            retrieved.extend(valid_items)
            valid_chunks = [item.model_dump(mode="json") for item in valid_items]
            kb_outputs: dict[str, object] = {"chunks": valid_chunks}
            if not valid_chunks:
                kb_outputs["fenced"] = True
            last_ts = _bump_ts(last_ts)
            kb_tokens = Tokens(prompt=0, completion=0)
            kb_event = TraceEvent(
                event_id=str(uuid.uuid4()),
                run_id=run_id,
                agent_id=recipe.agent_id,
                tenant_id=session_context.tenant_id,
                node_id=kb_node.id,
                node_type=NodeType.KB_RETRIEVE,
                ts=last_ts.isoformat(timespec="microseconds"),
                inputs_hash=_hash_params(fenced_params),
                outputs=kb_outputs,
                tokens=kb_tokens,
                cost=cost_of(kb_tokens),
                citations=None,
            )
            await trace_writer.write(kb_event)
            events.append(kb_event)
            state[kb_node.id] = kb_outputs
            # M7 — `Observation.params` carries the LLM's ORIGINAL (pre-fence)
            # request, not `fenced_params`: this is what threads back into the
            # next prompt/audit trail, and it must show what the model asked
            # for, not what the fence allowed. The fenced values already live
            # on `kb_event.inputs_hash` and were what `kb_exec` actually used.
            result_text = _truncate_observation(render_kb_observation(valid_items))
            observations.append(Observation(tool=signal.tool, params=dict(signal.params), result_text=result_text))
        else:
            tool_params: dict[str, object] = {**signal.params, "tool": signal.tool}
            tool_node = Node(id=f"t{i}-tool-{signal.tool}", type=NodeType.TOOL_CALL, params=tool_params)
            # No try/except here, by design (R-4, `executors.py:519-522`): a
            # dispatch failure (e.g. `ValueError` — tool outside whitelist)
            # propagates straight out of this function, out of scope to
            # catch-and-feed-back-to-the-model this phase (plan.md Out of
            # scope, "Error-recovery trong loop").
            result = await tool_exec.execute(tool_node)
            safe_result = _jsonsafe(result)
            last_ts = _bump_ts(last_ts)
            tool_tokens = Tokens(prompt=0, completion=0)
            tool_event = TraceEvent(
                event_id=str(uuid.uuid4()),
                run_id=run_id,
                agent_id=recipe.agent_id,
                tenant_id=session_context.tenant_id,
                node_id=tool_node.id,
                node_type=NodeType.TOOL_CALL,
                ts=last_ts.isoformat(timespec="microseconds"),
                inputs_hash=_hash_params(tool_params),
                outputs=safe_result if isinstance(safe_result, dict) else {"result": safe_result},
                tokens=tool_tokens,
                cost=cost_of(tool_tokens),
                citations=None,
            )
            await trace_writer.write(tool_event)
            events.append(tool_event)
            state[tool_node.id] = tool_event.outputs
            used_non_kb_tool = True
            result_text = _truncate_observation(json.dumps(safe_result, ensure_ascii=False, default=str))
            observations.append(Observation(tool=signal.tool, params=dict(signal.params), result_text=result_text))

    # Unreachable in practice (the `i == max_turns` branch above always fires
    # first and raises), kept as a structural safety net — `for` is bounded
    # by construction so this function can never spin forever regardless.
    raise AgentLoopExhausted(
        f"agent loop exhausted after {max_turns} turn(s) without a final answer",
        partial=RunResult(run_id=run_id, events=events, final_state=state),
        turns=max_turns,
    )
