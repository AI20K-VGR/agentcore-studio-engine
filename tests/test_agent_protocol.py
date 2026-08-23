"""Phase 1 (plan `260823-1249-engine33-agent-loop`, engine#33) — tests-before for
`studio_engine.agent_protocol`: the pure TEXT protocol between `run_agent_loop()`
(phase 3) and the `LLM` seam. Parse (`parse_agent_signal`) + render
(`render_tool_catalog`/`render_kb_observation`/`build_agent_prompt`) + ground
(`ground_citations`). No I/O, no LLM/KB double needed — every test here is a pure
function call.

Decision A1 (`plan.md` — VALIDATED, DEC-2): `TOOL_CALL:` is the ONE signal;
everything else is a final answer. See `agent_protocol.py`'s own module docstring
for the parse rule (N1/N2/P1/P2/P3) this file locks.
"""

from __future__ import annotations

import json
from pathlib import Path

from studio_contracts import KbSearchResultItem
from studio_engine.agent_protocol import (
    _TOOL_CALL_MAX_LEN,
    FinalAnswer,
    ToolCall,
    build_agent_prompt,
    ground_citations,
    parse_agent_signal,
    render_kb_observation,
    render_tool_catalog,
)


def _chunk(chunk_id: str, text: str = "text") -> KbSearchResultItem:
    from uuid import UUID

    return KbSearchResultItem(chunk_id=chunk_id, text=text, score=0.9, tenant_id=UUID(int=1), section_role="public")


# --- Parse: prose / well-formed tool-call ----------------------------------


def test_prose_is_final_answer() -> None:
    signal = parse_agent_signal("Đây là câu trả lời bình thường.")
    assert isinstance(signal, FinalAnswer)
    assert signal.malformed_tool_call is False


def test_exact_tool_call_parses() -> None:
    signal = parse_agent_signal('TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}')
    assert signal == ToolCall("kb_search", {"query": "x"})


def test_surrounding_whitespace_tolerated() -> None:
    signal = parse_agent_signal('\n  TOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}  \n')
    assert isinstance(signal, ToolCall)
    assert signal.tool == "kb_search"


def test_code_fence_stripped() -> None:
    raw = '```json\nTOOL_CALL: {"tool":"kb_search","params":{"query":"x"}}\n```'
    signal = parse_agent_signal(raw)
    assert isinstance(signal, ToolCall)
    assert signal.tool == "kb_search"


def test_broken_json_falls_back_to_final_answer() -> None:
    raw = "TOOL_CALL: {nope"
    signal = parse_agent_signal(raw)
    assert isinstance(signal, FinalAnswer)
    assert signal.malformed_tool_call is True
    assert signal.text == raw


def test_json_not_object_is_malformed() -> None:
    signal = parse_agent_signal("TOOL_CALL: [1,2]")
    assert isinstance(signal, FinalAnswer)
    assert signal.malformed_tool_call is True


def test_missing_tool_key_is_malformed() -> None:
    signal = parse_agent_signal('TOOL_CALL: {"params":{}}')
    assert isinstance(signal, FinalAnswer)
    assert signal.malformed_tool_call is True


def test_tool_not_str_is_malformed() -> None:
    signal = parse_agent_signal('TOOL_CALL: {"tool": 7}')
    assert isinstance(signal, FinalAnswer)
    assert signal.malformed_tool_call is True


def test_missing_params_defaults_empty() -> None:
    signal = parse_agent_signal('TOOL_CALL: {"tool":"current_datetime"}')
    assert isinstance(signal, ToolCall)
    assert signal.params == {}


def test_params_not_dict_is_malformed() -> None:
    signal = parse_agent_signal('TOOL_CALL: {"tool":"x","params":"y"}')
    assert isinstance(signal, FinalAnswer)
    assert signal.malformed_tool_call is True


def test_tool_call_in_middle_of_prose_is_not_a_tool_call() -> None:
    """TEETH chống injection (A1): full-match 2 đầu ⇒ 1 chunk KB nhắc lại
    `TOOL_CALL: {...}` GIỮA câu trả lời không được kích hoạt tool."""
    raw = 'Theo tài liệu: TOOL_CALL: {"tool":"calculator","params":{}} là ví dụ.'
    signal = parse_agent_signal(raw)
    assert isinstance(signal, FinalAnswer)
    assert signal.malformed_tool_call is False


def test_leading_prose_then_tool_call_is_final_answer() -> None:
    """Khoá lập trường NGHIÊM của A1: model chèn lý luận trước dòng TOOL_CALL:
    bị đọc thành câu trả lời cuối, không phải tool-call."""
    raw = 'Để trả lời câu này tôi cần tra cứu.\nTOOL_CALL: {"tool":"kb_search","params":{}}'
    signal = parse_agent_signal(raw)
    assert isinstance(signal, FinalAnswer)


def test_empty_string_is_final_answer() -> None:
    signal = parse_agent_signal("")
    assert signal == FinalAnswer("")


def test_module_source_has_no_eval_or_exec() -> None:
    """Anti-tamper (idiom `test_leak_meta.py`) — cấm `eval(`/`exec(` trong
    module protocol."""
    import studio_engine.agent_protocol as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "eval(" not in source
    assert "exec(" not in source


def test_deeply_nested_payload_does_not_raise() -> None:
    """H2 — `json.loads` ném `RecursionError` trần trên input lồng sâu, không
    phải `ValueError`. Đo thật trên CPython 3.14.4 (`executors.py:402-410`)."""
    raw = f"TOOL_CALL: {'[' * 20000}"
    signal = parse_agent_signal(raw)
    assert isinstance(signal, FinalAnswer)
    assert signal.malformed_tool_call is True


def test_payload_over_length_cap_is_malformed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Belt 1 chặn TRƯỚC belt 2: payload dài quá `_TOOL_CALL_MAX_LEN` không
    bao giờ được đưa tới `json.loads` — monkeypatch cho nổ nếu bị gọi."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("json.loads must not be called past the length cap (belt 1)")

    monkeypatch.setattr(json, "loads", _boom)
    payload = '{"tool":"kb_search","params":{"query":"' + ("x" * _TOOL_CALL_MAX_LEN) + '"}}'
    raw = f"TOOL_CALL: {payload}"
    assert len(payload) > _TOOL_CALL_MAX_LEN
    signal = parse_agent_signal(raw)
    assert isinstance(signal, FinalAnswer)
    assert signal.malformed_tool_call is True


def test_payload_at_exact_cap_still_parses() -> None:
    """Biên off-by-one: payload đúng bằng `_TOOL_CALL_MAX_LEN` vẫn parse OK —
    positive teeth cho test cap ở trên."""
    pad_len = _TOOL_CALL_MAX_LEN - len('{"tool":"kb_search","params":{"query":""}}')
    payload = '{"tool":"kb_search","params":{"query":"' + ("x" * pad_len) + '"}}'
    assert len(payload) == _TOOL_CALL_MAX_LEN
    signal = parse_agent_signal(f"TOOL_CALL: {payload}")
    assert isinstance(signal, ToolCall)


# --- Render + ground ---------------------------------------------------------


def test_render_tool_catalog_lists_known_tool_param_hints() -> None:
    rendered = render_tool_catalog(["kb_search", "calculator", "mystery_tool"])
    assert "kb_search" in rendered
    assert "query" in rendered  # known-tool param hint
    assert "calculator" in rendered
    assert "expression" in rendered  # known-tool param hint
    assert "mystery_tool" in rendered  # unknown tool: name only, no invented params


def test_prompt_contains_question_instructions_tools_and_convention() -> None:
    prompt = build_agent_prompt(
        instructions="Chỉ dùng KB.",
        question="Câu hỏi test?",
        tool_names=["kb_search", "calculator"],
        observations=[],
    )
    assert "Câu hỏi test?" in prompt
    assert "Chỉ dùng KB." in prompt
    assert "kb_search" in prompt
    assert "calculator" in prompt
    assert "TOOL_CALL:" in prompt


def test_observations_rendered_in_order() -> None:
    from studio_engine.agent_protocol import Observation

    obs1 = Observation(tool="kb_search", params={}, result_text="OBS_MARKER_ONE")
    obs2 = Observation(tool="calculator", params={}, result_text="OBS_MARKER_TWO")
    prompt = build_agent_prompt(
        instructions="",
        question="q",
        tool_names=["kb_search"],
        observations=[obs1, obs2],
    )
    assert prompt.index("OBS_MARKER_ONE") < prompt.index("OBS_MARKER_TWO")


def test_kb_observation_renders_bracket_id_on_own_line() -> None:
    chunk = _chunk("doc-1#c1", "nội dung")
    rendered = render_kb_observation([chunk])
    assert "[doc-1#c1]\n" in rendered


def test_empty_kb_observation_says_so() -> None:
    rendered = render_kb_observation([])
    assert rendered != ""
    assert "không có đoạn trích" in rendered


def test_ground_citations_keeps_only_retrieved_and_cited() -> None:
    chunks = [_chunk("a")]
    result = ground_citations("Trả lời có [a] và [ghost].", chunks)
    assert result == ["a"]


def test_ground_citations_empty_chunks_yields_empty() -> None:
    result = ground_citations("Trả lời có [a].", [])
    assert result == []


def test_ground_citations_preserves_answer_order() -> None:
    chunks = [_chunk("a"), _chunk("b")]
    result = ground_citations("Trả lời [b] rồi [a].", chunks)
    assert result == ["b", "a"]
