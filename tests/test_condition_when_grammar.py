"""Behavioral tests for `ConditionExecutor`'s `when` grammar v0 (phase 1, spec
AIE-1, plan `260806-0938-d14-aie1-node-executors-grid-prep`).

Real teeth per `docs/code-standards.md` §4.1: every test here pins a concrete
business property of the grammar/evaluation (never a bare
`pytest.raises(NotImplementedError)` — that form is reserved for the ONE
still-unfilled seam left after this phase,
`test_executors_behavior.py::test_tool_call_no_dispatcher_still_not_implemented`).

Grammar v0 (engine-local, free-to-change — DEC-A/DL-12.A1-1): exactly 3
tokens, `"<field> <op> <literal>"`. `field` is looked up by dict subscript
(never `getattr`); `op` is a whitelisted table (`==`, `!=`, `<=`, `>=`, `<`,
`>`); `literal` resolves via `json.loads` with a raw-string fallback — never
`eval`/`exec`/`ast.literal_eval`.
"""

from __future__ import annotations

import json

from studio_contracts import Node, NodeType
from studio_engine.executors import ConditionExecutor


async def _run(params: dict[str, object]) -> dict[str, object]:
    node = Node(id="cond", type=NodeType.CONDITION, params=params)
    result = await ConditionExecutor().execute(node)
    assert isinstance(result, dict)
    return result


async def test_condition_equality_on_upstream_state() -> None:
    """T1 — happy path, boolean literal via JSON `false`."""
    result = await _run({"when": "refused == false", "state": {"refused": False}})
    assert result["result"] is True
    assert result["reason"] == "ok"


async def test_condition_numeric_comparison_true_and_false() -> None:
    """T2 — real numeric comparison, not a hardcoded constant."""
    true_result = await _run({"when": "score >= 0.8", "state": {"score": 0.91}})
    assert true_result["result"] is True
    assert true_result["reason"] == "ok"

    false_result = await _run({"when": "score >= 0.8", "state": {"score": 0.5}})
    assert false_result["result"] is False
    assert false_result["reason"] == "ok"


async def test_condition_le_is_not_parsed_as_lt() -> None:
    """T3 — alternation-order trap (requirement 8): `<=` must not be parsed
    as `<` followed by a stray `= 1` value token."""
    result = await _run({"when": "score <= 0.5", "state": {"score": 0.5}})
    assert result["result"] is True
    assert result["reason"] == "ok"


async def test_condition_string_literal_quoted_and_bare() -> None:
    """T4 — `json.loads` for the quoted form, raw-string fallback for bare."""
    quoted = await _run({"when": 'verdict == "PASS"', "state": {"verdict": "PASS"}})
    assert quoted["result"] is True
    assert quoted["reason"] == "ok"

    bare = await _run({"when": "verdict == PASS", "state": {"verdict": "PASS"}})
    assert bare["result"] is True
    assert bare["reason"] == "ok"


async def test_condition_missing_field_reports_field_missing() -> None:
    """T5 — "could not evaluate" must never collapse into False (requirement 2)."""
    result = await _run({"when": "refused == false", "state": {}})
    assert result["result"] is None
    assert result["reason"] == "field-missing"


async def test_condition_state_not_a_dict_reports_reason() -> None:
    """T6 — upstream `kb-retrieve` output is a bare `list` (executors.py:150);
    must not blow up, must report a distinct reason."""
    result = await _run({"when": "refused == false", "state": []})
    assert result["result"] is None
    assert result["reason"] == "state-not-a-dict"


async def test_condition_without_when_returns_none_result() -> None:
    """T7 — a `condition` node with no declared `when` still runs cleanly."""
    result = await _run({})
    assert result == {"when": None, "result": None, "reason": "no-when-declared"}


async def test_malformed_grammar_does_not_raise_and_reports_unparsable() -> None:
    """T8 — F1: an exception here escapes `interpreter.py:250` (no
    try/except) and kills the whole run mid-walk. Must never raise."""
    for bad_when in ("refused", "score ~= 1", "a == "):
        result = await _run({"when": bad_when, "state": {"refused": False, "score": 1, "a": 1}})
        assert result["result"] is None
        assert result["reason"] == "unparsable-grammar"


async def test_multi_clause_expression_is_unparsable_not_false() -> None:
    """T9 — F1 (both halves): must not raise, AND must not silently compare a
    garbage RHS to produce a spurious `False`."""
    for bad_when in (
        "a == 1 and b == 2",
        "score > 0.5 && verdict == 'PASS'",
        "else",
        "true",
    ):
        result = await _run({"when": bad_when, "state": {"a": 1, "b": 2, "score": 1, "verdict": "PASS"}})
        assert result["result"] is None
        assert result["reason"] == "unparsable-grammar"


async def test_condition_does_not_execute_arbitrary_code() -> None:
    """T10 — behavioral fence (§4.1 form 2): grammar must never become a code
    execution path, regex-level AND value-level."""
    exploit_result = await _run({"when": "__import__('os').system('echo pwned') == 1", "state": {}})
    assert exploit_result["reason"] == "unparsable-grammar"
    assert exploit_result["result"] is None

    # RHS here is just an opaque non-matching string — never evaluated as code.
    literal_result = await _run({"when": 'x == __import__("os")', "state": {"x": 1}})
    assert literal_result["result"] is False
    assert literal_result["reason"] == "ok"


async def test_condition_type_mismatch_reports_reason() -> None:
    """T11 — a `TypeError` from comparing incompatible types must never
    escape `execute` (F1's same guarantee, applied to the comparison step)."""
    result = await _run({"when": "score >= 0.8", "state": {"score": "abc"}})
    assert result["result"] is None
    assert result["reason"] == "type-mismatch"


async def test_condition_output_is_json_serializable() -> None:
    """T12 — output flows straight into `TraceEvent.outputs` (interpreter.py:279-282)."""
    ok_case = await _run({"when": "score >= 0.8", "state": {"score": 0.9}})
    json.dumps(ok_case)

    error_case = await _run({"when": "score ~= 1", "state": {}})
    json.dumps(error_case)


async def test_oversized_when_is_rejected_before_parsing() -> None:
    """T14 — F2: measured DoS payloads (`"["*20000` -> `RecursionError`, NOT
    caught by a bare `except ValueError`). Must be cut off by length BEFORE
    reaching `json.loads` at all."""
    bracket_bomb = await _run({"when": "x == " + "[" * 3000, "state": {"x": 1}})
    assert bracket_bomb["result"] is None
    assert bracket_bomb["reason"] == "when-too-long"
    assert isinstance(bracket_bomb["when"], str)
    assert len(bracket_bomb["when"]) <= 200

    digit_bomb = await _run({"when": "x == " + "9" * 5000, "state": {"x": 1}})
    assert digit_bomb["result"] is None
    assert digit_bomb["reason"] == "when-too-long"
    assert isinstance(digit_bomb["when"], str)
    assert len(digit_bomb["when"]) <= 200


async def test_python_literal_casing_is_not_coerced_falls_through_as_string_mismatch() -> None:
    """T15 — F7: a NAMED trap, not an accident. JSON spells its boolean
    `true`/`false`, not Python's `True`/`False` — `"refused == True"` parses
    `True` as an opaque raw string (`json.loads("True")` fails), so it never
    equals the real Python `bool` `True` in `state`. This is documented,
    known, intentional grammar-v0 behavior (backlog: consider coercing
    Python-cased literals if SWE's future grammar uses that casing)."""
    result = await _run({"when": "refused == True", "state": {"refused": True}})
    assert result["result"] is False
    assert result["reason"] == "ok"


async def test_unparsable_grammar_wins_over_non_dict_state() -> None:
    """T16 — F5: check order locks regex failure BEFORE any `state` shape
    check, so the same broken expression reports the same reason regardless
    of what ran upstream."""
    result = await _run({"when": "a == 1 and b == 2", "state": []})
    assert result["reason"] == "unparsable-grammar"


async def test_missing_state_key_reports_no_upstream_output() -> None:
    """T17 — F8: distinguishes "no node ran before this one" (missing
    `state` key entirely) from "the upstream node returned an empty dict"."""
    result = await _run({"when": "refused == false"})
    assert result["reason"] == "no-upstream-output"
    assert result["result"] is None


async def test_when_echo_is_truncated_in_output() -> None:
    """T18 — F9: the echoed `when` string must never carry an unbounded
    client-declared value straight into the trace."""
    long_when = "a" + "b" * 249  # 250 chars, syntactically a single field token
    result = await _run({"when": long_when, "state": {}})
    assert isinstance(result["when"], str)
    assert len(result["when"]) == 200


class _RaisesOnCompare:
    """Test-local double (D14 #96 review C-1): a `state` value whose own
    comparison dunders raise something OTHER than `TypeError` — the
    reviewer's repro used exactly this shape (e.g. a numpy array or a
    hostile client-declared object). `_OPS["<"]`/`">="`/etc. dispatch to
    Python's rich comparison protocol, which calls these dunders directly;
    a narrow `except TypeError` around the comparison would not catch this,
    and `bool()` on the actual outcome is a second, separate place the same
    class of failure can surface."""

    def __gt__(self, other: object) -> bool:
        raise ValueError("comparison exploded")

    def __ge__(self, other: object) -> bool:
        raise ValueError("comparison exploded")

    def __lt__(self, other: object) -> bool:
        raise ValueError("comparison exploded")

    def __le__(self, other: object) -> bool:
        raise ValueError("comparison exploded")

    def __eq__(self, other: object) -> bool:
        raise ValueError("comparison exploded")

    def __ne__(self, other: object) -> bool:
        raise ValueError("comparison exploded")


async def test_condition_non_type_error_from_comparison_reports_type_mismatch() -> None:
    """C-1 (D14 #96 final review), bug 2: a comparison dunder raising
    `ValueError` (not `TypeError`) must still fail closed — `execute()`
    must never let it escape, and must still report the same
    `reason="type-mismatch"` label a client already expects from a
    type/comparison failure."""
    result = await _run({"when": "score >= 0.8", "state": {"score": _RaisesOnCompare()}})
    assert result["result"] is None
    assert result["reason"] == "type-mismatch"


class _BoolBoom:
    """`__bool__` itself raises — pins C-1 bug 1 specifically (`bool(outcome)`
    was OUTSIDE the try block). The comparison dunder here succeeds and
    returns this object (not a plain `bool`), so `_OPS[op](...)` does NOT
    raise; only the subsequent `bool(outcome)` coercion does. A fix that
    only widened the `except` around the comparison call (leaving `bool()`
    outside the try) would still let this one escape."""

    def __eq__(self, other: object) -> _BoolBoom:  # type: ignore[override]
        # Deliberately non-`bool` return (real classes raising in `__bool__`,
        # e.g. numpy arrays, do exactly this) — the whole point of this
        # double is to make `_OPS["=="]` succeed while handing `bool()` a
        # value it cannot coerce, so the override violates `object.__eq__`'s
        # `-> bool` contract on purpose.
        return self

    def __hash__(self) -> int:
        return id(self)

    def __bool__(self) -> bool:
        raise ValueError("bool() exploded")


async def test_condition_bool_coercion_failure_reports_type_mismatch() -> None:
    """C-1 (D14 #96 final review), bug 1: `bool(outcome)` raising must be
    caught too, not just the comparison call itself."""
    result = await _run({"when": "verdict == PASS", "state": {"verdict": _BoolBoom()}})
    assert result["result"] is None
    assert result["reason"] == "type-mismatch"


async def test_result_is_bool_iff_reason_is_ok() -> None:
    """T19 — the single place the core invariant of requirement 2 is locked:
    `result` is a `bool` if and only if `reason == "ok"`."""
    cases: list[dict[str, object]] = [
        {"when": "refused == false", "state": {"refused": False}},
        {"when": "score >= 0.8", "state": {"score": 0.5}},
        {"when": "refused == false", "state": {}},
        {"when": "refused == false", "state": []},
        {},
        {"when": "not a valid when"},
        {"when": "score >= 0.8", "state": {"score": "abc"}},
    ]
    for params in cases:
        result = await _run(params)
        assert isinstance(result["result"], bool) == (result["reason"] == "ok")
