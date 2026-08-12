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
   characterization/fence test (`docs/code-standards.md` §4.1): it locks an
   already-true property, it does not chase a bug, so it is allowed to be
   green immediately.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

from studio_contracts import Node, NodeType
from studio_engine.demo_stubs import EmptyEmbedding, FixtureLLM
from studio_engine.executors import LlmStepExecutor


class _AdHocGatewayLLM:
    """A minimal `LLM`-shaped double defined only in this test — deliberately
    NOT `FixtureLLM` and NOT any other known double from `demo_stubs.py`, so
    it exercises the default-to-"gateway" branch of the classification."""

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return "Câu trả lời từ một impl LLM không xác định."


def _llm_step_node() -> Node:
    return Node(id="n_llm", type=NodeType.LLM_STEP, params={"prompt": "x", "kwargs": {}})


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
    """Characterization/fence, green immediately (`docs/code-standards.md`
    §4.1) — `FixtureLLM.complete()` only reads a file and returns a string,
    no `random`/`time`/`id()`-order, so replaying the SAME case twice
    in-process yields identical `answer` + `citations`."""
    node = _llm_step_node()

    first = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)
    second = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)

    assert isinstance(first, dict) and isinstance(second, dict)
    assert first["answer"] == second["answer"]
    assert first["citations"] == second["citations"]


# Chạy trong tiến trình con — in ra `(answer, citations)` tất định cho một
# case cố định, cùng mẫu `test_embedding_service_contract.py::_E3_CHILD`
# (harness subprocess/PYTHONHASHSEED có sẵn, không phát minh mới).
_STABILITY_CHILD = textwrap.dedent(
    """
    import asyncio, sys
    from uuid import UUID
    from studio_contracts import Node, NodeType
    from studio_engine.demo_stubs import EmptyEmbedding, FixtureLLM
    from studio_engine.executors import LlmStepExecutor

    async def main() -> None:
        node = Node(id="n_llm", type=NodeType.LLM_STEP, params={{"prompt": "x", "kwargs": {{}}}})
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


def _replay_in_child(case_id: str, hash_seed: str) -> str:
    script = _STABILITY_CHILD.format(case_id=case_id)
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
    qua hai tiến trình `PYTHONHASHSEED` khác nhau (`0` và `424242`, cùng mẫu
    `test_embedding_service_contract.py::test_e3_tat_dinh_qua_hai_tien_trinh_khac_pythonhashseed`).
    Một trong hai lượt chạy trong CÙNG process sẽ xanh giả nếu bất định phụ
    thuộc hash — hai `PYTHONHASHSEED` khác nhau đóng lỗ đó."""
    payloads = {seed: _replay_in_child("smoke-01", seed) for seed in ("0", "424242")}

    distinct = set(payloads.values())
    assert len(distinct) == 1, f"llm-step replay đổi theo PYTHONHASHSEED — vi phạm tính ổn định: {payloads}"
    assert distinct.pop().startswith("("), "tiến trình con không trả về payload — bài này đang đo nhầm thứ"


async def test_fixture_llm_replay_stable_across_new_fixture_cases() -> None:
    """`tests/fixtures/llm_step/` có ≥3 case (Success criterion #2) — bài
    này chứng minh `FixtureLLM` replay ổn định KHÔNG chỉ trên `smoke-01`, mà
    trên các case case_id tự đặt riêng cho phase này."""
    for case_id in ("d18-stability-02", "d18-stability-03"):
        node = _llm_step_node()
        first = await LlmStepExecutor(FixtureLLM(case_id), EmptyEmbedding()).execute(node)
        second = await LlmStepExecutor(FixtureLLM(case_id), EmptyEmbedding()).execute(node)

        assert isinstance(first, dict) and isinstance(second, dict)
        assert first["answer"] == second["answer"] != ""
        assert first["citations"] == second["citations"]
        assert first["llm_source"] == "stub"
