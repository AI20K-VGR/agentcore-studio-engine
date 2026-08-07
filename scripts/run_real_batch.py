"""Chạy batch THẬT: DAG 4-node `kb-retrieve -> llm-step -> condition -> end` qua
`interpreter.run()`, tiêm `StaticKbSearch` (DE, `studio_kb.static_search` — 42 doc Callisto
thật) vào seam `KbSearch` Protocol thay cho `FixtureKbSearch`/`EmptyKbSearch` — D14
(plan `260806-0938-d14-aie1-node-executors-grid-prep`) đã chứng minh 6 executor + interpreter
đúng bằng double test-local; hôm nay chỉ đổi dữ liệu, không đổi logic (D-3, AIE-1, D15 #101).

Phase 1 (`plans/260807-1001-d15-aie1-real-batch-trace-citations/phases/
phase-1-real-batch-script.md`): dựng DAG + chạy + in `TraceEvent` thật. Phase 2 thêm
`_assert_schema`/`_assert_citations_c1` bên dưới `main()`.

Vì sao script nằm ở `scripts/` chứ không ở `src/studio_engine/`
---------------------------------------------------------------
Cùng lý do `measure_chunk_embed.py` (D11 #81, D-2 của plan này): nó import `studio_kb`, mà
`.importlinter` cấm package `studio_engine` (`src/studio_engine/`) chạm `studio_kb`; `scripts/`
nằm ngoài package đó nên không bị quét (`uv run lint-imports` xác nhận, không suy đoán).

Chạy lại
--------
    uv run python packages/engine/scripts/run_real_batch.py

Chạy từ **kit root** (`agentcore-studio-kit/`) — `uv run` resolve `studio_kb` qua uv-workspace,
không chạy standalone được trong repo con `agentcore-studio-engine` (R-1).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[3]
_KB_SRC = _ROOT / "packages" / "kb" / "src"

if not (_KB_SRC / "studio_kb" / "static_search.py").exists():
    print(
        "Không tìm thấy `studio_kb` tại "
        f"{_KB_SRC} — script này PHẢI chạy từ kit-root workspace "
        "(`uv run python packages/engine/scripts/run_real_batch.py` ở `agentcore-studio-kit/`), "
        "không chạy được standalone trong repo con `agentcore-studio-engine`.",
        file=sys.stderr,
    )
    raise SystemExit(1)

sys.path.insert(0, str(_KB_SRC))

from studio_contracts import (  # noqa: E402
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
from studio_engine import interpreter  # noqa: E402
from studio_engine.demo_stubs import EmptyEmbedding  # noqa: E402
from studio_kb.static_search import StaticKbSearch  # noqa: E402  (R-2: 1 dòng duy nhất)

# Team-wide canonical UUID for tenant "ankor" — same value across quadrants
# (`studio_engine.__main__:36`, `test_condition_dag_e2e.py:47`,
# `studio_kb.doc_factory.TENANT_IDS["ankor"]`).
ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")

# Query THẬT khớp `packages/kb/golden/smoke-5.yaml` (case SC-01): tenant `ankor`,
# `section_roles=["public"]`, đáp án ở `ankor-leave-001#c1`. Dùng golden case thật thay vì
# tự bịa query — đảm bảo có kết quả thật để chứng minh StaticKbSearch đang chạy, không phải
# đoán mò.
_QUERY = "Nhân viên xin nghỉ phép cần báo trước bao lâu?"

# Cùng hình bracket-citation mà `executors.py::_CITATION_RE` trích — `[chunk_id]` đứng riêng
# một dòng trong prompt do `build_prompt` dựng.
_BRACKET_ID_RE = re.compile(r"\[([\w#-]+)\]")


@dataclass(frozen=True, slots=True)
class _RealBatchSessionContext:
    """Session-context double (Day 8 INV-1 Tenant-Wall) — cùng hình
    `__main__.py::_DemoSessionContext`; script này không có phiên đăng nhập thật nào để đọc."""

    tenant_id: UUID
    user: str
    roles: list[str]


class _NoOpTraceWriter:
    """`TraceWriter` no-op hợp lệ — script tự đọc `RunResult.events` để in, không cần sink
    Postgres thật."""

    async def write(self, event: TraceEvent) -> None:
        del event


class _CitingLLM:
    """`LLM` double trích dẫn lại đúng những `chunk_id` mà `kb-retrieve` THẬT SỰ truy xuất
    được từ `StaticKbSearch` — đọc thẳng từ prompt `LlmStepExecutor.build_prompt` dựng (mỗi
    excerpt mở đầu bằng `[chunk_id]` đứng riêng một dòng).

    Khác `test_condition_dag_e2e.py::_Chunk042AnsweringLLM` (khoá cứng `chunk-042`, khớp
    `FixtureKbSearch` cố định): double này không giả định TRƯỚC chunk_id nào sẽ về — nó bám
    theo bất kỳ thứ gì truy xuất thật trả ra, nên citation sinh ra chứng minh được là có căn
    cứ từ `StaticKbSearch`, không phải fixture."""

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del kwargs
        ids = list(dict.fromkeys(_BRACKET_ID_RE.findall(prompt)))
        if not ids:
            return "Không có đoạn trích nào được truy xuất, không thể trả lời."
        cited = " ".join(f"[{cid}]" for cid in ids)
        return f"Theo tài liệu thật đã truy xuất, câu trả lời có căn cứ tại {cited}."


def _build_recipe() -> Recipe:
    """`kb-retrieve -> llm-step -> condition -> end`, cùng hình D14
    (`test_condition_dag_e2e.py::_kb_llm_cond_end_recipe`). KHÔNG đổi
    `__main__.py::build_demo_recipe` — recipe đó khoá chuỗi khác (`tool-call` thay vì
    `condition`) đúng như `studio_kb.trace_reader.EXPECTED_WALK` đang chốt; script này dựng
    recipe RIÊNG, không tái dùng/đổi recipe demo đó."""
    nodes = [
        Node(
            id="n_kb",
            type=NodeType.KB_RETRIEVE,
            params={"query": _QUERY, "section_roles": ["public"], "top_k": 5},
        ),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_cond", type=NodeType.CONDITION, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    edges = [
        Edge(from_="n_kb", to="n_llm"),
        Edge(from_="n_llm", to="n_cond"),
        Edge(from_="n_cond", to="n_end", when="refused == false"),
    ]
    return Recipe(
        agent_id="agent-d15-real-batch",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(instructions="", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=edges),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def _run() -> list[TraceEvent]:
    result = await interpreter.run(
        _build_recipe(),
        session_context=_RealBatchSessionContext(tenant_id=ANKOR_ID, user="d15-real-batch", roles=[]),
        kb_search=StaticKbSearch(),
        llm=_CitingLLM(),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )
    return result.events


def main() -> None:
    events = asyncio.run(_run())
    print(f"{len(events)} TraceEvent(s) từ batch thật (StaticKbSearch):\n")
    for event in events:
        print(json.dumps(event.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
