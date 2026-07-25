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
from datetime import datetime, timezone

from studio_contracts import (
    LLM,
    EmbeddingService,
    KbSearch,
    Node,
    NodeType,
    Recipe,
    Tokens,
    TraceEvent,
    TraceWriter,
)

from studio_engine.demo_stubs import WhitelistToolDispatch
from studio_engine.executors import EndExecutor, KbRetrieveExecutor, LlmStepExecutor, NodeExecutor, ToolCallExecutor

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


def _build_trace_event(
    run_id: str,
    recipe: Recipe,
    node: Node,
    output: object,
) -> TraceEvent:
    """Construct a TraceEvent instance for an executed node."""
    if isinstance(output, dict):
        outputs_dict = dict(output)
    elif isinstance(output, list):
        outputs_dict = {"items": output}
    else:
        outputs_dict = {"result": output}

    prompt_tokens = 0
    completion_tokens = 0
    if isinstance(output, dict):
        tokens_val = output.get("tokens")
        if isinstance(tokens_val, dict):
            prompt_tokens = int(tokens_val.get("prompt", 0))
            completion_tokens = int(tokens_val.get("completion", 0))

    cost = 0.0
    if isinstance(output, dict) and "cost" in output:
        try:
            cost = float(output["cost"])
        except (ValueError, TypeError):
            cost = 0.0

    citations: list[str] | None = None
    if node.type is NodeType.KB_RETRIEVE:
        if isinstance(output, list):
            citations = []
            for chunk in output:
                if isinstance(chunk, dict) and "id" in chunk:
                    citations.append(str(chunk["id"]))
                elif isinstance(chunk, str):
                    citations.append(chunk)

    params_str = json.dumps(node.params, default=str, sort_keys=True)
    inputs_hash = hashlib.sha256(params_str.encode("utf-8")).hexdigest()[:16]

    return TraceEvent(
        event_id=str(uuid.uuid4()),
        run_id=run_id,
        agent_id=recipe.agent_id,
        tenant_id=recipe.tenant_id,
        node_id=node.id,
        node_type=node.type,
        ts=datetime.now(timezone.utc).isoformat(),
        inputs_hash=inputs_hash,
        outputs=outputs_dict,
        tokens=Tokens(prompt=prompt_tokens, completion=completion_tokens),
        cost=cost,
        citations=citations,
    )


async def run(
    recipe: Recipe,
    *,
    kb_search: KbSearch,
    llm: LLM,
    embedding: EmbeddingService,
    trace_writer: TraceWriter | None = None,
) -> RunResult:
    """Walk the hardcoded Day-3 sequence `kb-retrieve -> llm-step ->
    tool-call -> end`.

    Constructs the 4 executors explicitly (constructor-DI, plan decision
    #2 — NOT a generic factory): `KbRetrieveExecutor(kb_search)`,
    `LlmStepExecutor(llm, embedding)`, a `ToolCallExecutor` wired with a
    `WhitelistToolDispatch(recipe.agent_config.tool_whitelist)`, and
    `EndExecutor()`. For each node type in the fixed order above, looks up
    the matching node in `recipe.dag.nodes` (by `.type`, never via
    `.edges`), executes it, accumulates `state[node.id] = output`, and
    emits a `TraceEvent` via `trace_writer`.
    """
    executors: dict[NodeType, NodeExecutor] = {
        NodeType.KB_RETRIEVE: KbRetrieveExecutor(kb_search),
        NodeType.LLM_STEP: LlmStepExecutor(llm, embedding),
        NodeType.TOOL_CALL: ToolCallExecutor(WhitelistToolDispatch(recipe.agent_config.tool_whitelist)),
        NodeType.END: EndExecutor(),
    }
    nodes_by_type = {node.type: node for node in recipe.dag.nodes}

    run_id = str(uuid.uuid4())
    events: list[TraceEvent] = []
    state: dict[str, object] = {}

    for node_type in _WALK_ORDER:
        node = nodes_by_type[node_type]
        if node_type is NodeType.LLM_STEP:
            kb_node_id = nodes_by_type[NodeType.KB_RETRIEVE].id
            node = node.model_copy(update={"params": {**node.params, "retrieved_chunks": state[kb_node_id]}})

        output = await executors[node_type].execute(node)
        state[node.id] = output

        event = _build_trace_event(run_id, recipe, node, output)
        if trace_writer is not None:
            await trace_writer.write(event)
        events.append(event)

        if node_type is NodeType.END:
            break

    return RunResult(run_id=run_id, events=events, final_state=state)
