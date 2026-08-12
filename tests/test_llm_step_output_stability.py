"""D18 (kit#116) — `llm-step` output stability + `llm_source` gateway-flag
(spec AIE-1, plan `260812-1133-d18-d20-aie1-engine-daily-prs` phase 1).

Two concerns pinned here, independent of who calls `LlmStepExecutor`
(§Overview of the phase file — the judge only ever sees the bare `answer`
string via `studio_evalhub`, never this executor directly):

1. `llm_source` classification (`executors.py::LlmStepExecutor.execute`) —
   `"stub"` for a known double (`FixtureLLM`), else `"gateway"` (the safe
   default direction: never under-claim "stub" for an impl this module
   doesn't recognize).
2. `FixtureLLM.complete()` (`demo_stubs.py:131-146`) is pure fixture replay —
   no `random`/`time`/`id()`-order — so it is stable both within one process
   and across two processes with different `PYTHONHASHSEED`. This half is a
   characterization/fence test: it locks an already-true property rather
   than chasing a bug, so it is allowed to be green immediately. That is a
   distinct, uncontroversial testing convention on its own — it does not
   need to borrow authority from `docs/code-standards.md` §4.1 (review D18
   W-3: that section is about the 2 xfail-test types, spec-contract vs
   behavioral-fence, for a seam that is NOT yet filled; this test targets an
   already-filled, already-correct seam, so §4.1 does not actually apply
   here and citing it was a misattribution).

Both stability tests below are wired with real `retrieved_chunks`
(`KbSearchResultItem`, review D18 W-1/W-5) matching each fixture's own
bracket-cited `chunk_id` — with empty `retrieved_chunks` the executor's
`citations` output is always `[]` regardless of what changed, so a stability
assertion on `citations` would be tautological (`[] == []`) and would not
actually exercise `LlmStepExecutor.execute`'s `retrieved_ids = {chunk.chunk_id
for chunk in retrieved_chunks}` set-construction + `_CITATION_RE.findall`
filter — the one line in that method whose *iteration order* could vary
under a different `PYTHONHASHSEED` if a future edit iterated the set instead
of the current `findall`-ordered list comprehension.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from uuid import UUID

from studio_contracts import KbSearchResultItem, Node, NodeType
from studio_engine.demo_stubs import EmptyEmbedding, FixtureLLM
from studio_engine.executors import LlmStepExecutor

ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")


def _chunk(chunk_id: str, text: str) -> KbSearchResultItem:
    return KbSearchResultItem(chunk_id=chunk_id, text=text, score=0.9, tenant_id=ANKOR_ID, section_role="public")


# One entry per `tests/fixtures/llm_step/<case_id>.json` this file replays —
# `chunk_id`/`text` mirror what each fixture's own `request.prompt` excerpt
# actually names, so the executor's real grounding logic (retrieved AND
# bracket-cited) resolves a genuine `citations` list instead of always `[]`.
# `d18-stability-03`'s recorded `response` (post review D18 W-4 fix) no
# longer cites `chunk-ot-01` — the excerpt does not answer the question, so
# the correct `citations` for that case is `[]` and `refused` is `True`; the
# chunk is still supplied here so that "not cited" is an exercised outcome,
# not an artifact of never offering a chunk to cite in the first place.
_SMOKE_01_CHUNKS = [_chunk("chunk-001", "Nhân viên tenant ankor được nghỉ phép năm 12 ngày.")]
_STABILITY_02_CHUNKS = [_chunk("chunk-wfh-01", "Nhân viên tenant ankor được làm việc từ xa tối đa 2 ngày mỗi tuần.")]
_STABILITY_03_CHUNKS = [_chunk("chunk-ot-01", "Chính sách làm thêm giờ tenant ankor cần quản lý phê duyệt.")]
_FIXTURE_CHUNKS: dict[str, list[KbSearchResultItem]] = {
    "smoke-01": _SMOKE_01_CHUNKS,
    "d18-stability-02": _STABILITY_02_CHUNKS,
    "d18-stability-03": _STABILITY_03_CHUNKS,
}
# Expected `citations` per case, given the chunks above and each fixture's
# recorded `response` text — used to assert the stability tests are
# exercising real (non-tautological) grounding, not just checking that two
# runs happen to agree with each other on an always-empty list.
_EXPECTED_CITATIONS: dict[str, list[str]] = {
    "smoke-01": ["chunk-001"],
    "d18-stability-02": ["chunk-wfh-01"],
    "d18-stability-03": [],
}


class _AdHocGatewayLLM:
    """A minimal `LLM`-shaped double defined only in this test — deliberately
    NOT `FixtureLLM` and NOT any other known double from `demo_stubs.py`, so
    it exercises the default-to-"gateway" branch of the classification."""

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return "Câu trả lời từ một impl LLM không xác định."


def _llm_step_node(retrieved_chunks: list[KbSearchResultItem] | None = None) -> Node:
    return Node(
        id="n_llm",
        type=NodeType.LLM_STEP,
        params={"prompt": "x", "kwargs": {}, "retrieved_chunks": list(retrieved_chunks) if retrieved_chunks else []},
    )


async def test_llm_source_is_stub_for_known_double_fixture_llm() -> None:
    """`FixtureLLM` is a known double (`demo_stubs.py`) — output must carry
    `llm_source == "stub"`. RED before the field exists: `KeyError`."""
    node = _llm_step_node()
    result = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)

    assert isinstance(result, dict)
    assert result["llm_source"] == "stub"


async def test_llm_source_defaults_to_gateway_for_unknown_impl() -> None:
    """An `LLM` impl that is NOT a known double must default to `"gateway"`
    — the safe direction (never under-claim "stub" for an impl this module
    doesn't recognize). RED before any classification logic exists: `KeyError`."""
    node = _llm_step_node()
    result = await LlmStepExecutor(_AdHocGatewayLLM(), EmptyEmbedding()).execute(node)

    assert isinstance(result, dict)
    assert result["llm_source"] == "gateway"


async def test_fixture_llm_replay_stable_within_one_process() -> None:
    """Characterization/fence, green immediately — this is a distinct,
    uncontroversial testing convention on its own (see module docstring;
    review D18 W-3 removed the earlier §4.1 misattribution) —
    `FixtureLLM.complete()` only reads a file and returns a string, no
    `random`/`time`/`id()`-order, so replaying the SAME case twice in-process
    yields identical `answer` + `citations`. `retrieved_chunks` (review D18
    W-1) carries `chunk-001` — the id `smoke-01`'s recorded `response`
    actually bracket-cites — so `citations` is asserted against a real,
    non-empty value, not just checked for self-equality on an always-`[]`
    output."""
    node = _llm_step_node(_SMOKE_01_CHUNKS)

    first = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)
    second = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)

    assert isinstance(first, dict) and isinstance(second, dict)
    assert first["answer"] == second["answer"]
    assert first["citations"] == second["citations"] == _EXPECTED_CITATIONS["smoke-01"]


# Chạy trong tiến trình con — in ra `(answer, citations)` tất định cho một
# case cố định, cùng mẫu `test_embedding_service_contract.py::_E3_CHILD`
# (harness subprocess/PYTHONHASHSEED có sẵn, không phát minh mới).
# `chunk_id`/`chunk_text` (review D18 W-1) thread một `KbSearchResultItem`
# thật vào `node.params["retrieved_chunks"]` bên trong tiến trình con — nếu
# không, `citations` luôn `[]` bất kể `PYTHONHASHSEED`, và bài test không đo
# được gì (review D18 W-1/W-5). `UUID` (review D18 S-2) được dùng thật để
# dựng `tenant_id` cho chunk đó — không còn là import chết.
_STABILITY_CHILD = textwrap.dedent(
    """
    import asyncio, sys
    from uuid import UUID
    from studio_contracts import KbSearchResultItem, Node, NodeType
    from studio_engine.demo_stubs import EmptyEmbedding, FixtureLLM
    from studio_engine.executors import LlmStepExecutor

    async def main() -> None:
        chunk = KbSearchResultItem(
            chunk_id={chunk_id!r},
            text={chunk_text!r},
            score=0.9,
            tenant_id=UUID("a0000000-0000-0000-0000-000000000001"),
            section_role="public",
        )
        node = Node(
            id="n_llm",
            type=NodeType.LLM_STEP,
            params={{"prompt": "x", "kwargs": {{}}, "retrieved_chunks": [chunk]}},
        )
        result = await LlmStepExecutor(FixtureLLM({case_id!r}), EmptyEmbedding()).execute(node)
        sys.stdout.write(repr((result["answer"], result["citations"])))

    asyncio.run(main())
    """
)


def _child_env() -> dict[str, str]:
    """Env tối thiểu cho tiến trình con: giữ `PYTHONPATH` để nó import được
    `studio_engine` giống hệt tiến trình cha (uv workspace, không cài
    site-packages) — cùng helper `test_embedding_service_contract.py::_child_env`."""
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    return env


def _replay_in_child(case_id: str, chunk_id: str, chunk_text: str, hash_seed: str) -> str:
    script = _STABILITY_CHILD.format(case_id=case_id, chunk_id=chunk_id, chunk_text=chunk_text)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={**_child_env(), "PYTHONHASHSEED": hash_seed},
    )
    return completed.stdout.strip()


def test_fixture_llm_replay_stable_across_pythonhashseed() -> None:
    """Bắt buộc, không chỉ đủ — cùng case_id ra cùng `(answer, citations)`
    qua BA tiến trình `PYTHONHASHSEED` khác nhau (`0`, `1`, `424242`, cùng
    mẫu `test_embedding_service_contract.py::
    test_e3_tat_dinh_qua_hai_tien_trinh_khac_pythonhashseed`, mà bài đó dùng
    ba seed thay vì hai đúng vì lý do sau — review D18 W-2). Một trong hai
    lượt chạy trong CÙNG process sẽ xanh giả nếu bất định phụ thuộc hash —
    nhiều `PYTHONHASHSEED` khác nhau đóng lỗ đó.

    Ba seed chứ không hai: hai seed trùng nhau có thể là trùng may; seed `0`
    tắt hẳn randomization nên nó là mốc đối chiếu khác bản chất với hai seed
    còn lại (bật randomization).

    `retrieved_chunks` (review D18 W-1) chứa `chunk-001` thật — `citations`
    được đối chiếu với giá trị cụ thể `["chunk-001"]`, không chỉ với chính
    nó, nên bài này thật sự exercise được đường `retrieved_ids` (một `set`)
    -> `_CITATION_RE.findall` (`executors.py::LlmStepExecutor.execute`): một
    mutation tương lai đổi đường đó sang lặp trực tiếp trên `set` (thay vì
    lọc theo thứ tự `findall`) sẽ đổi *thứ tự* `citations` giữa các
    `PYTHONHASHSEED` khác nhau và bài này sẽ bắt được, trong khi `citations`
    luôn `[]` thì không bài nào bắt được gì cả.
    """
    payloads = {
        seed: _replay_in_child("smoke-01", "chunk-001", _SMOKE_01_CHUNKS[0].text, seed) for seed in ("0", "1", "424242")
    }

    distinct = set(payloads.values())
    assert len(distinct) == 1, f"llm-step replay đổi theo PYTHONHASHSEED — vi phạm tính ổn định: {payloads}"
    payload = distinct.pop()
    # review D18 S-3: `startswith("(")` alone also matches `repr(('', []))` —
    # an empty-answer/empty-citations payload would pass that guard silently.
    # Parse the real tuple back out and assert the answer portion is
    # non-empty (mirroring `test_fixture_llm_replay_stable_across_new_fixture_cases`
    # below) AND that citations resolved to the expected non-empty value.
    assert payload.startswith("("), "tiến trình con không trả về payload — bài này đang đo nhầm thứ"
    answer, citations = ast.literal_eval(payload)
    assert answer, "tiến trình con trả answer rỗng — bài này đang đo nhầm thứ"
    assert citations == ["chunk-001"], (
        f"citations lệch khỏi kỳ vọng ({citations!r}) — chunk không thực sự được exercised qua "
        "đường retrieved_ids (set) -> _CITATION_RE.findall, bài này đang đo nhầm thứ"
    )


async def test_fixture_llm_replay_stable_across_new_fixture_cases() -> None:
    """`tests/fixtures/llm_step/` có ≥3 case (Success criterion #2) — bài
    này chứng minh `FixtureLLM` replay ổn định KHÔNG chỉ trên `smoke-01`, mà
    trên các case case_id tự đặt riêng cho phase này.

    `d18-stability-02` cites its one retrieved chunk (`chunk-wfh-01`,
    `citations` non-empty, `refused=False`); `d18-stability-03` (post review
    D18 W-4 fixture fix) does NOT — its excerpt is the wrong topic (overtime,
    not maternity leave) and its recorded `response` no longer bracket-cites
    the chunk it was given, so `citations == []` and `refused` correctly
    comes out `True`. Both are asserted against `_EXPECTED_CITATIONS` (review
    D18 W-1), not just checked for self-equality across two runs — the two
    cases together exercise both the "cites what it was given" AND "grounds
    nothing, correctly reads as a refusal" paths."""
    for case_id in ("d18-stability-02", "d18-stability-03"):
        node = _llm_step_node(_FIXTURE_CHUNKS[case_id])
        first = await LlmStepExecutor(FixtureLLM(case_id), EmptyEmbedding()).execute(node)
        second = await LlmStepExecutor(FixtureLLM(case_id), EmptyEmbedding()).execute(node)

        assert isinstance(first, dict) and isinstance(second, dict)
        assert first["answer"] == second["answer"] != ""
        assert first["citations"] == second["citations"] == _EXPECTED_CITATIONS[case_id]
        assert first["refused"] == second["refused"] == (not _EXPECTED_CITATIONS[case_id])
        assert first["llm_source"] == "stub"
