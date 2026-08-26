"""Phase 1 (plan `260823-1249-engine33-agent-loop`, engine#33) — tests-before for
`studio_engine.agent_protocol`: the pure TEXT protocol between `run_agent_loop()`
(phase 3) and the `LLM` seam. Parse (`parse_agent_signal`) + render
(`render_tool_catalog`/`render_kb_observation`/`build_agent_prompt`) + ground
(`ground_citations`) + faithfulness-verify prompt/parse
(`build_faithfulness_prompt`/`parse_faithfulness_verdict`, engine#43). No I/O,
no LLM/KB double needed — every test here is a pure function call.

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
    HistoryTurn,
    Observation,
    ToolCall,
    build_agent_prompt,
    build_faithfulness_prompt,
    ground_citations,
    parse_agent_signal,
    parse_faithfulness_verdict,
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


def test_prompt_contains_question_system_prompt_tools_and_convention() -> None:
    prompt = build_agent_prompt(
        system_prompt="Chỉ dùng KB.",
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
    obs1 = Observation(tool="kb_search", params={}, result_text="OBS_MARKER_ONE")
    obs2 = Observation(tool="calculator", params={}, result_text="OBS_MARKER_TWO")
    prompt = build_agent_prompt(
        system_prompt="",
        question="q",
        tool_names=["kb_search"],
        observations=[obs1, obs2],
    )
    assert prompt.index("OBS_MARKER_ONE") < prompt.index("OBS_MARKER_TWO")


# --- engine#47: history (multi-turn context control) ------------------------
# `HistoryTurn` carries one prior Q/A pair from a PREVIOUS chat turn (a
# different `run_agent_loop()` call), not this run's own `observations` —
# `build_agent_prompt` renders it BEFORE the current turn's observations
# (issue's own ordering requirement). Sliding-window cap and per-field
# truncation are `agent_loop.py`'s job (`_MAX_HISTORY_TURNS`,
# `_truncate_observation`) — this module only renders whatever list it is
# given, in order, same "pure, no policy" split as `observations` already has.


def test_build_agent_prompt_history_renders_before_current_observations() -> None:
    history = [HistoryTurn(question="HISTORY_Q_MARKER", answer="HISTORY_A_MARKER")]
    obs = [Observation(tool="kb_search", params={}, result_text="CURRENT_OBS_MARKER")]
    prompt = build_agent_prompt(
        system_prompt="",
        question="q",
        tool_names=["kb_search"],
        observations=obs,
        history=history,
    )
    assert "HISTORY_Q_MARKER" in prompt
    assert "HISTORY_A_MARKER" in prompt
    assert prompt.index("HISTORY_Q_MARKER") < prompt.index("CURRENT_OBS_MARKER")


def test_build_agent_prompt_history_multi_turn_preserves_order() -> None:
    history = [
        HistoryTurn(question="Q_FIRST", answer="A_FIRST"),
        HistoryTurn(question="Q_SECOND", answer="A_SECOND"),
    ]
    prompt = build_agent_prompt(
        system_prompt="", question="q", tool_names=["kb_search"], observations=[], history=history
    )
    assert prompt.index("Q_FIRST") < prompt.index("Q_SECOND")


def test_build_agent_prompt_default_history_is_empty_and_backward_compatible() -> None:
    # Every existing call site (including every other test in this file) omits
    # `history` entirely — must still work, and must render no "[Lịch sử]"
    # block at all when there is none to render.
    prompt = build_agent_prompt(system_prompt="", question="q", tool_names=["kb_search"], observations=[])
    assert "Lịch sử" not in prompt


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


# --- engine#43 (HB2-25): faithfulness-verify prompt/parse -------------------
# `ground_citations` above proves PROVENANCE only (cited id was retrieved) — it
# cannot prove SUBJECT match (the cited chunk actually answers what was asked).
# These two functions stay pure (this module's own "no I/O, no async" contract,
# DEC-2/A1) — the async `llm.complete()` orchestration around them lives in
# `agent_loop.py`.


def test_build_faithfulness_prompt_includes_chunk_id_and_text() -> None:
    # chunk_id deliberately included alongside text, not text alone: a prior
    # measurement found only 253/800 (31.6%) of the real corpus's chunks name
    # their subject inside chunk TEXT itself — chunk_id's tenant-subject-slug
    # prefix (e.g. "ankor-engineering-incident#c6") is the signal the other
    # 68.4% of the time text alone cannot provide.
    chunk = _chunk("ankor-engineering-incident#c6", text="P1 MTTR target: dưới 1 giờ.")
    prompt = build_faithfulness_prompt("Sự cố P1 của Borea?", [chunk])
    assert "ankor-engineering-incident#c6" in prompt
    assert "P1 MTTR target: dưới 1 giờ." in prompt


def test_build_faithfulness_prompt_includes_question() -> None:
    prompt = build_faithfulness_prompt("Sự cố P1 của Borea?", [_chunk("a")])
    assert "Sự cố P1 của Borea?" in prompt


def test_parse_faithfulness_verdict_co_is_co() -> None:
    assert parse_faithfulness_verdict("CO") == "CO"


def test_parse_faithfulness_verdict_khong_is_khong() -> None:
    assert parse_faithfulness_verdict("KHONG") == "KHONG"


def test_parse_faithfulness_verdict_diacritic_co_is_co() -> None:
    # Regression: `.strip().upper().startswith("CO")` (the naive form) does NOT
    # match "CÓ" — `str.upper()` does not strip Vietnamese combining marks.
    assert parse_faithfulness_verdict("CÓ") == "CO"


def test_parse_faithfulness_verdict_diacritic_khong_is_khong() -> None:
    assert parse_faithfulness_verdict("KHÔNG") == "KHONG"


def test_parse_faithfulness_verdict_khong_buried_in_sentence_is_khong() -> None:
    # PR review (engine#43/#46) regression: `.startswith("KHONG")` reads this
    # as CO because "KHÔNG" isn't the first word — but this sentence IS
    # HB2-25's own reasoning shape (the model correctly notices the chunk is
    # Ankor's while the question asks about Borea). Word-boundary detection
    # must catch a clear KHONG signal wherever it appears, not just position 0.
    verdict = parse_faithfulness_verdict("Đoạn trích thuộc tài liệu Ankor nên KHÔNG")
    assert verdict == "KHONG"


def test_parse_faithfulness_verdict_khong_prefixed_by_label_is_khong() -> None:
    assert parse_faithfulness_verdict("Câu trả lời: KHÔNG") == "KHONG"


def test_parse_faithfulness_verdict_no_co_or_khong_word_is_unparseable() -> None:
    # 3-state, not a bool (PR review point 2): callers must be able to tell
    # "model said CO" apart from "could not tell", instead of both silently
    # collapsing into one value the way the first cut did.
    assert parse_faithfulness_verdict("xin chào") == "UNPARSEABLE"
    assert parse_faithfulness_verdict("") == "UNPARSEABLE"


# --- PR #46 review round 2 regression: KHONG-before-CO priority order -------
# The word-boundary fix above (round 1) reads a clear KHONG/CO signal ANYWHERE
# in the sentence, not just position 0 — correct for HB2-25's own reasoning
# shape. But an earlier version of the function then broke the *other* way:
# it checked KHONG before CO, so any sentence where the model correctly
# answers CO but explains itself using "không" (an ordinary Vietnamese
# negation particle, unrelated to the verdict) got silently misread as
# KHONG and over-refused — the exact failure class this feature exists to
# avoid (evalhub#51, see the function's own docstring). Fixed: BOTH tokens
# present is genuinely ambiguous and must fail open to UNPARSEABLE, not pick
# a winner by priority or by which token appears first (the reviewer's own
# alternative — verified separately to only rescue 2 of these 3 sentences).


def test_parse_faithfulness_verdict_co_first_khong_word_is_unparseable() -> None:
    # Model answers CO, then explains using "không" for something else
    # entirely ("does not at all talk about a different subject") — NOT a
    # verdict of KHONG. The KHONG-before-CO priority order misread this as
    # KHONG; must be UNPARSEABLE (ambiguous), which still keeps the citation
    # at the call site, same as CO would.
    verdict = parse_faithfulness_verdict("CÓ, đoạn trích này không hề nói về chủ thể khác")
    assert verdict == "UNPARSEABLE"


def test_parse_faithfulness_verdict_khong_word_then_co_is_unparseable() -> None:
    verdict = parse_faithfulness_verdict("Có — không có gì sai ở đây")
    assert verdict == "UNPARSEABLE"


def test_parse_faithfulness_verdict_khong_word_before_co_verdict_is_unparseable() -> None:
    # KHONG token appears before the CO token here, so "take whichever
    # token appears first" (the alternative fix considered and rejected)
    # would ALSO misread this one as KHONG — only the both-present ->
    # UNPARSEABLE rule handles all 3 regression sentences correctly.
    verdict = parse_faithfulness_verdict("Đoạn trích KHÔNG sai chủ thể, nên CÓ")
    assert verdict == "UNPARSEABLE"
