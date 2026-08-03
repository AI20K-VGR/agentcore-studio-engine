"""Interpreter loop (spec AIE-1, R-SPEC A2) — walks `recipe.dag` by
following `dag.edges` from a single start node (the one node id no edge's
`.to` targets) until an `end` node executes, dispatching each visited node
to its constructor-DI'd executor and accumulating outputs into a plain
`dict` (`RunState`, an insertion-order accumulator — a plain `dict` already
preserves insertion order, no `OrderedDict` needed).

Day 6 (spec AIE-1, plan risk R2 lifted): walk order is now DERIVED from
`recipe.dag.edges` — Day 3's hardcoded `_WALK_ORDER` `NodeType` tuple is
gone. `condition` branching (`Edge.when`) is still unevaluated
(`ConditionExecutor` stays `NotImplementedError`), so this phase only
supports a finite single-successor chain and refuses anything else with a
`ValueError` rather than guessing. The four structural preconditions:

1. exactly 1 start node (no incoming edge) — `_find_start_node_id`
2. every node has ≤ 1 outgoing edge — `_build_next_map`
3. no node is visited twice (no reachable cycle) — the walk's `visited` set
4. the chain terminates ON an `end` node, not merely by running out of edges

None of the four may be delegated upstream, for a reason that does not
depend on how far along `graph_lint` (spec SWE) happens to be: `run()`
never CALLS it. Nothing in this module can observe whether a recipe was
linted, so treating any of these as somebody else's job would mean trusting
a check this code cannot see. Rule (4) additionally has no upstream twin at
all — graph_lint's 4 rules say nothing about an `end` node being reachable
or terminal. Without (3) a graph like `a -> b -> b` passes (1) and (2) and
would spin forever, growing `events` and calling `trace_writer.write`
without bound.

(Do NOT re-state graph_lint's implementation status here. An earlier cut of
this docstring claimed its "4 specified rules cover neither a lasso cycle
nor an `end` node" — the lasso half was already false when written: rule 2
"no forbidden cycle" has been in the spec since the stub, and workbench#12
implements it as a 3-color DFS that does reject `a -> b -> b`. The `visited`
set below is defense-in-depth, which is a claim about THIS module and stays
true either way.)
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
)
from studio_engine.session import SessionContext

# Day 5 (out of scope for cost-lineage — obs.costs stays a schema-shell until
# DE builds real cost aggregation): every TraceEvent this phase emits carries
# this fixed cost, never a computed one.
_NO_COST = 0.0


def _find_start_node_id(dag: Dag) -> str:
    """The DAG's sole entry point: the one node id no edge's `.to` targets.
    0 or >1 candidates means the recipe doesn't describe a single walkable
    chain — fail loud instead of guessing which one to start from."""
    targets = {edge.to for edge in dag.edges}
    starts = [node.id for node in dag.nodes if node.id not in targets]
    if len(starts) != 1:
        raise ValueError(f"recipe.dag must have exactly 1 start node (no incoming edge), found {len(starts)}: {starts}")
    return starts[0]


def _build_next_map(edges: list[Edge]) -> dict[str, str]:
    """`from_ -> to` single-successor lookup. >1 outgoing edge from the same
    node is `condition`-node branching (`Edge.when`) — unevaluated this
    phase (`ConditionExecutor` is still `NotImplementedError`), so it raises
    rather than silently picking one arm."""
    next_by_id: dict[str, str] = {}
    for edge in edges:
        if edge.from_ in next_by_id:
            raise ValueError(
                f"node {edge.from_!r} has >1 outgoing edge — condition branching is not "
                "evaluated this phase (ConditionExecutor is unimplemented)"
            )
        next_by_id[edge.from_] = edge.to
    return next_by_id


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
) -> RunResult:
    """Walk `recipe.dag` from its single start node, following `dag.edges`
    node-by-node, until an `end` node executes.

    `session_context` (Day 8, INV-1 Tenant-Wall) is server-resolved caller
    identity — mandatory, keyword-only, NO default, so no call site can
    silently skip the fence. Every tenant identity this function emits (the
    `kb-retrieve` node's injected `tenant_id` param, and every `TraceEvent`'s
    `tenant_id`) comes from `session_context.tenant_id`, NEVER from the
    recipe's own client-declared tenant field — a mismatch (client declares
    one tenant, session resolves to another) is not an error, it is simply
    ignored: the run proceeds scoped to the session's tenant. That recipe
    field is client-supplied data (`studio_contracts.recipe.Recipe`, built by
    workbench/client) and this function does not read it anywhere; see
    `studio_engine.session` for why that is the actual fence, not merely a
    convention.

    Constructs all 6 executors explicitly (constructor-DI, plan decision
    #2 — NOT a generic factory): `KbRetrieveExecutor(kb_search)`,
    `LlmStepExecutor(llm, embedding)`, a `ToolCallExecutor` wired with a
    `WhitelistToolDispatch(recipe.agent_config.tool_whitelist)`,
    `ConditionExecutor()`, `HitlPauseExecutor()`, and `EndExecutor()` — the
    last two are still `NotImplementedError` stubs, wired here only so a
    recipe that happens to route through one fails with that clear error
    instead of a bare `KeyError`. Dispatch order is derived from
    `recipe.dag.edges` (see `_find_start_node_id`/`_build_next_map`), never
    a fixed `NodeType` sequence, and accumulates `state[node.id] = output`.
    Stops right after the `end` node executes, even if the recipe carries
    more nodes past it — those are simply never reached by the walk.

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

    No graph-lint validation seam is wired in here (workbench's `graph_lint`,
    spec SWE) and none is planned: lint runs at PUBLISH time, `run()` at
    execute time, and a recipe can reach an interpreter without having gone
    through this deployment's publish path at all. So `run()` defends itself
    with the 4 structural preconditions listed in the module docstring, each
    raising `ValueError`.

    Those preconditions are STRICTER than graph_lint's 4 rules — precondition
    (2) (≤ 1 outgoing edge) and (4) (must terminate ON an `end` node) have no
    upstream twin, because `condition` branching is still unevaluated here.
    A recipe can therefore pass publish-time lint and still be rejected at
    run time. That gap is intentional for now, but it is a REAL divergence
    between two published contracts, and closing it is cross-lane work
    (AIE-1 + SWE), not something this module decides alone.
    """
    executors: dict[NodeType, NodeExecutor] = {
        NodeType.KB_RETRIEVE: KbRetrieveExecutor(kb_search),
        NodeType.LLM_STEP: LlmStepExecutor(llm, embedding),
        NodeType.TOOL_CALL: ToolCallExecutor(WhitelistToolDispatch(recipe.agent_config.tool_whitelist)),
        NodeType.CONDITION: ConditionExecutor(),
        NodeType.HITL_PAUSE: HitlPauseExecutor(),
        NodeType.END: EndExecutor(),
    }
    nodes_by_id = {node.id: node for node in recipe.dag.nodes}
    next_by_id = _build_next_map(recipe.dag.edges)

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
    # Cycle guard: `_find_start_node_id` only rejects a graph whose every node
    # has an incoming edge, and `_build_next_map` only rejects out-degree > 1.
    # A "lasso" (`a -> b -> b`, or `a -> b -> c -> b`) passes both and would
    # spin forever here — unbounded `events` growth AND unbounded
    # `trace_writer.write` calls. graph_lint's no-cycle rule (spec SWE) also
    # rejects lassos, but this guard is not redundant: `run()` never calls
    # graph_lint, so an unlinted recipe reaching here would hit nothing else.
    visited: set[str] = set()
    current_id: str = _find_start_node_id(recipe.dag)
    while True:
        if current_id in visited:
            raise ValueError(
                f"recipe.dag has a cycle — node {current_id!r} revisited; this phase walks "
                "a finite single-successor chain and has no loop semantics"
            )
        visited.add(current_id)
        node = nodes_by_id[current_id]
        node_type = node.type
        if node_type is NodeType.KB_RETRIEVE:
            # Thread the SESSION's tenant identity (Day 8, INV-1) down into
            # the `kb-retrieve` executor (same inject-into-params pattern used
            # for `retrieved_chunks` below) — never the recipe's own tenant
            # field, which is client-declared and exactly the value INV-1
            # must not trust.
            # `session_context.tenant_id` is set AFTER the `**node.params`
            # spread on purpose: whatever tenant identity a client-authored
            # node already carries in its own params (the workbench recipe
            # only ever puts a tenant SLUG there, never a UUID, but nothing
            # stops a hand-crafted recipe from putting a UUID) is always
            # overwritten by the session's, never merely supplemented — a
            # missing/malformed value post-override still fails closed at
            # `KbRetrieveExecutor` (`isinstance(..., UUID)`-check, raises
            # `PermissionError`, see `executors.py`), it does not fall back
            # to a sentinel (that fallback was removed, Day 8 phase 2).
            node = node.model_copy(update={"params": {**node.params, "tenant_id": session_context.tenant_id}})
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
        output = await executors[node_type].execute(node)
        state[node.id] = output
        if node_type is NodeType.KB_RETRIEVE:
            last_kb_output = output
            last_kb_query = node.params.get("query", "")

        if isinstance(output, list):
            outputs: dict[str, object] = {
                "chunks": [item.model_dump(mode="json") for item in output if isinstance(item, KbSearchResultItem)]
            }
            tokens = Tokens(prompt=0, completion=0)
            citations = None
        else:
            raw_outputs = dict(output) if isinstance(output, dict) else {}
            raw_tokens = raw_outputs.get("tokens")
            tokens = raw_tokens if isinstance(raw_tokens, Tokens) else Tokens(prompt=0, completion=0)
            raw_citations = raw_outputs.get("citations")
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
            # The chain ran out of edges before an `end` node executed. Day 3's
            # `nodes_by_type[NodeType.END]` lookup made a missing terminal a
            # loud `KeyError`; an edge-derived walk would otherwise just fall
            # out of the loop and return a normal-looking `RunResult`, so a
            # truncated run would be indistinguishable from a complete one to
            # every caller (`RunResult` carries no "terminated" flag, and
            # evalhub scores it the same either way). Keep the failure loud.
            raise ValueError(
                f"recipe.dag walk ended at node {current_id!r} (no outgoing edge) without "
                f"executing an `end` node — visited {len(visited)} node(s): {sorted(visited)}"
            )
        current_id = next_id

    return RunResult(run_id=run_id, events=events, final_state=state)
