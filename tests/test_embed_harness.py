"""Khoá seam `embed_harness.py` (AIE-1, D14 #96) — T1-T7 theo phase-3.

Ràng buộc chung (F10/F11): mọi test gọi `as_embedder` phải khai bằng `def test_...`,
KHÔNG `async def` — trừ T6, test cố ý dùng `async def` để khoá đúng điều ngược lại
(`asyncio.run` bên trong adapter phải nổ `RuntimeError` khi gọi từ trong một event loop
đang chạy). Repo bật `asyncio_mode = "auto"` (`pyproject.toml:69`), nên một `async def
test_...` khác vô tình viết thành async sẽ chạy trong event loop và `as_embedder` bên
trong nó luôn nổ — đây chính là bẫy T6 khoá lại một cách tường minh.

Nạp `embed_harness.py` qua `importlib.util.spec_from_file_location` thay vì
`conftest.py` + `sys.path`: `apps/studio/tests/` đã có sẵn một `conftest.py` khác (từ
D13, không liên quan phase này), và mypy đặt tên module bằng basename khi thư mục
không có `__init__.py` — hai `conftest.py` cùng tên ở hai `tests/` khác nhau va nhau
thành `Duplicate module named "conftest"` khi chạy `mypy packages apps`. Thêm
`__init__.py` để tách namespace lại phá 10 file test khác trong `packages/engine/tests/`
đang import chéo kiểu phẳng (`from test_xxx import ...`), mà phase này không được đụng
vào ("không đụng ... mọi test khác"). `importlib` nạp thẳng theo đường dẫn file, không
cần thêm gì vào `sys.path` — né được cả hai vấn đề, và là fallback đã được chính rủi ro
P3-R1 của phase cho phép dùng khi `conftest.py` "bị từ chối vì lý do nào đó".
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from studio_contracts import EmbeddingService

_EMBED_HARNESS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "embed_harness.py"
_spec = importlib.util.spec_from_file_location("embed_harness", _EMBED_HARNESS_PATH)
assert _spec is not None and _spec.loader is not None, f"không nạp được {_EMBED_HARNESS_PATH}"
_embed_harness = importlib.util.module_from_spec(_spec)
# Đăng ký vào `sys.modules` TRƯỚC `exec_module`: `measure_chunk_embed.py` tự nó viết
# `from embed_harness import as_embedder, load_cases` (đúng theo yêu cầu 3 của phase),
# và dòng đó chỉ resolve được nếu "embed_harness" đã có sẵn trong `sys.modules` — không
# có `conftest.py`/`sys.path` nào chèn `scripts/` cho nó dựa vào nữa (xem docstring trên).
sys.modules["embed_harness"] = _embed_harness
_spec.loader.exec_module(_embed_harness)

as_embedder = getattr(_embed_harness, "as_embedder")  # noqa: B009 — getattr(name) trả Any, mypy strict chấp nhận
load_cases = getattr(_embed_harness, "load_cases")  # noqa: B009


def _load_measure_chunk_embed() -> Any:
    """Nạp `measure_chunk_embed.py` cùng cơ chế `importlib` — LAZY (gọi trong thân test,
    sau `pytest.importorskip`), vì module đó `import studio_kb` ở mức module: nạp nó ở
    mức module-của-test-file sẽ nổ cứng ở collection khi thiếu dep, thay vì SKIP sạch.

    `embed_harness` đã nằm sẵn trong `sys.modules` (đăng ký ở trên) nên
    `from embed_harness import ...` bên trong `measure_chunk_embed.py` resolve được mà
    không cần `scripts/` trên `sys.path`. Tự bản thân `measure_chunk_embed` cũng phải
    đăng ký vào `sys.modules` TRƯỚC `exec_module`: file dùng `from __future__ import
    annotations` (mọi annotation hoá thành chuỗi), nên `@dataclass` trên `ServiceEntry`
    cần `sys.modules[cls.__module__]` có thật để suy giải kiểu — thiếu nó `dataclasses`
    tự ném `AttributeError` (đã thấy khi bỏ dòng đăng ký này).
    """
    path = Path(__file__).resolve().parents[1] / "scripts" / "measure_chunk_embed.py"
    spec = importlib.util.spec_from_file_location("measure_chunk_embed", path)
    assert spec is not None and spec.loader is not None, f"không nạp được {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure_chunk_embed"] = module
    spec.loader.exec_module(module)
    return module


class _FixedService:
    """Service test-local: luôn trả đúng 1 vector cố định, bất kể input."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]]


class _TwoVectorsService:
    """Service test-local trả 2 vector cho 1 text — adapter phải lấy phần tử đầu."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 2.0], [3.0, 4.0]]


def test_as_embedder_adapts_async_batch_protocol() -> None:
    """T1 — adapter thật sự gọi qua Protocol async-batch, không đi tắt."""
    embed = as_embedder(_FixedService())

    assert embed("bất kỳ") == [1.0, 0.0]


def test_test_local_registry_entries_satisfy_protocol() -> None:
    """T2 — seam chấp nhận đúng vật conformant với `EmbeddingService`.

    KHÔNG import `SERVICES` thật (F3): `SERVICES` sống ở `measure_chunk_embed.py`, file
    đó kéo theo `studio_kb` — dựng registry test-local ở đây để không phá tính "thuần
    stdlib" của bài test seam.
    """

    class _A:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] for _ in texts]

    class _B:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] for _ in texts]

    registry: dict[str, EmbeddingService] = {"a": _A(), "b": _B()}

    for impl in registry.values():
        assert isinstance(impl, EmbeddingService)


def test_as_embedder_returns_one_vector_per_text() -> None:
    """T3 — bất biến E1 của `EmbeddingService`: adapter lấy phần tử đầu, không nổ."""
    embed = as_embedder(_TwoVectorsService())

    assert embed("chỉ một text") == [1.0, 2.0]


def test_load_cases_accepts_explicit_path(tmp_path: Path) -> None:
    """T4 — chứng minh golden là tham số: đường dẫn tường minh, không phải tên khoá cứng."""
    pytest.importorskip("yaml")

    mini_golden = textwrap.dedent(
        """\
        cases:
          - id: pos-1
            query: "câu hỏi dương"
            tenant: ankor
            expected_section_role: public
            expected_citation: ["ankor-leave-001#c1"]
          - id: neg-1
            query: "câu hỏi từ chối, không có đáp án"
        """
    )
    golden_path = tmp_path / "mini-golden.yaml"
    golden_path.write_text(mini_golden, encoding="utf-8")

    cases: list[dict[str, Any]] = load_cases(golden_path)

    assert len(cases) == 1
    assert cases[0]["id"] == "pos-1"


def test_control_impl_label_says_control() -> None:
    """T5 — anti-slop: nhãn `null-const` phải chứa CONTROL, không đọc nhầm thành impl thật.

    Sống cùng T7: nhãn nằm trong `SERVICES` ở `measure_chunk_embed.py`, dùng cùng cặp
    `importorskip` (yaml + studio_kb).
    """
    pytest.importorskip("yaml")
    pytest.importorskip("studio_kb")

    mce = _load_measure_chunk_embed()

    label = mce.SERVICES["null-const"].label

    assert "control" in label.lower()


async def test_as_embedder_from_async_context_raises_runtime_error() -> None:
    """T6 (cố ý `async def`) — F10/F11: khoá đúng điều mà quy ước "phải viết `def`" ngăn.

    Repo bật `asyncio_mode = "auto"`, nên hàm này chạy TRONG một event loop đang mở.
    `as_embedder(...)("x")` gọi `asyncio.run(...)` bên trong, mà `asyncio.run` không cho
    gọi từ trong một loop đang chạy — phải nổ `RuntimeError`, không phải lặng lẽ trả kết
    quả sai hay treo.
    """
    embed = as_embedder(_FixedService())

    with pytest.raises(RuntimeError, match="running event loop"):
        embed("x")


def test_real_services_registry_satisfies_protocol() -> None:
    """T7 — phủ registry THẬT ở môi trường đủ dep; skip sạch nếu thiếu `yaml`/`studio_kb`.

    Mọi entry của `SERVICES` (kéo theo `studio_kb.embeddings.derive_vector`) phải thoả
    `EmbeddingService` và mang nhãn không rỗng.
    """
    pytest.importorskip("yaml")
    pytest.importorskip("studio_kb")

    mce = _load_measure_chunk_embed()

    assert mce.SERVICES, "SERVICES rỗng — registry chưa đăng ký impl nào"
    for key, entry in mce.SERVICES.items():
        assert isinstance(entry.impl, EmbeddingService), f"SERVICES[{key!r}] không thoả EmbeddingService"
        assert entry.label, f"SERVICES[{key!r}] thiếu nhãn"
