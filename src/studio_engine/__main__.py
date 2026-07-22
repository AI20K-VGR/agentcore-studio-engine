"""Day-3 CLI demo entrypoint (`python -m studio_engine`, spec AIE-1, plan
`260722-0956-day3-interpreter-3node` phase 3).

Runs 1 synthetic 4-node recipe (`kb-retrieve -> llm-step -> tool-call -> end`)
through `interpreter.run()` wired to the Day-3 demo-only stub collaborators
(`studio_engine.demo_stubs`) and prints the run's `final_state` as JSON —
the plan's DoD "1 case chạy CLI in state cuối".

`build_demo_recipe()` is factored out from `_demo()`/`main()` on purpose so
`tests/test_cli_demo.py` can drive `interpreter.run()` directly on the same
recipe without capturing stdout (phase spec: "tách logic dựng recipe khỏi
I/O để test được không cần capture stdout").
"""

from __future__ import annotations

import asyncio
import json

from studio_contracts import AgentConfig, Dag, KbBinding, Node, NodeType, Recipe, ScorecardThreshold, TraceEvent

from studio_engine import interpreter
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM

# The tool name the synthetic `tool-call` node's `params={"tool": ...}`
# carries — MUST match `agent_config.tool_whitelist` below, otherwise
# `WhitelistToolDispatch` (constructed by `interpreter.run()` from the
# recipe's own whitelist) legitimately raises `ValueError`.
_TOOL_NAME = "search_docs"


class _NoOpTraceWriter:
    """Conforming no-op `TraceWriter` — the seam is wired-but-unused this
    phase (populating real `TraceEvent`s is Day 5 scope, per
    `interpreter.py`'s own docstring); a no-op satisfies `run()`'s required
    keyword param without pulling in `PgTraceWriter`/`studio_app`."""

    async def write(self, event: TraceEvent) -> None:
        del event


def build_demo_recipe() -> Recipe:
    """Synthetic 4-node `kb-retrieve -> llm-step -> tool-call -> end` recipe
    (same minimal-but-valid shape as
    `test_interpreter_behavior.py::_four_node_recipe` — copied deliberately,
    not reinvented, per the phase's own risk table). `tenant="ankor"` mirrors
    the fixture's tenant (`tests/fixtures/llm_step/smoke-01.json`).
    """
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    return Recipe(
        agent_id="agent-demo",
        tenant="ankor",
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[_TOOL_NAME]),
        dag=Dag(nodes=nodes, edges=[]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def _demo() -> None:
    result = await interpreter.run(
        build_demo_recipe(),
        kb_search=EmptyKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )
    print(json.dumps(result.final_state, default=str, ensure_ascii=False))


def main() -> None:
    asyncio.run(_demo())


if __name__ == "__main__":
    main()
