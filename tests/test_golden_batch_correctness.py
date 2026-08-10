"""Khoá tính ĐÚNG (correctness) của golden batch 30 case (D16 AIE-1 phase 1,
plan `260810-1501-d16-aie1-golden-batch-determinism`).

Nạp `run_golden_batch.py` qua `importlib.util.spec_from_file_location` — cùng
idiom `test_embed_harness.py:33-45` (đăng ký `sys.modules["run_golden_batch"]`
TRƯỚC `exec_module`): file dùng `from __future__ import annotations` nên mọi
annotation hoá thành chuỗi, và `@dataclass` trên `GoldenCase`/`CaseOutcome`
cần `sys.modules[cls.__module__]` có thật để suy giải kiểu — thiếu bước đăng
ký này `dataclasses` tự ném `AttributeError` khi module tự tham chiếu tên của
chính nó.

`pytest.importorskip("studio_kb")` bọc ngoài mọi test thật vì
`run_golden_batch.py` làm `import studio_kb` ở mức module — nạp module đó ở
mức module-của-test-file (như `_EMBED_HARNESS_PATH` phía trên) sẽ nổ cứng ở
collection khi `studio_kb` không nằm trên `sys.path` (chạy test không từ
kit-root workspace), thay vì SKIP sạch; cùng lý do lazy-load
`_load_measure_chunk_embed` ở `test_embed_harness.py:48-59`.
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
    # Module key riêng cho file test này (review D16 W3): cả 2 file test nạp cùng
    # `run_golden_batch.py` qua `importlib` độc lập — khoá `sys.modules` PHẢI khác nhau, nếu
    # không file nạp sau sẽ ghi đè entry của file nạp trước (`sys.modules["run_golden_batch"]`),
    # đúng ngược lại thứ 2 file đang tự nhận là tránh (rò trạng thái `sys.modules` giữa 2 file).
    spec = importlib.util.spec_from_file_location("run_golden_batch_correctness_view", _RUN_GOLDEN_BATCH_PATH)
    assert spec is not None and spec.loader is not None, f"không nạp được {_RUN_GOLDEN_BATCH_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_golden_batch_correctness_view"] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_run_golden_batch()
load_golden_cases = _mod.load_golden_cases
run_all_cases = _mod.run_all_cases


def test_load_golden_cases_exact_count_22_positive_8_refusal() -> None:
    """Khoá số: đúng 30 case, đúng 22 dương + 8 refusal — chặn loader đọc lố/thiếu file lặng
    lẽ (assertion #1, phase file)."""
    cases = load_golden_cases()
    assert len(cases) == 30
    positive = [c for c in cases if not c.is_refusal]
    refusal = [c for c in cases if c.is_refusal]
    assert len(positive) == 22
    assert len(refusal) == 8


def test_load_golden_cases_no_duplicate_case_id() -> None:
    """YAML cho phép key trùng ở list (chỉ override) — một loader đọc lố/thiếu case do trùng
    `case_id` sẽ lộ ra ở đây thay vì âm thầm chạy 29 case tưởng là 30 (assertion #6)."""
    cases = load_golden_cases()
    assert len(cases) == len({c.case_id for c in cases})


def _outcomes_by_case() -> dict[str, Any]:
    return {o.case_id: o for o in run_all_cases()}


def test_positive_cases_citations_match_golden_label() -> None:
    """Assertion #2: mỗi case dương, `outcome.citations` (thật, từ walk `interpreter.run()`)
    khớp `set(case.expected_citation)`."""
    cases = {c.case_id: c for c in load_golden_cases()}
    outcomes = _outcomes_by_case()
    for case_id, case in cases.items():
        if case.is_refusal:
            continue
        outcome = outcomes[case_id]
        assert set(outcome.citations) == set(case.expected_citation), (
            f"{case_id}: citations={outcome.citations!r} != expected={case.expected_citation!r}"
        )


def test_positive_cases_not_refused() -> None:
    """Assertion #4 (đối xứng với #3): mỗi case dương, `outcome.refused is False` — chặn một
    double luôn trả `refused=True` bất kể citations, vẫn lọt qua #2 nếu #2 chỉ check tập rỗng
    hay không."""
    cases = {c.case_id: c for c in load_golden_cases()}
    outcomes = _outcomes_by_case()
    for case_id, case in cases.items():
        if case.is_refusal:
            continue
        outcome = outcomes[case_id]
        assert outcome.refused is False, f"{case_id}: refused=True nhưng đây là case dương"


def test_refusal_cases_empty_citations_and_refused_true() -> None:
    """Assertion #3: mỗi case refusal, `outcome.citations == set()` VÀ `outcome.refused is True`."""
    cases = {c.case_id: c for c in load_golden_cases()}
    outcomes = _outcomes_by_case()
    for case_id, case in cases.items():
        if not case.is_refusal:
            continue
        outcome = outcomes[case_id]
        assert set(outcome.citations) == set(), f"{case_id}: citations={outcome.citations!r} phải rỗng"
        assert outcome.refused is True, f"{case_id}: refused=False nhưng đây là case refusal"


def test_every_outcome_walk_is_real_kb_retrieve_llm_step_end() -> None:
    """Goodhart-closing (assertion #5, bắt buộc): mỗi `outcome` mang `events` THẬT từ
    `interpreter.run()`, walk đúng `(kb-retrieve, llm-step, end)` — 3 event, không thiếu
    không trùng — và với case dương, `outputs["chunks"]` của event `kb-retrieve` không rỗng.

    Không có check này, một implementation giả (đọc thẳng `case.expected_citation` từ YAML
    rồi gán thẳng vào `outcome.citations`, KHÔNG hề gọi `interpreter.run()`) vẫn PASS toàn bộ
    assertion #2-4."""
    cases = {c.case_id: c for c in load_golden_cases()}
    outcomes = _outcomes_by_case()
    for case_id, case in cases.items():
        outcome = outcomes[case_id]
        node_types = [event.node_type.value for event in outcome.events]
        assert node_types == ["kb-retrieve", "llm-step", "end"], (
            f"{case_id}: walk={node_types!r}, kỳ vọng đúng ['kb-retrieve', 'llm-step', 'end']"
        )
        if not case.is_refusal:
            kb_event = outcome.events[0]
            chunks = kb_event.outputs.get("chunks", [])
            assert isinstance(chunks, list) and len(chunks) > 0, (
                f"{case_id}: kb-retrieve.outputs['chunks'] rỗng cho case dương — walk không thật"
            )
