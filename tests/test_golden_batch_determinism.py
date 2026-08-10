"""Khoá tính LẶP LẠI (determinism) của golden batch 30 case (D16 AIE-1 phase 2,
plan `260810-1501-d16-aie1-golden-batch-determinism`) — generalize đúng 3 khẳng định của
`test_interpreter_determinism.py` (D9, N=1) lên N=30 case thật qua `run_golden_batch.py` (Phase 1):
(a) nội dung 2 lần chạy giống hệt trừ 3 field tươi-theo-thiết-kế (`run_id`/`event_id`/`ts`);
(b) 3 field đó KHÔNG trùng giữa 2 lần chạy (chặn bug cached-run); (c) giá trị cụ thể được pin
(cardinality 22 dương / 8 refusal), không chỉ so `==` suông (một impl thoái hoá trả cùng-sai ở cả 2
lần chạy vẫn lọt qua phép so `==` nếu chỉ có test #1).

Nạp `run_golden_batch.py` qua `importlib.util.spec_from_file_location` — file test ĐỘC LẬP với
`test_golden_batch_correctness.py` (Phase 1), tự làm `spec_from_file_location` riêng, KHÔNG import
chéo — tránh trạng thái `sys.modules["run_golden_batch"]` rò giữa 2 file test (rủi ro đã thấy ở D14
review). Cùng idiom `test_embed_harness.py:33-45` (đăng ký `sys.modules` TRƯỚC `exec_module`, vì
`from __future__ import annotations` + `@dataclass` cần `sys.modules[cls.__module__]` có thật để
suy giải kiểu).

`pytest.importorskip("studio_kb")` bọc ngoài mọi test thật vì `run_golden_batch.py` làm
`import studio_kb` ở mức module — nạp ở mức module-của-test-file sẽ nổ cứng ở collection khi
`studio_kb` không nằm trên `sys.path`, thay vì SKIP sạch.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("studio_kb")

_RUN_GOLDEN_BATCH_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_golden_batch.py"


def _load_run_golden_batch() -> Any:
    # Module key riêng cho file test này (review D16 W3, đối xứng với
    # `test_golden_batch_correctness.py::_load_run_golden_batch`): dùng chung khoá
    # `sys.modules["run_golden_batch"]` giữa 2 file sẽ khiến file nạp sau ghi đè entry của file
    # nạp trước — đúng ngược lại điều docstring module này khẳng định (tránh rò `sys.modules`).
    spec = importlib.util.spec_from_file_location("run_golden_batch_determinism_view", _RUN_GOLDEN_BATCH_PATH)
    assert spec is not None and spec.loader is not None, f"không nạp được {_RUN_GOLDEN_BATCH_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_golden_batch_determinism_view"] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_run_golden_batch()
load_golden_cases = _mod.load_golden_cases
run_all_cases = _mod.run_all_cases

_EXPECTED_CASE_COUNT = 30
_EXPECTED_EVENT_COUNT = _EXPECTED_CASE_COUNT * 3  # kb-retrieve, llm-step, end mỗi case
_EXPECTED_POSITIVE_COUNT = 22
_EXPECTED_REFUSAL_COUNT = 8


def _content_projection(outcomes: list[Any]) -> dict[str, dict[str, object]]:
    """Mọi field CONTENT-bearing của mỗi outcome, khoá theo `case_id` — trừ 3 field tươi-theo-
    thiết-kế (`run_id`/`event_id`/`ts`, giữ nguyên trong `TraceEvent`). Cùng hình
    `test_interpreter_determinism.py::_content_projection` (dòng 130-151), generalize từ 1 walk
    sang 30 case × walk. `citations`/`refused` ở mức `CaseOutcome` cũng được chiếu vì đó là kết
    quả THẬT mà `test_golden_batch_correctness.py` đối chiếu với nhãn golden — determinism phải
    giữ luôn 2 field đó, không chỉ trace thô."""
    return {
        outcome.case_id: {
            "citations": outcome.citations,
            "refused": outcome.refused,
            "events": [
                {
                    "node_id": event.node_id,
                    "node_type": event.node_type,
                    "agent_id": event.agent_id,
                    "tenant_id": event.tenant_id,
                    "inputs_hash": event.inputs_hash,
                    "outputs": event.outputs,
                    "tokens": event.tokens,
                    "cost": event.cost,
                    "citations": event.citations,
                }
                for event in outcome.events
            ],
        }
        for outcome in outcomes
    }


def test_repeat_batch_yields_identical_outcomes() -> None:
    """Assertion #1: gọi `run_all_cases()` HAI LẦN độc lập (mỗi lần tự dựng
    `StaticKbSearch()`/`_GoldenAwareLLM` mới bên trong `run_one_case` — không share instance nào
    giữa 2 lần, cùng nguyên tắc D9 "shared instance có thể giấu state mang giữa các run"). Chiếu
    nội dung (`_content_projection`) của cả 30 case phải giống hệt.

    Không vacuous: khoá tổng số event = 90 (30 case × 3 event/case, walk `kb-retrieve -> llm-step
    -> end` mà Phase 1 đã xác nhận) TRƯỚC khi so `==` — một impl thoái hoá trả `events=[]` cho mọi
    case vẫn "giống hệt" ở phép so sánh dict rỗng nếu không có khoá số này."""
    first_run = run_all_cases()
    second_run = run_all_cases()

    assert len(first_run) == _EXPECTED_CASE_COUNT
    assert len(second_run) == _EXPECTED_CASE_COUNT
    first_event_total = sum(len(outcome.events) for outcome in first_run)
    second_event_total = sum(len(outcome.events) for outcome in second_run)
    assert first_event_total == _EXPECTED_EVENT_COUNT
    assert second_event_total == _EXPECTED_EVENT_COUNT

    first_projection = _content_projection(first_run)
    second_projection = _content_projection(second_run)
    assert set(first_projection.keys()) == set(second_projection.keys())
    assert len(first_projection) == _EXPECTED_CASE_COUNT
    assert first_projection == second_projection


def test_repeat_batch_identity_fields_are_fresh() -> None:
    """Assertion #2: nửa còn lại của khẳng định determinism chính xác — nội dung lặp lại,
    IDENTITY thì không. Hai lần chạy phải là 2 lần chạy THẬT SỰ khác nhau: `run_id` trùng hoặc
    `event_id` tái dùng nghĩa là một run bị cache/replay, sẽ làm phép so `==` ở test #1 đúng vì
    sai lý do.

    - `run_id` (đọc từ `TraceEvent.run_id` — mọi event của 1 case cùng 1 `run_id`, do
      `interpreter.run()` chỉ mint 1 `run_id` mỗi lần gọi, `interpreter.py:244,380`) của CÙNG một
      `case_id` phải khác nhau giữa 2 lần chạy.
    - Toàn bộ `event_id` gộp cả 2 lần (180 event) không được trùng nhau.
    - `ts` trong mỗi lần chạy của mỗi case phải TĂNG DẦN nghiêm ngặt (walk đơn-luồng,
      `interpreter.py:373-376` tự đảm bảo `now > last_ts` bằng cách cộng thêm 1 microsecond khi
      đồng hồ không tăng)."""
    first_run = run_all_cases()
    second_run = run_all_cases()
    first_by_case = {outcome.case_id: outcome for outcome in first_run}
    second_by_case = {outcome.case_id: outcome for outcome in second_run}
    assert set(first_by_case.keys()) == set(second_by_case.keys())

    all_event_ids: list[str] = []
    all_run_ids: list[str] = []
    for case_id in first_by_case:
        first_outcome = first_by_case[case_id]
        second_outcome = second_by_case[case_id]
        assert first_outcome.events and second_outcome.events, f"{case_id}: outcome thiếu event"

        first_run_id = first_outcome.events[0].run_id
        second_run_id = second_outcome.events[0].run_id
        assert first_run_id != second_run_id, f"{case_id}: run_id trùng giữa 2 lần chạy — nghi cached-run"
        # `run_id` phải nhất quán trong CHÍNH nó — cả 3 event của cùng 1 lần chạy 1 case đến từ
        # cùng 1 lần gọi `interpreter.run()`, nên phải cùng `run_id`.
        assert all(event.run_id == first_run_id for event in first_outcome.events)
        assert all(event.run_id == second_run_id for event in second_outcome.events)
        all_run_ids.extend([first_run_id, second_run_id])

        for outcome in (first_outcome, second_outcome):
            timestamps = [event.ts for event in outcome.events]
            assert timestamps == sorted(timestamps), f"{case_id}: ts không tăng dần: {timestamps!r}"
            assert len(set(timestamps)) == len(timestamps), f"{case_id}: ts trùng nhau: {timestamps!r}"
            all_event_ids.extend(event.event_id for event in outcome.events)

    assert len(all_run_ids) == _EXPECTED_CASE_COUNT * 2
    assert len(set(all_run_ids)) == len(all_run_ids), "run_id tái dùng giữa các case/lần chạy"
    assert len(all_event_ids) == _EXPECTED_EVENT_COUNT * 2
    assert len(set(all_event_ids)) == len(all_event_ids), "event_id tái dùng giữa 2 lần chạy"


def test_batch_positive_negative_split_matches_golden() -> None:
    """Assertion #3: pin cứng cardinality — đúng 22 case `citations != []` (khớp
    `is_refusal=False`), đúng 8 case `citations == []` (khớp `is_refusal=True`), Ở CẢ HAI lần
    chạy độc lập. Chặn một implementation thoái hoá (mọi case đều refuse, hoặc mọi case đều
    "trả lời") mà vẫn lọt qua phép so `==` ở test #1 nếu cả 2 lần chạy CÙNG SAI theo cách giống
    nhau — pin số cụ thể (22/8) thay vì chỉ so sánh 2 lần chạy với nhau."""
    cases = {c.case_id: c for c in load_golden_cases()}
    assert len(cases) == _EXPECTED_CASE_COUNT

    for outcomes in (run_all_cases(), run_all_cases()):
        outcomes_by_case = {o.case_id: o for o in outcomes}
        positive_case_ids = [case_id for case_id, case in cases.items() if not case.is_refusal]
        refusal_case_ids = [case_id for case_id, case in cases.items() if case.is_refusal]
        assert len(positive_case_ids) == _EXPECTED_POSITIVE_COUNT
        assert len(refusal_case_ids) == _EXPECTED_REFUSAL_COUNT

        non_empty_citation_count = sum(
            1 for case_id in positive_case_ids if outcomes_by_case[case_id].citations != []
        )
        empty_citation_count = sum(1 for case_id in refusal_case_ids if outcomes_by_case[case_id].citations == [])
        assert non_empty_citation_count == _EXPECTED_POSITIVE_COUNT, (
            f"kỳ vọng {_EXPECTED_POSITIVE_COUNT} case dương có citations, thực tế {non_empty_citation_count}"
        )
        assert empty_citation_count == _EXPECTED_REFUSAL_COUNT, (
            f"kỳ vọng {_EXPECTED_REFUSAL_COUNT} case refusal citations rỗng, thực tế {empty_citation_count}"
        )
