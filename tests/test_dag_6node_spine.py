"""D20 (kit#126, con của kit#129 GATE-2) — khoá 1 DAG THẬT đi qua đủ 6 `NodeType`
qua `interpreter.run()` thật (không mock executor nào) — chuỗi thẳng
`kb-retrieve -> llm-step -> condition -> tool-call -> hitl-pause -> end`, không rẽ nhánh
thật (Overview của phase, DEC-A vẫn giữ nguyên).

Đây CHỈ chứng minh phần thuộc quyền ghi AIE-1: executor của cả 6 loại chạy được trong 1
walk thật, output đúng shape, không crash — KHÔNG chứng minh `condition` route walk hay
`hitl-pause` dừng thật (2 giới hạn đã biết, xem docstring `ConditionExecutor`/
`HitlPauseExecutor` trong `executors.py`, và `interpreter.py`'s own module docstring).

Nạp `scripts/run_spine_dag_6node.py` qua `importlib.util.spec_from_file_location` — cùng
cơ chế `test_embed_harness.py:33-42` dùng để né xung đột basename `conftest.py` giữa
`packages/engine/tests/` và `apps/studio/tests/` (P3-R1 fallback), không thêm `sys.path`
hay `conftest.py` mới.

Teeth per `docs/code-standards.md` §4.1: mọi assertion khoá giá trị cụ thể (số event đúng
6, thứ tự `node_type` chính xác theo edges, output shape đúng chữ) — không có bare presence
check nào.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from studio_contracts import NodeType

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_spine_dag_6node.py"
_spec = importlib.util.spec_from_file_location("run_spine_dag_6node", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None, f"không nạp được {_SCRIPT_PATH}"
_run_spine_dag_6node = importlib.util.module_from_spec(_spec)
sys.modules["run_spine_dag_6node"] = _run_spine_dag_6node
_spec.loader.exec_module(_run_spine_dag_6node)

run_spine = _run_spine_dag_6node.run_spine
build_six_node_recipe = _run_spine_dag_6node.build_six_node_recipe


# --------------------------------------------------------------------------
# T1: đủ 6 node_type khác nhau, đúng thứ tự walk theo `dag.edges`, 1 event/node.
# --------------------------------------------------------------------------


async def test_all_six_node_types_dispatch_in_dag_edge_order() -> None:
    result = await run_spine()

    assert len(result.events) == 6
    assert [e.node_type for e in result.events] == [
        NodeType.KB_RETRIEVE,
        NodeType.LLM_STEP,
        NodeType.CONDITION,
        NodeType.TOOL_CALL,
        NodeType.HITL_PAUSE,
        NodeType.END,
    ]
    assert {e.node_type for e in result.events} == set(NodeType)
    assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_cond", "n_tool", "n_hitl", "n_end"]
    run_ids = {e.run_id for e in result.events}
    assert run_ids == {result.run_id}
    timestamps = [e.ts for e in result.events]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == 6


# --------------------------------------------------------------------------
# T2: tool-call dispatches for real, through the `WhitelistToolDispatch`
# `interpreter.run()` auto-wires (not a script-owned dispatcher stub).
# --------------------------------------------------------------------------


async def test_tool_call_dispatches_through_whitelist() -> None:
    result = await run_spine()

    tool_output = result.final_state["n_tool"]
    assert tool_output == {"tool": "search_docs", "status": "stub-dispatched"}


# --------------------------------------------------------------------------
# T3: hitl-pause returns the real pause-shaped output but the walk does NOT
# actually halt there — accepted scope limit, proven not hidden.
# --------------------------------------------------------------------------


async def test_hitl_pause_returns_pause_shaped_output_but_does_not_halt_walk() -> None:
    result = await run_spine()

    hitl_output = result.final_state["n_hitl"]
    assert hitl_output == {"paused": True, "status": "pending_approval"}
    assert result.final_state["n_end"] == {"terminated": True}


# --------------------------------------------------------------------------
# T4: condition evaluates REAL upstream state (llm-step's `refused`) but its
# own `result` never changes which node the walk visits next (DEC-A).
# --------------------------------------------------------------------------


async def test_condition_evaluates_real_upstream_state_but_does_not_route() -> None:
    result = await run_spine()

    cond_output = result.final_state["n_cond"]
    assert cond_output == {"when": "refused == true", "result": True, "reason": "ok"}
    node_ids = list(result.final_state.keys())
    assert node_ids[node_ids.index("n_cond") + 1] == "n_tool"


# --------------------------------------------------------------------------
# T5: the DAG itself is a single-successor chain — no real branching, per
# Overview ("chuỗi thẳng, không rẽ nhánh thật").
# --------------------------------------------------------------------------


def test_dag_has_no_branching_single_successor_chain() -> None:
    recipe = build_six_node_recipe()

    from_ids = [edge.from_ for edge in recipe.dag.edges]
    assert len(recipe.dag.nodes) == 6
    assert len(from_ids) == 5
    assert len(set(from_ids)) == 5
