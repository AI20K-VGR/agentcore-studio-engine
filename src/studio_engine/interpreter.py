"""Interpreter loop (spec AIE-1, R-SPEC A2) — walks `recipe.dag` by
following `dag.edges` from a single start node (the one node id no edge's
`.to` targets) until an `end` node executes, dispatching each visited node
to its constructor-DI'd executor and accumulating outputs into a plain
`dict` (`RunState`, an insertion-order accumulator — a plain `dict` already
preserves insertion order, no `OrderedDict` needed).

Day 6 (spec AIE-1, plan risk R2 lifted): walk order is now DERIVED from
`recipe.dag.edges` — Day 3's hardcoded `_WALK_ORDER` `NodeType` tuple is
gone.

Day 14 (plan `260806-0938-d14-aie1-node-executors-grid-prep` phase 2):
`condition` nodes now receive real `state` (the walk's last output) and
`when` (from the outgoing edge the walk actually takes, `_build_edge_map`)
injected into `node.params` before dispatch, the same pattern `kb-retrieve`
and `llm-step` already use below — `ConditionExecutor` is no longer a
`NotImplementedError` stub (P1 of the same plan filled its body). The walk
still does NOT branch on a `condition`'s own `result` (DEC-A is unchanged):
this remains a finite single-successor chain regardless of what any
`condition` node evaluates to.

Day 12 (spec AIE-1, DEC-A): `run()` no longer validates `recipe.dag`'s
structure itself. It TRUSTS that `recipe.dag` already passed `graph_lint`
(spec SWE, `packages/workbench/src/studio_workbench/validator.py`) before
reaching here, and walks it as-is — no start-node count check, no
outgoing-edge-count check, no cycle guard, no "must terminate on `end`"
check. A recipe that violates one of those turns into whatever Python error
that produces naturally (e.g. `IndexError`), or a silent-but-deterministic
walk (last-declared edge wins on ambiguous branching; the walk simply stops
and returns when it runs off the end of the chain) — never a hand-rolled
fallback that hides the mismatch. See
`docs/decisions/decision-log.md` (entry DL-12.A1-1) for the full DEC-A/B/C
rationale and the accepted risk; this docstring does not repeat it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from studio_contracts import (
    LLM,
    Dag,
    Edge,
    EmbeddingService,
    KbSearch,
    KbSearchResultItem,
    NodeType,
    Recipe,
    Tokens,
    TraceEvent,
    TraceWriter,
)

from studio_engine.demo_stubs import WhitelistToolDispatch
from studio_engine.executors import (
    ConditionExecutor,
    EndExecutor,
    HitlPauseExecutor,
    KbRetrieveExecutor,
    LlmStepExecutor,
    NodeExecutor,
    ToolCallExecutor,
    ToolDispatch,
    WhitelistGuardedDispatch,
)
from studio_engine.fence import fenced_kb_params
from studio_engine.session import SessionContext

# Day 5 (out of scope for cost-lineage — obs.costs stays a schema-shell until
# DE builds real cost aggregation): every TraceEvent this phase emits carries
# this fixed cost, never a computed one.
_NO_COST = 0.0

# Day 14 (F8): sentinel distinct from `None`/`{}`/any real executor output —
# tells "no node has run yet this walk" (a `condition` is the DAG's start
# node) apart from "the last node ran and returned an empty dict/list". A
# bare `{}` cannot make that distinction: `ConditionExecutor` would read an
# empty dict as "upstream ran, state has no fields" (`reason="field-missing"`)
# instead of the true "no upstream at all" (`reason="no-upstream-output"`) —
# fail-closed only by accident, the exact anti-pattern the old nil-UUID
# sentinel (`executors.py:124-131`) was already retired for once. See
# `_build_edge_map` above for the sibling F4 fix this same phase closes.
_NO_UPSTREAM = object()


def _find_start_node_id(dag: Dag) -> str:
    """The DAG's sole entry point: the one node id no edge's `.to` targets.

    Day 12 (DEC-A): no longer validates `len(starts) == 1` — that is now
    `graph_lint`'s job (spec SWE), not this module's. `starts[0]` is
    returned directly: if `starts` is empty (every node has an incoming
    edge — a cycle with no entry point), Python's own `IndexError` on the
    empty list surfaces naturally, no hand-rolled fallback. If `starts` has
    >1 candidate, the first one in `dag.nodes` declaration order is picked
    silently — an ambiguous recipe that reaches here is assumed already
    invalid per `graph_lint`, not re-diagnosed here."""
    targets = {edge.to for edge in dag.edges}
    starts = [node.id for node in dag.nodes if node.id not in targets]
    return starts[0]


def _build_next_map(edges: list[Edge]) -> dict[str, str]:
    """`from_ -> to` single-successor lookup.

    Day 12 (DEC-A): no longer rejects >1 outgoing edge from the same node
    (`condition`-node branching, `Edge.when`, is still unevaluated this
    phase — `ConditionExecutor` stays `NotImplementedError`). Plain dict
    assignment per edge means the LAST-declared edge for a given `from_`
    wins (last-write-wins), silently — enforcing "at most 1 outgoing edge"
    is now `graph_lint`'s job (spec SWE), not this module's."""
    next_by_id: dict[str, str] = {}
    for edge in edges:
        next_by_id[edge.from_] = edge.to
    return next_by_id


def _build_edge_map(edges: list[Edge]) -> dict[str, Edge]:
    """`from_ -> Edge` lookup — hands a `condition` node the FULL `Edge` it
    is about to walk (`.to` AND `.when` together), not merely its `when`.

    Day 14 (plan `260806-0938-d14-aie1-node-executors-grid-prep` phase 2,
    red-team F4): deliberately NOT `dict[str, str]` filtered to
    `when is not None`. This map exists ONLY to hand the right `Edge` to
    `ConditionExecutor` — it does NOT route the walk; `_build_next_map`
    above remains the sole routing mechanism (DEC-A, single-successor).
    Uses the exact SAME last-write-wins iteration over ALL edges as
    `_build_next_map`: a node can have >1 outgoing edge (e.g. one with a
    `when`, declared first, and a plain fallback edge, declared last); a
    map that pre-filters to only `when is not None` edges could pick a
    DIFFERENT edge than the one `_build_next_map` picks for the same
    `from_`, so `.to` and `.when` would come from two different edges. A
    condition would then be shown the predicate of an edge the walk never
    took. Building both maps off the identical last-write-wins rule over
    the identical edge list guarantees `_build_edge_map(edges)[n].to ==
    _build_next_map(edges)[n]` always — locked by
    `test_condition_dag_e2e.py::test_when_comes_from_the_edge_the_walk_actually_takes`."""
    edge_by_id: dict[str, Edge] = {}
    for edge in edges:
        edge_by_id[edge.from_] = edge
    return edge_by_id


@dataclass(frozen=True)
class RunResult:
    """`interpreter.run()`'s return shape. This is `studio_engine`'s own
    type, NOT one of the 4 seam contracts frozen at P2 (R-SPEC A1) — free to
    change shape without a mini-RFC.

    `final_state` is the `RunState` accumulator: `node_id -> executor output`
    in dispatch (insertion) order, one entry per node the edge-derived walk
    actually visited.
    """

    run_id: str
    events: list[TraceEvent] = field(default_factory=list)
    final_state: dict[str, object] = field(default_factory=dict)


async def run(
    recipe: Recipe,
    *,
    session_context: SessionContext,
    kb_search: KbSearch,
    llm: LLM,
    embedding: EmbeddingService,
    trace_writer: TraceWriter,
    tool_dispatch: ToolDispatch | None = None,
) -> RunResult:
    """Walk `recipe.dag` from its single start node, following `dag.edges`
    node-by-node, until an `end` node executes.

    `session_context` (Day 8, INV-1 Tenant-Wall; Day 17, T6 label-spoof) is
    server-resolved caller identity AND scope — mandatory, keyword-only, NO
    default, so no call site can silently skip the fence. Every tenant
    identity AND `section_roles` scope this function emits (the
    `kb-retrieve` node's injected `tenant_id`/`section_roles` params) comes
    from `session_context.tenant_id`/`session_context.roles`, NEVER from the
    recipe's own client-declared tenant/scope fields — a mismatch (client
    declares one tenant or a wider scope, session resolves to another) is not
    an error, it is simply ignored: the run proceeds scoped to the session's
    tenant and roles. Every `TraceEvent`'s `tenant_id` likewise comes from
    `session_context.tenant_id`. Those recipe fields (`Recipe.tenant_id`, and
    `kb-retrieve`'s `params["section_roles"]` — built by workbench's
    `_parse_kb_scope` from the client-declared `kb_binding.scope` string) are
    client-supplied data (`studio_contracts.recipe.Recipe`, built by
    workbench/client) and this function does not trust either of them; see
    `studio_engine.session` for why that is the actual fence, not merely a
    convention.

    Constructs all 6 executors explicitly (constructor-DI, plan decision
    #2 — NOT a generic factory): `KbRetrieveExecutor(kb_search)`,
    `LlmStepExecutor(llm, embedding)`, a `ToolCallExecutor` wired with
    `tool_dispatch` if the caller passed one (engine#32 — production
    composition roots, e.g. `apps/studio`, always inject their own real
    `ToolDispatch`), else falling back to
    `WhitelistToolDispatch(recipe.agent_config.tool_whitelist)` (kept as the
    engine-internal default so the pre-engine#32 call shape — no
    `tool_dispatch` kwarg — stays valid for every existing caller/test).
    Either way, that dispatcher is wrapped in `WhitelistGuardedDispatch`
    (engine#35 review fixup) so `recipe.agent_config.tool_whitelist` is
    enforced by `run()` itself regardless of what the caller injected — belt
    2 (R-SPEC A2) no longer depends on the injected dispatcher choosing to
    check it. `ConditionExecutor()`, `HitlPauseExecutor()`, and
    `EndExecutor()` round out the 6. Day 14
    (plan `260806-0938-d14-aie1-node-executors-grid-prep`, P1) filled both
    `ConditionExecutor.execute` and `HitlPauseExecutor.execute` — neither
    raises `NotImplementedError` anymore, so a recipe that routes through
    either dispatches for real. `condition` additionally has its `state`/
    `when` params injected below (same pattern as `kb-retrieve`/`llm-step`);
    `hitl-pause`'s output is a real pause-shaped dict, but this walk still
    does NOT implement actual pause/resume semantics — it dispatches
    `hitl-pause` and immediately continues to the next edge like any other
    node, so "pausing" a run end-to-end remains unwired here, out of this
    phase's scope. Dispatch order is derived from `recipe.dag.edges` (see
    `_find_start_node_id`/`_build_next_map`), never a fixed `NodeType`
    sequence, and accumulates `state[node.id] = output`. Stops right after
    the `end` node executes, even if the recipe carries more nodes past it —
    those are simply never reached by the walk.

    `llm-step`'s `retrieved_chunks` is threaded from the `kb-retrieve` this
    WALK last passed through (`last_kb_output`), not from a by-type lookup
    into `dag.nodes`: with an edge-derived walk, node declaration order no
    longer implies execution order, so a `{node.type: node}` dict
    (last-declared-wins) can supply a sibling branch's chunks or a node that
    has not executed yet. A walk with no upstream `kb-retrieve` threads `[]`,
    which `LlmStepExecutor` documents as a valid input.

    Day 5: a real `TraceEvent` is built and `await trace_writer.write(event)`
    called for EVERY dispatched node (no node is skipped) — `events` on the
    returned `RunResult` carries all of them, same order as dispatch. A
    single build point handles all 4 node types uniformly (no per-executor
    trace logic): `outputs` wraps `kb-retrieve`'s raw `list[KbSearchResultItem]`
    as `{"chunks": [...]}` (the 3 other node types already return
    `dict[str, object]`, used as-is); `tokens`/`citations` are lifted from the
    executor's own output dict when present (currently only `llm-step`
    carries them), else `Tokens(0, 0)`/`None`. `cost` is a fixed `0.0` this
    phase — no real cost model exists yet (`obs.costs` is a schema-shell,
    DE's later work).

    Dispatch order is still derived from `recipe.dag.edges` exactly as
    before (Day 6 behavior unchanged) — only the validation stance changed.
    `run()` does not call `graph_lint` (workbench's, spec SWE) and does not
    re-validate the structure itself either (Day 12, DEC-A): it trusts
    `recipe.dag` arrived already-linted. See `docs/decisions/decision-log.md`
    (entry DL-12.A1-1) for why and the accepted risk — not repeated here.
    """
    executors: dict[NodeType, NodeExecutor] = {
        NodeType.KB_RETRIEVE: KbRetrieveExecutor(kb_search),
        NodeType.LLM_STEP: LlmStepExecutor(llm, embedding),
        NodeType.TOOL_CALL: ToolCallExecutor(
            WhitelistGuardedDispatch(
                tool_dispatch
                if tool_dispatch is not None
                else WhitelistToolDispatch(recipe.agent_config.tool_whitelist),
                recipe.agent_config.tool_whitelist,
            )
        ),
        NodeType.CONDITION: ConditionExecutor(),
        NodeType.HITL_PAUSE: HitlPauseExecutor(),
        NodeType.END: EndExecutor(),
    }
    nodes_by_id = {node.id: node for node in recipe.dag.nodes}
    next_by_id = _build_next_map(recipe.dag.edges)
    edge_by_id = _build_edge_map(recipe.dag.edges)

    run_id = str(uuid.uuid4())
    state: dict[str, object] = {}
    events: list[TraceEvent] = []
    last_ts: datetime | None = None
    # `llm-step`'s `retrieved_chunks` comes from the `kb-retrieve` the WALK
    # actually passed through — NOT from a by-type lookup into `dag.nodes`.
    # Day 3/4 could use `{node.type: node}` because the walk order was fixed
    # and `dag.nodes` was guaranteed to hold exactly one node per type; an
    # edge-derived walk breaks both assumptions. A by-type dict is
    # last-declared-wins, so with 2 `kb-retrieve` nodes it can hand `llm-step`
    # a sibling's chunks (silently wrong grounding → wrong `citations`), or a
    # node the walk has not executed yet (`KeyError` on `state[...]`). `[]`
    # when no upstream `kb-retrieve` was visited is a valid input per
    # `LlmStepExecutor` ("no retrieved chunk → no citation"), not an error.
    last_kb_output: object = []
    # The question `llm-step` must answer lives on the `kb-retrieve` node
    # (`params["query"]`); `llm-step` has none of its own. Threaded from the
    # same walk-passed node as `last_kb_output`, for the same reason: node
    # declaration order no longer implies execution order, so a by-type lookup
    # could supply a sibling branch's query.
    last_kb_query: object = ""
    # `condition`'s `state` param (see the injection branch below) is the
    # WALK's last output, whatever node type produced it — not filtered to
    # `kb-retrieve` the way `last_kb_output` above is. `_NO_UPSTREAM` (F8)
    # distinguishes "no node has run yet" from "the last node ran and
    # returned an empty dict", which a bare `{}` initial value could not.
    last_output: object = _NO_UPSTREAM
    current_id: str = _find_start_node_id(recipe.dag)
    while True:
        node = nodes_by_id[current_id]
        node_type = node.type
        if node_type is NodeType.KB_RETRIEVE:
            # Thread the SESSION's tenant identity AND scope (Day 8 INV-1 /
            # Day 17 T6 label-spoof) down into the `kb-retrieve` executor —
            # never the recipe's own client-declared tenant/section_roles
            # fields. engine#33 phase 2: this override used to be inline here
            # (~45 lines); now shared with `agent_loop.run_agent_loop()`
            # (phase 3) via `studio_engine.fence.fenced_kb_params` — see that
            # function's docstring for the full rationale (probed
            # `list("public")`/`list(None)` evidence, QĐ-5, QĐ-7, D8 INV-1)
            # this refactor did not drop, only relocate.
            node = node.model_copy(update={"params": fenced_kb_params(node.params, session_context)})
        if node_type is NodeType.LLM_STEP:
            node = node.model_copy(
                update={
                    "params": {
                        **node.params,
                        "retrieved_chunks": last_kb_output,
                        "query": last_kb_query,
                        # Day 7: `agent_config.instructions`/`.model` threaded the
                        # same way as `retrieved_chunks`/`query` above —
                        # `LlmStepExecutor` reads both from `node.params`, so
                        # swapping `StubEmbedding`→`GatewayEmbedding` (or
                        # `FixtureLLM`→a real gateway LLM) never touches this file.
                        "instructions": recipe.agent_config.instructions,
                        "model": recipe.agent_config.model,
                    }
                }
            )
        if node_type is NodeType.CONDITION:
            # `state`: the walk ALWAYS determines the truth about whether
            # upstream state exists — a client declaration never overrides
            # that (same "server/walk-derived value always wins" stance as
            # `kb-retrieve`'s `tenant_id` above). Two branches, together
            # unconditional: when a node ran before this one, its output
            # overwrites whatever the client declared; when NONE did
            # (`condition` is the DAG's start node, F8's `_NO_UPSTREAM`
            # case), any client-declared `state` is explicitly REMOVED
            # rather than left to survive untouched — a start-node
            # `condition` must always evaluate as if `state` were absent
            # (`reason="no-upstream-output"`), never against attacker/
            # recipe-supplied data masquerading as real upstream output
            # (D14 #96 review I-1: a client-declared `params={"state": {...}}`
            # on a start node must not leak through as if it were `ok`).
            condition_params: dict[str, object] = dict(node.params)
            if last_output is not _NO_UPSTREAM:
                condition_params["state"] = last_output
            else:
                condition_params.pop("state", None)
            # `when`: from the OUTGOING edge the walk is about to take out
            # of this node (`_build_edge_map`, F4) — but only when the node
            # hasn't already declared its own `when`, same "recipe declares
            # it, recipe wins" precedence already used for `prompt`/`model`
            # above (`executors.py:228-247`).
            outgoing_edge = edge_by_id.get(current_id)
            if "when" not in node.params and outgoing_edge is not None and outgoing_edge.when is not None:
                condition_params["when"] = outgoing_edge.when
            node = node.model_copy(update={"params": condition_params})
        output = await executors[node_type].execute(node)
        state[node.id] = output
        last_output = output
        if node_type is NodeType.KB_RETRIEVE:
            last_kb_output = output
            last_kb_query = node.params.get("query", "")

        if isinstance(output, list):
            chunks = [item.model_dump(mode="json") for item in output if isinstance(item, KbSearchResultItem)]
            outputs: dict[str, object] = {"chunks": chunks}
            if node_type is NodeType.KB_RETRIEVE and not chunks:
                # Audit (#111): `[]` từ `kb.search` nghĩa CHÍNH XÁC là "không có gì trong
                # phạm vi" — hàng rào đã lọc sạch (`kb-search.v0.md` §6.1/§6.1a). Ca "trong
                # phạm vi nhưng không có đáp án" trả về list KHÁC rỗng, nên không bao giờ
                # rơi vào nhánh này. Không có cờ này, người đọc trace thấy `chunks: []` mà
                # không biết là bị chặn hay là không tìm được.
                #
                # Đọc đúng chữ: cờ này nói "KHÔNG CÓ CHUNK HỢP LỆ NÀO trong event", không
                # nói "hàng rào vừa chặn một thứ cụ thể". `chunks` cũng rỗng khi `kb.search`
                # trả về phần tử sai kiểu (bị `isinstance(item, KbSearchResultItem)` lọc
                # hết) — một lỗi serialize sẽ hiện ra ở đây y hệt một ca fence. Đừng đọc
                # `fenced: true` như một sự kiện bảo mật đơn lẻ; nó là "0 chunk hợp lệ".
                outputs["fenced"] = True
            tokens = Tokens(prompt=0, completion=0)
            citations = None
        else:
            raw_outputs = dict(output) if isinstance(output, dict) else {}
            raw_tokens = raw_outputs.get("tokens")
            tokens = raw_tokens if isinstance(raw_tokens, Tokens) else Tokens(prompt=0, completion=0)
            # C-1 (`docs/contracts/trace-citations.v0.md`): CHỈ `llm-step` được mang
            # `citations`. Cổng theo `node_type`, không theo hình dạng output — trước
            # D11 chỗ này nhấc `citations` từ output của **bất kỳ** node trả dict, nên
            # "chỉ llm-step" là hành vi tình cờ đúng chứ không phải bảo đảm: riêng
            # `ToolCallExecutor` trả thẳng dict của `ToolDispatch.dispatch()` — một seam
            # NGOÀI — nên một tool đặt key "citations" là đi thẳng vào trace như trích
            # dẫn thật, và `citation_accuracy` (evalhub) ăn điểm giả.
            # Không raise, không mất dữ liệu: key vẫn nằm nguyên trong `outputs` bên
            # dưới nên vẫn truy được, chỉ không được nhận là trích dẫn có căn cứ.
            raw_citations = raw_outputs.get("citations") if node_type is NodeType.LLM_STEP else None
            citations = raw_citations if isinstance(raw_citations, list) else None
            # JSON-safe outputs (F15's PgTraceWriter serializes via Jsonb):
            # a raw Tokens pydantic object can't go through json.dumps as-is.
            outputs = {
                key: (value.model_dump(mode="json") if isinstance(value, Tokens) else value)
                for key, value in raw_outputs.items()
            }

        now = datetime.now(UTC)
        if last_ts is not None and now <= last_ts:
            now = last_ts + timedelta(microseconds=1)
        last_ts = now

        event = TraceEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            agent_id=recipe.agent_id,
            tenant_id=session_context.tenant_id,
            node_id=node.id,
            node_type=node_type,
            ts=now.isoformat(timespec="microseconds"),
            inputs_hash=hashlib.sha256(json.dumps(node.params, sort_keys=True, default=str).encode()).hexdigest(),
            outputs=outputs,
            tokens=tokens,
            cost=_NO_COST,
            citations=citations,
        )
        await trace_writer.write(event)
        events.append(event)

        if node_type is NodeType.END:
            break
        next_id = next_by_id.get(current_id)
        if next_id is None:
            # Day 12 (DEC-A): the chain ran out of edges before an `end` node
            # executed. Previously this raised loudly — now it just `break`s
            # and returns whatever `RunResult` was accumulated so far. This
            # IS the exact danger the old comment here warned about: "a
            # truncated run would be indistinguishable from a complete one to
            # every caller" (`RunResult` carries no "terminated" flag, and
            # evalhub scores it the same either way). Accepted on purpose per
            # DEC-A (see docs/decisions/decision-log.md, DL-12.A1-1) — not an
            # oversight.
            break
        current_id = next_id

    return RunResult(run_id=run_id, events=events, final_state=state)
