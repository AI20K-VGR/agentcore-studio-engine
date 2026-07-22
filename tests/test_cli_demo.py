"""Smoke test for the Day-3 CLI demo (`python -m studio_engine`, phase 3,
plan `260722-0956-day3-interpreter-3node`).

Only asserts on `final_state` DATA (keys/values), never on captured stdout —
`build_demo_recipe()` is factored out of `__main__` specifically so this test
can drive `interpreter.run()` directly without capturing prints (phase spec
"tach logic dung recipe khoi I/O de test duoc khong can capture stdout").
"""

from __future__ import annotations

import json
from pathlib import Path

from studio_contracts import TraceEvent
from studio_engine import interpreter
from studio_engine.__main__ import build_demo_recipe
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "llm_step" / "smoke-01.json"


class _NoOpTraceWriter:
    """Conforming no-op `TraceWriter` — same pattern as
    `test_interpreter_behavior.py::_NoOpTraceWriter` (the seam is
    wired-but-unused this phase)."""

    async def write(self, event: TraceEvent) -> None:
        del event


async def test_demo_recipe_runs_four_nodes() -> None:
    recipe = build_demo_recipe()
    result = await interpreter.run(
        recipe,
        kb_search=EmptyKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    final_state = result.final_state
    assert len(final_state) == 4

    kb_node, llm_node, tool_node, end_node = final_state.keys()
    assert final_state[kb_node] == []

    llm_output = final_state[llm_node]
    assert isinstance(llm_output, dict)
    assert llm_output["answer"] == fixture["response"]

    tool_output = final_state[tool_node]
    assert isinstance(tool_output, dict)
    assert tool_output["status"] == "stub-dispatched"
    assert tool_output["tool"] in recipe.agent_config.tool_whitelist

    assert final_state[end_node] == {"terminated": True}
