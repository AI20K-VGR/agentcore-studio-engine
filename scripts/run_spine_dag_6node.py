"""D20 (kit#126, con của kit#129 GATE-2 — *"spine 4 mảng chạy THẬT end-to-end lần đầu"*).
Việc AIE-1 trong GATE-2: 1 DAG THẬT đi qua đủ 6 `NodeType` (`kb-retrieve -> llm-step ->
condition -> tool-call -> hitl-pause -> end`) qua `interpreter.run()` thật (không mock
executor nào), chuỗi thẳng — không rẽ nhánh thật.

Ranh giới đã biết, KHÔNG phải bug của script này (xem `interpreter.py`'s module docstring
+ `ConditionExecutor`/`HitlPauseExecutor` trong `executors.py`):
- `condition` CHỈ evaluate `when`/`state` thật — kết quả của nó KHÔNG route walk
  (`interpreter.py::_build_next_map` là single-successor last-write-wins, DEC-A).
- `hitl-pause` trả output pause-SHAPED (`{"paused": True, "status": "pending_approval"}`)
  nhưng KHÔNG thật sự dừng gì — walk đi tiếp ngay tới `end` như mọi node khác.

Vì vậy 1 walk chạy hết 6/6 node chỉ chứng minh: cả 6 loại executor chạy được, output đúng
shape, không crash walk — ĐÚNG điều issue #126 đòi ("6 node-type executor chạy DAG thật"),
KHÔNG đòi routing/pause thật (đó là INV-2/phase interpreter khác, ngoài scope D20). Đây là
bằng chứng phía engine SẴN SÀNG ghép vào spine — verify chéo với canvas/UI thật của SWE
KHÔNG nằm trong script/PR này.

`tool-call` node: `interpreter.run()` TỰ dựng `ToolCallExecutor(WhitelistToolDispatch(
recipe.agent_config.tool_whitelist))` (`interpreter.py:240`) — script này KHÔNG dựng
dispatcher riêng, chỉ cần đặt tên tool node dùng (`_TOOL_NAME`) vào
`agent_config.tool_whitelist` để `WhitelistToolDispatch` không raise.

Collaborator doubles dùng lại từ `studio_engine.demo_stubs` (Day-3 demo scaffolding, đã
ghi rõ trong docstring module đó là KHÔNG phải production impl):
`EmptyKbSearch` (`kb-retrieve` -> `[]`, không cần fixture retrieval thật), `FixtureLLM`
(VCR-replay `tests/fixtures/llm_step/smoke-01.json`, đã có sẵn — không thêm fixture mới),
`EmptyEmbedding` (`llm-step`'s DI-only collaborator, không dùng thật ở Day 3+). Trace
writer là 1 no-op local double (cùng hình `run_golden_batch.py::_NoOpTraceWriter`) — bằng
chứng thật của walk là `RunResult.events`, không cần sink Postgres.

Chạy lại
--------
    uv run python packages/engine/scripts/run_spine_dag_6node.py

Không cần workspace root như `run_golden_batch.py`/`measure_chunk_embed.py` — script này
không import `studio_kb` (không chạm `StaticKbSearch`/`doc_factory`), chỉ
`studio_contracts`/`studio_engine`, cả hai đã cài vào venv uv-workspace nên chạy standalone
được từ bất kỳ cwd nào `uv run` resolve được venv của repo.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from studio_contracts import (
    AgentConfig,
    Dag,
    Edge,
    KbBinding,
    Node,
    NodeType,
    Recipe,
    ScorecardThreshold,
    TraceEvent,
)
from studio_engine import interpreter
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureLLM

# Team-wide canonical UUID for tenant "ankor" — same value as
# `run_golden_batch.py`/`test_kb_retrieve_llm_step_threading.py`/`test_condition_dag_e2e.py`.
_TENANT_ID = UUID("a0000000-0000-0000-0000-000000000001")
_TOOL_NAME = "search_docs"
_FIXTURE_CASE_ID = "smoke-01"


@dataclass(frozen=True, slots=True)
class _SpineSessionContext:
    """Session-context double satisfying `studio_engine.session.SessionContext`
    structurally (Day 8 INV-1 Tenant-Wall) — same shape as
    `run_golden_batch.py::_GoldenBatchSessionContext`; this script has no real
    login session to read, tenant identity is the fixed `_TENANT_ID` above."""

    tenant_id: Any
    user: str
    roles: list[str]


class _NoOpTraceWriter:
    """`TraceWriter` no-op double — the real evidence this script produces is
    `RunResult.events` (returned by `interpreter.run()` regardless of the
    writer), not a Postgres sink. Same mold as `run_golden_batch.py::_NoOpTraceWriter`."""

    async def write(self, event: TraceEvent) -> None:
        del event


def build_six_node_recipe() -> Recipe:
    """1 `Recipe` dựng trực tiếp qua `studio_contracts` (Node/Edge/Dag/Recipe/AgentConfig/
    KbBinding/ScorecardThreshold) — KHÔNG gọi `graph_lint` (`studio_workbench`,
    `.importlinter` cấm `studio_engine` chạm `studio_workbench`). Chuỗi thẳng 6 node, đúng
    1 outgoing edge mỗi node (không rẽ nhánh thật, xem docstring module ở trên):
    `n_kb -> n_llm -> n_cond -> n_tool -> n_hitl -> n_end`.

    `n_cond -> n_tool` mang `when="refused == true"` — không route walk (DEC-A), chỉ cho
    `ConditionExecutor` một predicate THẬT để evaluate chống lại `llm-step`'s `refused`
    output thật (chứng minh `condition` đọc dữ liệu walk thật, không phải hardcode)."""
    nodes = [
        Node(
            id="n_kb",
            type=NodeType.KB_RETRIEVE,
            params={
                "query": "Nhân viên ankor được nghỉ phép năm bao nhiêu ngày?",
                "section_roles": ["public"],
                "top_k": 5,
            },
        ),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_cond", type=NodeType.CONDITION, params={}),
        Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
        Node(id="n_hitl", type=NodeType.HITL_PAUSE, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    edges = [
        Edge(from_="n_kb", to="n_llm"),
        Edge(from_="n_llm", to="n_cond"),
        Edge(from_="n_cond", to="n_tool", when="refused == true"),
        Edge(from_="n_tool", to="n_hitl"),
        Edge(from_="n_hitl", to="n_end"),
    ]
    return Recipe(
        agent_id="agent-d20-spine-demo",
        tenant_id=_TENANT_ID,
        agent_config=AgentConfig(instructions="", model="m", tool_whitelist=[_TOOL_NAME]),
        dag=Dag(nodes=nodes, edges=edges),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="d20-spine-demo",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


def build_session_context() -> _SpineSessionContext:
    return _SpineSessionContext(tenant_id=_TENANT_ID, user="d20-spine-demo", roles=["public"])


async def run_spine() -> interpreter.RunResult:
    """Chạy `build_six_node_recipe()` qua `interpreter.run()` THẬT — không mock executor
    nào (mọi executor được `interpreter.run()` tự dựng, `interpreter.py:237-244`); chỉ
    collaborator ở biên (`kb_search`/`llm`/`embedding`/`trace_writer`) là double demo, cùng
    quy ước mọi script D15/D16 khác trong `scripts/`."""
    return await interpreter.run(
        build_six_node_recipe(),
        session_context=build_session_context(),
        kb_search=EmptyKbSearch(),
        llm=FixtureLLM(_FIXTURE_CASE_ID),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )


def main() -> None:
    result = asyncio.run(run_spine())
    node_types = [event.node_type for event in result.events]
    expected = list(NodeType)
    print(f"run_id={result.run_id}")
    for event in result.events:
        print(f"  {event.node_id:8s} {event.node_type.value:12s} outputs={event.outputs!r}")
    ok = len(result.events) == 6 and node_types == expected == [
        NodeType.KB_RETRIEVE,
        NodeType.LLM_STEP,
        NodeType.CONDITION,
        NodeType.TOOL_CALL,
        NodeType.HITL_PAUSE,
        NodeType.END,
    ]
    if not ok:
        raise SystemExit(f"FAIL: kỳ vọng đủ 6 NodeType đúng thứ tự DAG, thấy {[nt.value for nt in node_types]}")
    print(
        f"OK: {len(result.events)}/6 NodeType executor chạy DAG thật qua interpreter.run() "
        "(bằng chứng: RunResult.events thật ở trên) — condition/hitl-pause là evaluate-only/"
        "shape-only, chưa route/pause thật (giới hạn đã biết, không phải bug script này)."
    )


if __name__ == "__main__":
    main()
