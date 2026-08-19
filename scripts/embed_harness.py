"""Seam `EmbeddingService` cắm-được cho `measure_chunk_embed.py` (AIE-1, D14 #96).

Module này **thuần stdlib + `studio_contracts` (chỉ để lấy kiểu Protocol)** — không import
gói của DE (chấm dứt bằng `_kb` — cố ý không viết liền ra đây, xem grep gate ở Success
criteria của phase-3). Chỉ 2 thứ sống ở đây:

- `as_embedder` — adapter từ Protocol async-batch `EmbeddingService.embed` sang chữ ký
  đồng bộ 1-text mà `measure_chunk_embed.score()` đang dùng.
- `load_cases` — nhận **tên** golden (resolve dưới `packages/kb/src/studio_kb/golden/`) hoặc
  **đường dẫn** tường minh, để bộ golden trở thành tham số thay vì khoá cứng.

Vì sao registry các impl KHÔNG sống ở đây: nó phải trỏ tới hàm sinh vector của gói kb
(`embeddings.derive_vector`), mà đưa import đó vào module này thì module hết "thuần
stdlib" — mâu thuẫn với chính tiêu chí grep ở Success criteria của phase. Registry ở lại
`measure_chunk_embed.py`, file đã được phép import gói đó (tên registry: xem module đó).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from studio_contracts import EmbeddingService

Embedder = Callable[[str], list[float]]


def as_embedder(service: EmbeddingService) -> Embedder:
    """Adapt `EmbeddingService.embed(texts) -> list[list[float]]` sang 1-text đồng bộ.

    Gọi `asyncio.run` cho MỖI text — đơn giản, đúng cho corpus hiện tại (~330 hàng lớn
    nhất, đo thật 2026-08-06). `asyncio.run` không cho gọi từ trong một event loop đang
    chạy: một `async def test_...` (pytest `asyncio_mode=auto` tự bọc loop) gọi hàm trả
    về từ đây sẽ ăn `RuntimeError: ... cannot be called from a running event loop` — đó
    là hành vi ĐÚNG cần khoá lại (xem `test_as_embedder_from_async_context_raises_runtime_error`
    trong `tests/test_embed_harness.py`), không phải lỗi cần né bằng
    `asyncio.get_event_loop()`.

    Lấy phần tử ĐẦU của kết quả `embed([text])`: một impl trả nhiều hơn 1 vector cho 1
    text đầu vào (vi phạm bất biến E-1 của Protocol) vẫn không làm adapter nổ — nó chỉ
    lặng lẽ dùng vector đầu, giống cách `score()` đang tiêu thụ theo chỉ số hàng.
    """

    def _embed_one(text: str) -> list[float]:
        vectors = asyncio.run(service.embed([text]))
        return vectors[0]

    return _embed_one


def _golden_root() -> Path:
    """`packages/kb/src/studio_kb/golden/` — từ `embed_harness.py` (…/packages/engine/scripts/)
    đi lên 3 cấp tới gốc repo, khớp `_ROOT` của `measure_chunk_embed.py` (cùng độ sâu file).

    `kb#37`/`kit#181`: `golden/` dời vào trong `src/studio_kb/` để đóng gói được vào wheel —
    trước đó nằm cạnh `src/`, ngoài phạm vi wheel."""
    return Path(__file__).resolve().parents[3] / "packages" / "kb" / "src" / "studio_kb" / "golden"


def load_cases(golden: str | Path) -> list[dict[str, Any]]:
    """Nạp case DƯƠNG (có `expected_citation`) từ golden — tên (resolve dưới
    `packages/kb/src/studio_kb/golden/`) hoặc đường dẫn tường minh.

    `import yaml` đặt TRONG hàm (lazy) — chủ ý — để module này import được ở môi trường
    không cài PyYAML; chỉ lúc thật sự gọi `load_cases` mới cần dep đó.
    """
    import yaml  # type: ignore[import-untyped, unused-ignore]  # noqa: PLC0415 — lazy, xem docstring trên

    path = Path(golden)
    if not path.is_file():
        path = _golden_root() / str(golden)
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = raw["cases"]
    return [c for c in cases if c.get("expected_citation")]
