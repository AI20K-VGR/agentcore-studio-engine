"""Closed-set registry guard (R-SPEC A2, spec AIE-1) — XANH: locks the
engine's node-executor registry to exactly the 6 closed `NodeType` values
imported from `studio_contracts`, so a regression can never grow a 7th
node-type or a turing-complete DSL (cap cứng — CẤM thêm node ngoài 6 loại).
Real enforcement already lives one layer down (the pydantic `NodeType` enum,
P2); this is the engine-side second belt (`registry.py`'s own docstring).
"""

from __future__ import annotations

import pytest
from studio_contracts import NodeType
from studio_engine.registry import REGISTRY, get_executor_class


def test_registry_has_exactly_six() -> None:
    """KHOÁ: `REGISTRY`'s key-set is exactly the 6 `NodeType` members — no
    fewer (a missing node-type would break dispatch), no more (a 7th key
    would be the closed-set regression this test exists to catch)."""
    assert set(REGISTRY.keys()) == set(NodeType)
    assert len(REGISTRY) == 6


def test_unknown_node_type_rejected() -> None:
    """KHOÁ: a node.type outside the 6 closed values is rejected — here at
    the registry lookup (`KeyError`), on top of the pydantic enum (P2) that
    already makes such a value unconstructable on `Node.type` upstream."""
    with pytest.raises(KeyError):
        get_executor_class("not-a-real-node-type")  # type: ignore[arg-type]
