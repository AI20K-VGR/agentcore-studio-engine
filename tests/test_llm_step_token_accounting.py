"""D19 (kit#121) — real token accounting + idempotent-across-replay for
`LlmStepExecutor.execute()` (spec AIE-1, plan
`260812-1133-d18-d20-aie1-engine-daily-prs` phase 2).

3 concerns pinned here:

1. `tokens` is a REAL whitespace-split count of the built `prompt` and the
   `answer` — not the `Tokens(prompt=0, completion=0)` hardcode this phase
   replaces (`executors.py::LlmStepExecutor.execute`). RED before Task 1's
   implementation: `Tokens(0, 0)` always fails the exact-count assertions
   below (a known 3-word prompt and the fixture's own word-counted answer).
2. Idempotent WITHIN one process — calling `execute()` twice on the same
   node/case yields identical `tokens`. `FixtureLLM.complete()` is pure
   fixture replay (`demo_stubs.py`), so this is a fence test on an
   already-true property, same convention as
   `test_llm_step_output_stability.py::test_fixture_llm_replay_stable_within_one_process`.
3. Idempotent ACROSS processes — same case replayed via `subprocess` under 3
   different `PYTHONHASHSEED` values (`"0"`, `"1"`, `"424242"`) yields
   identical `tokens`. Reuses the exact subprocess + `PYTHONHASHSEED`
   harness pattern from `test_llm_step_output_stability.py`
   (`_STABILITY_CHILD`/`_child_env`/`_replay_in_child`, itself following
   `test_embedding_service_contract.py::
   test_e3_tat_dinh_qua_hai_tien_trinh_khac_pythonhashseed`) — adapted here
   to assert on `tokens` instead of `(answer, citations)`.

A 4th, unrelated tiny test lives at the bottom of this file (Task 4's own
success criterion): the D19 retrieval-failure-modes design-note exists and
is non-empty. It does NOT verify the note's prose/`file:line` anchors are
accurate — that is a human-review job (see the design-note's own header).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from uuid import UUID

from studio_contracts import KbSearchResultItem, Node, NodeType, Tokens
from studio_engine.demo_stubs import EmptyEmbedding, FixtureLLM
from studio_engine.executors import LlmStepExecutor

ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")

# Same fixture-load pattern as `test_executors_behavior.py:31` — read the
# recorded `response` back out so the expected completion count is derived
# from the fixture text (`len(text.split())`, the same rule the executor
# itself uses), not a hardcoded magic number that could drift from the
# fixture file.
_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "llm_step" / "smoke-01.json"

# `chunk-001` is `smoke-01`'s real cited chunk (`tests/fixtures/llm_step/
# smoke-01.json`'s recorded `response` bracket-cites it) — same fixture/chunk
# pairing `test_llm_step_output_stability.py::_SMOKE_01_CHUNKS` uses.
_SMOKE_01_CHUNKS = [
    KbSearchResultItem(
        chunk_id="chunk-001",
        text="Nhân viên tenant ankor được nghỉ phép năm 12 ngày.",
        score=0.9,
        tenant_id=ANKOR_ID,
        section_role="public",
    )
]


def _smoke_01_node(prompt: str = "x") -> Node:
    return Node(
        id="n_llm",
        type=NodeType.LLM_STEP,
        params={"prompt": prompt, "kwargs": {}, "retrieved_chunks": list(_SMOKE_01_CHUNKS)},
    )


async def test_llm_step_emits_real_nonzero_tokens() -> None:
    """RED today: `Tokens(0, 0)` hardcode fails both `> 0` assertions. GREEN
    after Task 1 replaces it with a real whitespace-split count of the built
    `prompt` and the `answer`.

    A single-word declared `prompt` (the old `"x"`) can't distinguish a real
    word-count from a hardcoded non-zero constant, and a bare `> 0` on both
    fields wouldn't catch a prompt/completion swap bug — so this uses a known
    3-word declared prompt and asserts the EXACT counts: `tokens.prompt == 3`
    (`"alpha beta gamma".split()`) and `tokens.completion` == the exact
    word-count of `FixtureLLM("smoke-01")`'s recorded answer, read straight
    from the fixture file (`_FIXTURE_PATH`'s `"response"` field) so the
    expectation can't drift from what the fixture actually says. `FixtureLLM`
    ignores the prompt entirely and always replays the fixture's `response`
    (`demo_stubs.py`), so the declared prompt only affects `.prompt`, never
    `.completion` — the two counts are independent, real measurements."""
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_completion = len(fixture["response"].split())
    node = _smoke_01_node(prompt="alpha beta gamma")
    result = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)

    assert isinstance(result, dict)
    tokens = result["tokens"]
    assert isinstance(tokens, Tokens)
    assert tokens.prompt == 3
    assert tokens.completion == expected_completion
    # Catches a prompt/completion field swap — with a 3-word prompt and a
    # 21-word fixture answer the two counts are far apart, so an accidental
    # swap (e.g. `Tokens(completion=.., prompt=..)` args flipped) fails loud.
    assert tokens.prompt != tokens.completion


async def test_llm_step_tokens_idempotent_within_one_process() -> None:
    """Same node/case, called twice in the same process → identical
    `tokens`. `FixtureLLM.complete()` is pure fixture replay (no `random`/
    `time`/`id()`-order), so this locks an already-true property — fence
    test, allowed green immediately (same convention as
    `test_llm_step_output_stability.py::test_fixture_llm_replay_stable_within_one_process`)."""
    node = _smoke_01_node()

    first = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)
    second = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)

    assert isinstance(first, dict) and isinstance(second, dict)
    assert first["tokens"] == second["tokens"]


# Chạy trong tiến trình con — in ra `tokens` tất định cho case `smoke-01`,
# cùng mẫu `test_llm_step_output_stability.py::_STABILITY_CHILD` (chính mẫu
# đó phỏng theo `test_embedding_service_contract.py::_E3_CHILD`) — chỉ đổi
# payload in ra từ `(answer, citations)` sang `(prompt, completion)` của
# `tokens`.
_TOKENS_CHILD = textwrap.dedent(
    """
    import asyncio, sys
    from uuid import UUID
    from studio_contracts import KbSearchResultItem, Node, NodeType
    from studio_engine.demo_stubs import EmptyEmbedding, FixtureLLM
    from studio_engine.executors import LlmStepExecutor

    async def main() -> None:
        chunk = KbSearchResultItem(
            chunk_id="chunk-001",
            text="Nhân viên tenant ankor được nghỉ phép năm 12 ngày.",
            score=0.9,
            tenant_id=UUID("a0000000-0000-0000-0000-000000000001"),
            section_role="public",
        )
        node = Node(
            id="n_llm",
            type=NodeType.LLM_STEP,
            params={"prompt": "x", "kwargs": {}, "retrieved_chunks": [chunk]},
        )
        result = await LlmStepExecutor(FixtureLLM("smoke-01"), EmptyEmbedding()).execute(node)
        tokens = result["tokens"]
        sys.stdout.write(repr((tokens.prompt, tokens.completion)))

    asyncio.run(main())
    """
)


def _child_env() -> dict[str, str]:
    """Env tối thiểu cho tiến trình con: giữ `PYTHONPATH` để nó import được
    `studio_engine` giống hệt tiến trình cha (uv workspace, không cài
    site-packages) — cùng helper `test_llm_step_output_stability.py::_child_env`."""
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    return env


def _replay_tokens_in_child(hash_seed: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _TOKENS_CHILD],
        capture_output=True,
        text=True,
        check=False,
        env={**_child_env(), "PYTHONHASHSEED": hash_seed},
    )
    # `check=False` + an explicit assert here (instead of `check=True`) so an
    # import/crash in the child surfaces its real stderr in the pytest
    # failure output, rather than an opaque `CalledProcessError` that hides
    # the actual traceback.
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_llm_step_tokens_idempotent_across_pythonhashseed() -> None:
    """Cùng case `smoke-01` ra cùng `tokens` qua BA tiến trình con
    `PYTHONHASHSEED` khác nhau (`0`, `1`, `424242`) — cùng mẫu
    `test_llm_step_output_stability.py::
    test_fixture_llm_replay_stable_across_pythonhashseed`, ba seed vì lý do
    tương tự: seed `0` tắt hẳn randomization (mốc đối chiếu khác bản chất),
    hai seed còn lại bật randomization nhưng khác nhau — trùng nhau ở cả 3
    thì không còn là trùng may."""
    payloads = {seed: _replay_tokens_in_child(seed) for seed in ("0", "1", "424242")}

    distinct = set(payloads.values())
    assert len(distinct) == 1, f"tokens đổi theo PYTHONHASHSEED — vi phạm idempotent qua tiến trình: {payloads}"
    payload = distinct.pop()
    assert payload.startswith("("), "tiến trình con không trả về payload tokens — bài này đang đo nhầm thứ"
    prompt_tokens, completion_tokens = ast.literal_eval(payload)
    assert prompt_tokens > 0
    assert completion_tokens > 0


_DESIGN_NOTE_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "design-notes" / "aie1-day19-retrieval-failure-modes.md"
)


def test_d19_retrieval_failure_modes_design_note_exists_and_is_nonempty() -> None:
    """Task 4's own success criterion — the design-note file exists and its
    stripped content is non-empty. Does NOT verify prose content or
    `file:line` anchor accuracy (a human reviewer's job, per the note's own
    header)."""
    assert _DESIGN_NOTE_PATH.is_file(), f"missing design-note: {_DESIGN_NOTE_PATH}"
    assert _DESIGN_NOTE_PATH.read_text(encoding="utf-8").strip() != ""
