"""Tool-signal TEXT protocol between `run_agent_loop()` (`agent_loop.py`, phase 3)
and the `LLM` seam (`studio_contracts.LLM.complete`) — engine#33, decision A1
(`plans/260823-1249-engine33-agent-loop/plan.md`, VALIDATED, DEC-2, user-confirmed
via `AskUserQuestion` 2026-08-23). Pure functions only: no I/O, no async, no state.

Signal convention (A1): `TOOL_CALL:` is the ONE signal the loop recognizes.
Everything else — including a `TOOL_CALL:`-prefixed string with broken JSON — is
read as the agent's final answer. `FINAL_ANSWER:` (an earlier proposal) was
rejected: every `LLM` double in this repo already emits bare prose with
`[chunk_id]` brackets, so a required prefix would force rewriting every fixture
AND still need a fallback branch for unprefixed output — the fallback branch
alone already does the job. The failure direction this buys is safer too: a
model that garbles its output never triggers an extra tool turn, it just answers
early (observable via `outputs["signal"]` in the trace, see `agent_loop.py`).

Parse rule, EXACT order (do not reorder — each step is load-bearing, see plan.md
§A1 "Đánh đổi phải chấp nhận"):

  N1 — `raw.strip()`.
  N2 — strip ONE outer code-fence layer if present (``` ... ```, optional
       language tag on the opening line).
  P1 — full-match, both ends anchored: `\\ATOOL_CALL:\\s*(\\{.*\\})\\Z` (DOTALL).
       No match -> final answer (step 7). Anchoring both ends is what makes a KB
       chunk that merely CONTAINS the literal string `TOOL_CALL: {...}` in the
       MIDDLE of a longer answer read as prose, not a tool call — the anti prompt
       -injection property this module buys (plan.md R1). It does NOT stop a
       model from echoing an ENTIRE chunk verbatim as its whole output; that is a
       structural limit of a text-only signal, mitigated one layer up by
       `fence.fenced_kb_params` overriding tenant/scope regardless of what the
       "client" (the model) declares (plan.md R1(b)).
  P2 — BELT 1: `len(group(1)) > _TOOL_CALL_MAX_LEN` -> malformed immediately,
       `json.loads` is NEVER called. Copy of `_WHEN_MAX_LEN`'s pattern
       (`executors.py:377`, "the caller additionally cuts `when` off ... BEFORE
       any regex/JSON processing (belt 1)").
  P3 — BELT 2: `except ValueError, RecursionError:` (PEP 758 bare-comma, valid
       Python 3.14 grammar — `CLAUDE.md` root, engine#31/#34 incident; do NOT
       add parens, `ruff format --check` under this repo's py314 target will
       reject them). `except ValueError` ALONE is not enough: `json.loads`
       recurses on deeply-nested input and raises a bare `RecursionError`, not a
       `JSONDecodeError` — measured on this repo's own CPython 3.14.4
       (`executors.py:394-414`, `_parse_literal`'s identical belt-2 clause).
  Shape check — must be a `dict`, `tool` must be a non-empty `str`. Missing
       `params` defaults to `{}`; a `params` that IS present but not a `dict` is
       malformed.
  Fallback — anything not matching the above is `FinalAnswer(text=raw,
       malformed_tool_call=<True iff the normalized string started with
       "TOOL_CALL:">)`. `text` is the RAW, unmodified input — citation grounding
       must read exactly what the model said, not the stripped/fence-less form.

NO `eval`/`exec` anywhere in this module (locked by
`tests/test_agent_protocol.py::test_module_source_has_no_eval_or_exec`, same
anti-tamper idiom as `packages/kb/tests/test_leak_meta.py`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from studio_contracts import KbSearchResultItem

TOOL_CALL_PREFIX = "TOOL_CALL:"
KB_SEARCH_TOOL = "kb_search"

# Belt 1 (see module docstring P2) — copy of `_WHEN_MAX_LEN`'s pattern
# (`executors.py:377`), a hard cutoff BEFORE any JSON parsing is attempted.
_TOOL_CALL_MAX_LEN = 4096

# Full-match, both ends anchored (`\A`/`\Z`, not `^`/`$` — those match at line
# boundaries under no flag change, `\A`/`\Z` match only the true string ends).
# DOTALL so a pretty-printed multi-line JSON payload still matches `.` across
# newlines. Greedy `.*` is deliberate: with both ends anchored, greedy is
# CORRECT (it must reach the true last `}`), not a bug — see plan.md risk table.
_TOOL_CALL_RE = re.compile(r"\ATOOL_CALL:\s*(\{.*\})\Z", re.DOTALL)

# One outer code-fence layer: optional language tag on the opening line, closing
# fence alone on its own line at the very end.
_CODE_FENCE_RE = re.compile(r"\A```[^\n]*\n(.*)\n```\Z", re.DOTALL)

# Same bracket-citation shape `executors.py::_CITATION_RE` extracts
# (`executors.py:30`) — kept as a 3rd local copy per repo idiom (already copied
# once in `test_cross_tenant_refusal_audit.py:73`, once in
# `scripts/run_golden_batch.py:69`); change one, change all three.
_CITATION_RE = re.compile(r"\[([\w#-]+)\]")

# Same rendering shape as `build_prompt`'s excerpt block (`executors.py:74`):
# `[chunk_id]` alone on its own line, then the chunk text, excerpts blank-line
# separated. `ground_citations` below depends on this shape matching
# `_CITATION_RE` — if one drifts, so must the other (plan.md "Trùng lặp CHẤP
# NHẬN" #2).
_NO_KB_EXCERPT = "(không có đoạn trích nào được truy xuất)"

# v0 prompt-hint catalog (NOT a tool registry/schema SSOT — just what this
# module tells the model to expect as params for the 3 tools known at design
# time). A tool name outside this map still gets listed, just without a param
# hint.
_TOOL_PARAM_HINTS: dict[str, str] = {
    "kb_search": 'params: {"query": str, "top_k"?: int}',
    "calculator": 'params: {"expression": str}',
    "current_datetime": 'params: {"mode"?: str, "from_date"?: str, "to_date"?: str}',
}


@dataclass(frozen=True)
class ToolCall:
    tool: str
    params: dict[str, object]


@dataclass(frozen=True)
class FinalAnswer:
    text: str
    malformed_tool_call: bool = False


AgentSignal = ToolCall | FinalAnswer


@dataclass(frozen=True)
class Observation:
    """One completed tool turn, threaded into the next prompt.
    `params` is whatever the tool call actually carried at THIS layer — for
    `kb_search` that is the caller's/model's ORIGINAL params, pre-fence (see
    `agent_loop.py`'s M7 note); this module does not care where `params` came
    from, it only renders/consumes it."""

    tool: str
    params: dict[str, object]
    result_text: str


def _strip_code_fence(text: str) -> str:
    """N2 — remove exactly one outer ``` ... ``` layer if the WHOLE (already
    N1-stripped) string is wrapped in one. A fence that does not wrap the whole
    string (e.g. mid-answer) is left alone — that is prose, not a signal."""
    match = _CODE_FENCE_RE.match(text)
    return match.group(1).strip() if match else text


def parse_agent_signal(raw: str) -> AgentSignal:
    """Parse one `LLM.complete()` output into a `ToolCall` or `FinalAnswer`.
    See module docstring for the exact N1/N2/P1/P2/P3 rule this implements."""
    normalized = _strip_code_fence(raw.strip())  # N1, N2
    match = _TOOL_CALL_RE.match(normalized)  # P1
    if match is None:
        malformed = normalized.startswith(TOOL_CALL_PREFIX)
        return FinalAnswer(text=raw, malformed_tool_call=malformed)

    candidate = match.group(1)
    if len(candidate) > _TOOL_CALL_MAX_LEN:  # P2 — belt 1, before any JSON parsing
        return FinalAnswer(text=raw, malformed_tool_call=True)

    try:
        payload = json.loads(candidate)
    except ValueError, RecursionError:  # noqa — P3, PEP 758 py314, do NOT add parens (CLAUDE.md root)
        return FinalAnswer(text=raw, malformed_tool_call=True)

    if not isinstance(payload, dict):
        return FinalAnswer(text=raw, malformed_tool_call=True)
    tool = payload.get("tool")
    if not isinstance(tool, str) or not tool:
        return FinalAnswer(text=raw, malformed_tool_call=True)
    params = payload.get("params", {})
    if not isinstance(params, dict):
        return FinalAnswer(text=raw, malformed_tool_call=True)

    return ToolCall(tool=tool, params=params)


def render_tool_catalog(tool_names: Sequence[str]) -> str:
    """v0 prompt hint, NOT a schema registry (see module-level
    `_TOOL_PARAM_HINTS` docstring note) — 1 line per tool name."""
    lines = []
    for name in tool_names:
        hint = _TOOL_PARAM_HINTS.get(name)
        lines.append(f"- {name} ({hint})" if hint else f"- {name}")
    return "\n".join(lines)


def render_kb_observation(chunks: Sequence[KbSearchResultItem]) -> str:
    """Render `kb_search` results in the SAME `[chunk_id]\\n<text>` shape as
    `build_prompt`'s excerpt block (`executors.py:74`) — `ground_citations`
    below depends on this shape lining up with `_CITATION_RE`. Empty input says
    so explicitly (same reasoning as `_NO_EXCERPT`, `executors.py:51`): an empty
    retrieval is a valid result, not a rendering gap to leave blank."""
    if not chunks:
        return _NO_KB_EXCERPT
    return "\n\n".join(f"[{chunk.chunk_id}]\n{chunk.text}" for chunk in chunks)


_CONVENTION_BLOCK = (
    "Quy ước bắt buộc:\n"
    "- Muốn gọi tool: in CHỈ MỘT DÒNG DUY NHẤT theo đúng dạng\n"
    '  TOOL_CALL: {"tool": "...", "params": {...}}\n'
    "  không thêm chữ nào khác, không bọc code-fence.\n"
    "- Khi đã đủ thông tin để trả lời: trả lời thẳng bằng văn xuôi và trích dẫn\n"
    "  chunk_id trong ngoặc vuông, ví dụ [doc-1#c1].\n"
    "- Nếu các đoạn trích không chứa câu trả lời: nói rõ là không có thông tin\n"
    "  và KHÔNG trích dẫn gì."
)


def build_agent_prompt(
    *,
    instructions: str,
    question: str,
    tool_names: Sequence[str],
    observations: Sequence[Observation],
) -> str:
    """Assemble the full prompt for one loop turn: `instructions` (if any) ->
    tool-call convention block + catalog -> observation transcript, in order ->
    the question. `observations` render in the exact order given — the caller
    (`agent_loop.py`) is responsible for turn ordering."""
    blocks: list[str] = []
    if instructions:
        blocks.append(instructions)
    blocks.append(_CONVENTION_BLOCK)
    blocks.append("Tool khả dụng:\n" + render_tool_catalog(tool_names))
    for obs in observations:
        blocks.append(f"[Kết quả {obs.tool}]\n{obs.result_text}")
    blocks.append(f"Câu hỏi: {question}")
    return "\n\n".join(blocks)


def ground_citations(answer: str, chunks: Sequence[KbSearchResultItem]) -> list[str]:
    """Copy of `executors.py:357-358`'s grounding rule: a citation survives only
    if it is BOTH bracket-cited in `answer` AND present in `chunks`. Empty
    `chunks` -> `[]` unconditionally, no fallback to raw extraction — that
    fallback is exactly the "hallucinated citation" bug `executors.py:290-294`
    describes. Order follows the ORDER citations appear in `answer`
    (`_CITATION_RE.findall`), matching `executors.py:358`."""
    retrieved_ids = {chunk.chunk_id for chunk in chunks}
    return [cid for cid in _CITATION_RE.findall(answer) if cid in retrieved_ids]
