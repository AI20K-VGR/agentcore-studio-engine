"""Chạy 30 case golden-set (`packages/kb/golden/callisto-golden-30-v1.yaml`) qua
`interpreter.run()` THẬT bằng `StaticKbSearch` THẬT (không fixture) — D16 AIE-1 phase 1
(plan `260810-1501-d16-aie1-golden-batch-determinism`). Đối chiếu `citations`/`refused`
đọc từ `TraceEvent` thật (event `kb-retrieve`/`llm-step`, KHÔNG phải `AgentAnswer.citations`
— đúng quy ước `agent_runner.py:9-10`, dù seam đó không nằm trong lane này) với nhãn golden.
Đây là bước CHỨNG MINH ĐÚNG (correctness), tiền đề bắt buộc trước Phase 2 (chứng minh LẶP
LẠI đúng qua replay) — không thể đo determinism có ý nghĩa trên một run đã sai nhãn.

Vì sao script nằm ở `scripts/` chứ không ở `src/studio_engine/`
-----------------------------------------------------------------
Cùng lý do `run_real_batch.py` (D15) / `measure_chunk_embed.py` (D11): nó import
`studio_kb.static_search`/`studio_kb.doc_factory`, mà `.importlinter` cấm package
`studio_engine` (`src/studio_engine/`) chạm `studio_kb`; `scripts/` nằm ngoài package đó
nên không bị quét.

Chạy lại
--------
    uv run python packages/engine/scripts/run_golden_batch.py

Chạy từ **kit root** (`agentcore-studio-kit/`) — `uv run` resolve `studio_kb` qua
uv-workspace, không chạy standalone được trong repo con `agentcore-studio-engine`.
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped, unused-ignore]

_ROOT = Path(__file__).resolve().parents[3]
_KB_SRC = _ROOT / "packages" / "kb" / "src"
_GOLDEN_SET_PATH = _ROOT / "packages" / "kb" / "golden" / "callisto-golden-30-v1.yaml"

if not (_KB_SRC / "studio_kb" / "static_search.py").exists():
    print(
        "Không tìm thấy `studio_kb` tại "
        f"{_KB_SRC} — script này PHẢI chạy từ kit-root workspace "
        "(`uv run python packages/engine/scripts/run_golden_batch.py` ở `agentcore-studio-kit/`), "
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
from studio_kb.doc_factory import TENANT_IDS  # noqa: E402  (R-2: 1 dòng duy nhất)
from studio_kb.static_search import StaticKbSearch  # noqa: E402  (R-2: 1 dòng duy nhất)

# Cùng hình bracket-citation mà `executors.py::_CITATION_RE` trích — `[chunk_id]` đứng riêng
# một dòng trong prompt do `build_prompt` dựng (tái dùng idiom `run_real_batch.py:79`).
_BRACKET_ID_RE = re.compile(r"\[([\w#-]+)\]")


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """Một case golden-set — 8 field format (`packages/kb/golden/format.md` §2), cùng hình
    `GridCase` (`packages/kb/src/studio_kb/grid_queries.py:40-62`, chỉ dùng làm THAM CHIẾU
    hình dạng, không import chéo package)."""

    case_id: str
    query: str
    tenant: str
    section_roles: list[str]
    expected_tenant: str
    expected_section_role: str
    expected: str
    expected_citation: list[str]

    @property
    def is_refusal(self) -> bool:
        """Case âm (leak-test) ⇔ không có citation kỳ vọng. Suy từ dữ liệu, không phải cờ
        riêng — cùng quy ước `GridCase.is_refusal`."""
        return not self.expected_citation


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Kết quả THẬT của 1 case sau khi chạy qua `interpreter.run()` — `events` giữ NGUYÊN
    walk thật (không rút gọn) để test Goodhart-closing xác nhận có run thật đứng sau
    `citations`/`refused`, không phải đọc thẳng nhãn YAML rồi gán ngược."""

    case_id: str
    citations: list[str]
    refused: bool
    events: list[TraceEvent]


class _GoldenAwareLLM:
    """`LLM.complete` double biết trước nhãn golden CỦA CASE HIỆN TẠI (`expected_citation`,
    tiêm qua constructor — mỗi case dùng 1 instance riêng). Đọc `[chunk_id]` bracket mà
    prompt thật sự đưa ra (tái dùng regex `_BRACKET_ID_RE` cùng hình `run_real_batch.py:79`),
    trả lời trích ĐÚNG giao `bracket_ids ∩ expected_citation` — giao rỗng (case refusal, hoặc
    case dương nhưng fence đã lọc mất chunk kỳ vọng) thì trả câu không trích gì (không ngoặc
    vuông nào), để `LlmStepExecutor` tự suy `citations=[]`/`refused=True` qua logic thật của
    nó (`executors.py:253-266`) — double này KHÔNG tự gán `refused`.

    ⚠️ Giới hạn đã biết (review D16 W1): cho 8 case refusal, `expected_citation=[]` ⇒
    `self._expected` rỗng ⇒ `cite_ids` LUÔN rỗng, BẤT KỂ `StaticKbSearch` có thật sự trả về
    chunk nào hay không (probe review: cả 8/8 case refusal đều retrieve non-empty). Đường
    refusal vì vậy KHÔNG khả-phủ-chứng bằng double này — nó chứng minh double trung thực
    (không bịa citation), KHÔNG chứng minh fence/leak-filtering của `StaticKbSearch` đúng
    trên các case đó. `30/30 khớp nhãn` là bằng chứng ĐÚNG cho 22 case dương (retrieval thật
    phải tìm đúng chunk); với 8 case refusal nó chỉ xác nhận nhãn golden tự nhất quán với
    double, không phải bằng chứng độc lập cho hành vi fence. Test fence/leak-detection thật
    (nếu cần) ngoài scope AIE-1 hôm nay (`DEC-D16-06`, LLM-judge/match_mode hoãn D18)."""

    def __init__(self, expected_citation: list[str]) -> None:
        self._expected = set(expected_citation)

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del kwargs
        offered_ids = list(dict.fromkeys(_BRACKET_ID_RE.findall(prompt)))
        cite_ids = [cid for cid in offered_ids if cid in self._expected]
        if not cite_ids:
            return "Không có đoạn trích nào khớp câu hỏi, không thể trả lời."
        cited = " ".join(f"[{cid}]" for cid in cite_ids)
        return f"Theo tài liệu đã truy xuất, câu trả lời có căn cứ tại {cited}."


class _NoOpTraceWriter:
    """`TraceWriter` no-op hợp lệ — script tự đọc `RunResult.events` để đối chiếu, không cần
    sink Postgres thật (copy hình `run_real_batch.py:92-97`)."""

    async def write(self, event: TraceEvent) -> None:
        del event


@dataclass(frozen=True, slots=True)
class _GoldenBatchSessionContext:
    """Session-context double (Day 8 INV-1 Tenant-Wall) — cùng hình
    `run_real_batch.py::_RealBatchSessionContext`; script này không có phiên đăng nhập thật
    nào để đọc, tenant_id tới từ `TENANT_IDS[case.tenant]`."""

    tenant_id: Any
    user: str
    roles: list[str]


def load_golden_cases() -> list[GoldenCase]:
    """Parse `_GOLDEN_SET_PATH` bằng `yaml.safe_load` — KHÔNG phụ thuộc
    `studio_kb.golden_set.GoldenCase` (module đó chưa tồn tại, PR #18 kb chưa merge)."""
    raw = yaml.safe_load(_GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    cases_raw = raw["cases"]
    return [
        GoldenCase(
            case_id=entry["case_id"],
            query=entry["query"],
            tenant=entry["tenant"],
            section_roles=list(entry["section_roles"]),
            expected_tenant=entry["expected_tenant"],
            expected_section_role=entry["expected_section_role"],
            expected=entry["expected"],
            expected_citation=list(entry["expected_citation"]),
        )
        for entry in cases_raw
    ]


def _build_recipe(case: GoldenCase) -> Recipe:
    """`kb-retrieve -> llm-step -> end`, cùng hình D15 `run_real_batch.py::_build_recipe`
    nhưng BỎ `condition` (2 edge: kb->llm, llm->end) — không cần định tuyến theo `refused`
    ở đây, chỉ cần walk chạy hết và đọc `TraceEvent` thật ra để đối chiếu nhãn."""
    tenant_id = TENANT_IDS[case.tenant]
    nodes = [
        Node(
            id="n_kb",
            type=NodeType.KB_RETRIEVE,
            params={"query": case.query, "section_roles": case.section_roles, "top_k": 5},
        ),
        Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    edges = [
        Edge(from_="n_kb", to="n_llm"),
        Edge(from_="n_llm", to="n_end"),
    ]
    role = case.section_roles[0] if case.section_roles else "public"
    return Recipe(
        agent_id=f"agent-d16-golden-{case.case_id}",
        tenant_id=tenant_id,
        agent_config=AgentConfig(instructions="", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=edges),
        kb_binding=KbBinding(kb_id="kb-1", scope=f"{case.tenant}/{role}"),
        golden_set_ref="callisto-golden-30-v1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def _run_case(case: GoldenCase) -> CaseOutcome:
    tenant_id = TENANT_IDS[case.tenant]
    result = await interpreter.run(
        _build_recipe(case),
        # Day 17 (plan 260811-1121-d17): `interpreter.run()` now server-resolves
        # `section_roles` from `session_context.roles`, overwriting whatever the
        # node/recipe declared — the session must mirror the case's OWN label
        # (`roles=list(case.section_roles)`, NOT `{"public", *case.section_roles}`;
        # the golden set declares single-role cases and adding `"public"` would
        # widen 20+ cases' scope and desync `expected_citation`, plan Risk R2).
        session_context=_GoldenBatchSessionContext(
            tenant_id=tenant_id, user=f"d16-golden-{case.case_id}", roles=list(case.section_roles)
        ),
        kb_search=StaticKbSearch(),
        llm=_GoldenAwareLLM(case.expected_citation),
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )
    llm_events = [event for event in result.events if event.node_type is NodeType.LLM_STEP]
    citations: list[str] = llm_events[-1].citations if llm_events and llm_events[-1].citations else []
    refused = bool(llm_events[-1].outputs.get("refused", not citations)) if llm_events else True
    return CaseOutcome(case_id=case.case_id, citations=citations, refused=refused, events=result.events)


def run_one_case(case: GoldenCase) -> CaseOutcome:
    return asyncio.run(_run_case(case))


def run_all_cases() -> list[CaseOutcome]:
    """Lặp `load_golden_cases()` qua `run_one_case` — tuần tự, đủ dùng cho script không nhạy
    hiệu năng (30 case)."""
    return [run_one_case(case) for case in load_golden_cases()]


def main() -> None:
    cases = {c.case_id: c for c in load_golden_cases()}
    outcomes = run_all_cases()
    # Khoá TẬP trước khi soi từng case (review D16 W2, cùng lý do I-1 của D15
    # `run_real_batch.py:180`): một `outcomes` bị cắt/thiếu vẫn khiến vòng lặp bên dưới chạy
    # "sạch" trên tập rỗng-tương-đối rồi in `N/N` (N nhỏ hơn 30) như thể mọi thứ đều khớp.
    outcome_ids = {o.case_id for o in outcomes}
    if outcome_ids != set(cases):
        missing = sorted(set(cases) - outcome_ids)
        extra = sorted(outcome_ids - set(cases))
        print(
            f"FAIL: outcomes ({len(outcomes)}) không khớp tập case_id kỳ vọng "
            f"({len(cases)}) — missing={missing!r}, extra={extra!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    ok = True
    for outcome in outcomes:
        case = cases[outcome.case_id]
        citations_match = set(outcome.citations) == set(case.expected_citation)
        refused_match = outcome.refused == case.is_refusal
        status = "OK" if citations_match and refused_match else "FAIL"
        if status == "FAIL":
            ok = False
        print(
            f"{status} {outcome.case_id}: citations={sorted(outcome.citations)!r} "
            f"(expected {sorted(case.expected_citation)!r}), refused={outcome.refused} "
            f"(expected {case.is_refusal})"
        )
    print()
    if not ok:
        print("FAIL: có case lệch nhãn golden", file=sys.stderr)
        sys.exit(1)
    print(f"{len(outcomes)}/{len(outcomes)} case khớp nhãn golden")


if __name__ == "__main__":
    main()
