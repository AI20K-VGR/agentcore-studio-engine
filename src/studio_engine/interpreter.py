"""Interpreter loop (spec AIE-1, R-SPEC A2) — walks a HARDCODED 4-node
sequence `kb-retrieve -> llm-step -> tool-call -> end`, dispatching each node
to its constructor-DI'd executor and accumulating outputs into a plain
`dict` (`RunState`, an insertion-order accumulator — a plain `dict` already
preserves insertion order, no `OrderedDict` needed).

Day 3 intentionally does NOT read `recipe.dag.edges` to decide dispatch
order — that is Day 6 scope (plan risk R2 explicitly forbids reading edges
to "walk" dynamically this phase). `recipe.dag.nodes` is read only to look
up each node's `id`/`params` by its fixed `NodeType`, never to derive the
walk order itself.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from studio_contracts import (
    LLM,
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
from studio_engine.executors import EndExecutor, KbRetrieveExecutor, LlmStepExecutor, NodeExecutor, ToolCallExecutor

# Day 5 (out of scope for cost-lineage — obs.costs stays a schema-shell until
# DE builds real cost aggregation): every TraceEvent this phase emits carries
# this fixed cost, never a computed one.
_NO_COST = 0.0

# Hardcoded Day-3 walk order (plan decision #3 + risk R2) — NEVER derived
# from `recipe.dag.edges`. Reading edges to make this dynamic is Day 6 scope
# creep, explicitly forbidden by this phase's risk table.
_WALK_ORDER: tuple[NodeType, ...] = (
    NodeType.KB_RETRIEVE,
    NodeType.LLM_STEP,
    NodeType.TOOL_CALL,
    NodeType.END,
)


@dataclass(frozen=True)
class RunResult:
    """`interpreter.run()`'s return shape. This is `studio_engine`'s own
    type, NOT one of the 4 seam contracts frozen at P2 (R-SPEC A1) — free to
    change shape without a mini-RFC.

    `final_state` is the `RunState` accumulator: `node_id -> executor output`
    in dispatch (insertion) order, one entry per node this phase's hardcoded
    4-node walk actually executed.
    """

    run_id: str
    events: list[TraceEvent] = field(default_factory=list)
    final_state: dict[str, object] = field(default_factory=dict)


async def run(
    recipe: Recipe,
    *,
    kb_search: KbSearch,
    llm: LLM,
    embedding: EmbeddingService,
    trace_writer: TraceWriter,
) -> RunResult:
    """Walk the hardcoded Day-3 sequence `kb-retrieve -> llm-step ->
    tool-call -> end`.

    Constructs the 4 executors explicitly (constructor-DI, plan decision
    #2 — NOT a generic factory): `KbRetrieveExecutor(kb_search)`,
    `LlmStepExecutor(llm, embedding)`, a `ToolCallExecutor` wired with a
    `WhitelistToolDispatch(recipe.agent_config.tool_whitelist)`, and
    `EndExecutor()`. For each node type in the fixed order above, looks up
    the matching node in `recipe.dag.nodes` (by `.type`, never via
    `.edges`), executes it, and accumulates `state[node.id] = output`.
    Stops right after the `end` node executes, even if the recipe carries
    more nodes past it — those are simply never looked up by this phase's
    fixed walk.

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

    A recipe missing one of the 4 required node types raises `KeyError` on
    lookup — Day 3 has no graph-lint validation seam wired in yet
    (workbench's `graph_lint`, spec SWE); a recipe that hasn't passed
    validation is not this phase's problem to defend against beyond that.
    """
    executors: dict[NodeType, NodeExecutor] = {
        NodeType.KB_RETRIEVE: KbRetrieveExecutor(kb_search),
        NodeType.LLM_STEP: LlmStepExecutor(llm, embedding),
        NodeType.TOOL_CALL: ToolCallExecutor(WhitelistToolDispatch(recipe.agent_config.tool_whitelist)),
        NodeType.END: EndExecutor(),
    }
    nodes_by_type = {node.type: node for node in recipe.dag.nodes}

    run_id = str(uuid.uuid4())
    state: dict[str, object] = {}
    events: list[TraceEvent] = []
    last_ts: datetime | None = None
    for node_type in _WALK_ORDER:
        node = nodes_by_type[node_type]
        if node_type is NodeType.LLM_STEP:
            kb_node_id = nodes_by_type[NodeType.KB_RETRIEVE].id
            node = node.model_copy(update={"params": {**node.params, "retrieved_chunks": state[kb_node_id]}})
        output = await executors[node_type].execute(node)
        state[node.id] = output

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
            tenant_id=recipe.tenant_id,
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

    return RunResult(run_id=run_id, events=events, final_state=state)
