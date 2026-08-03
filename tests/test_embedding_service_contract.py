"""Ghim 3 bất biến tiêu thụ của `EmbeddingService` (AIE-1, D11 #81).

Neo: `docs/contracts/embedding-service.v0.md` §2. `typing.Protocol` +
`runtime_checkable` chỉ kiểm **có method tên `embed`** — không kiểm bất cứ điều gì
về giá trị trả về. Ba bất biến dưới đây mọi bên tiêu thụ đều đang dựa vào, và cả ba
đều vỡ **im lặng** chứ không ném lỗi, nên chúng phải sống thành assert chứ không
thành câu văn trong tài liệu.

Khác `test_stub_embedding.py`: file đó kiểm `StubEmbedding` replay **đúng nội dung
đã ghi** (hành vi của một impl cụ thể). File này kiểm **hợp đồng mà mọi impl phải
thoả**, kể cả gateway chưa về — nên mọi test đều đi qua `_conforming_impls()` để
impl mới chỉ cần thêm một dòng ở đó là được kiểm đủ.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from studio_contracts import EmbeddingService
from studio_engine.demo_stubs import EmptyEmbedding, StubEmbedding

# Bề rộng vector, PHẢI khớp `packages/kb/src/studio_kb/schema.py::EMBEDDING_DIM`.
# Khai lại (không import) vì `.importlinter` cấm `studio_engine` chạm `studio_kb` —
# đây là nợ có ý thức, xem contract §2 (E-2). Đổi một bên mà quên bên kia thì
# `test_e2_width_matches_embedding_dim` đỏ ở đây, thay vì vỡ sâu trong pgvector.
EXPECTED_DIM = 8


def _conforming_impls() -> list[EmbeddingService]:
    """Mọi impl PHẢI thoả đủ E-1/E-2/E-3. Gateway về thì thêm vào đây.

    `EmptyEmbedding` KHÔNG có trong danh sách — nó vi phạm E-1 có chủ ý và phạm vi
    dùng hợp lệ bị thu hẹp bằng câu chữ; xem `test_empty_embedding_khong_tuan_thu`.
    """
    return [StubEmbedding("smoke-01")]


@pytest.mark.parametrize("n_texts", [1, 2, 5, 13])
async def test_e1_mot_vector_cho_moi_text(n_texts: int) -> None:
    """E-1 — `len(kết quả) == len(texts)`, với mọi độ dài đầu vào.

    Số 13 cố ý KHÔNG chia hết cho 2 (số vector trong `smoke-01.json`): impl replay
    xoay vòng, nên một bản cài sai kiểu "trả nguyên bộ đã ghi" sẽ lọt ở n=2 và chỉ
    lộ ở một độ dài không chia hết.

    Vỡ E-1 không ném lỗi ở người tiêu thụ — họ ghép bằng `zip(texts, vectors)` và
    `zip` cắt cụt im lặng, nên phần corpus thiếu vector lặng lẽ không được index.
    """
    for impl in _conforming_impls():
        texts = [f"đoạn văn {i}" for i in range(n_texts)]

        result = await impl.embed(texts)

        assert len(result) == n_texts, f"{type(impl).__name__} trả {len(result)} vector cho {n_texts} text"


async def test_e2_moi_vector_cung_be_rong_va_khop_embedding_dim() -> None:
    """E-2 — mọi vector cùng độ dài, và độ dài đó bằng `EMBEDDING_DIM`.

    `kb.chunks.embedding` khai `vector(EMBEDDING_DIM)` + index HNSW cosine; vector
    lệch chiều vỡ trong pgvector lúc `index` (`postgres.py:137-140`) — xa chỗ gây lỗi
    và khó truy. Bắt tại seam thì thông điệp chỉ thẳng vào impl sai.
    """
    for impl in _conforming_impls():
        result = await impl.embed(["a", "b", "c"])

        widths = {len(vector) for vector in result}
        assert widths == {EXPECTED_DIM}, f"{type(impl).__name__} ra bề rộng {widths}, chờ {{{EXPECTED_DIM}}}"


async def test_e3_tat_dinh_trong_mot_tien_trinh() -> None:
    """E-3, nửa RẺ — cùng đầu vào ra cùng vector, không gọi mạng/model (INV-4).

    Embedding nhấp nháy làm `test_interpreter_determinism.py` đỏ ngẫu nhiên và điểm
    smoke-eval hết tái lập — hỏng theo kiểu đổ lỗi nhầm cho chỗ khác.

    Bài này KHÔNG đủ để khoá E-3: hai lượt chạy trong **cùng một** process nên
    `PYTHONHASHSEED` của chúng bằng nhau, và mọi bất định phụ thuộc hash sẽ giống
    nhau ở cả hai lượt ⇒ xanh giả. Nửa còn lại ở
    `test_e3_tat_dinh_qua_hai_tien_trinh_khac_pythonhashseed`.
    """
    for impl in _conforming_impls():
        texts = ["cùng một đầu vào", "lần hai"]

        assert await impl.embed(texts) == await impl.embed(texts)


# Chạy trong tiến trình con: in ra payload tất định của mọi impl phải-tuân-thủ.
# `repr` của `list[float]` là ổn định trong CPython (`float.__repr__` là round-trip
# ngắn nhất), nên so chuỗi ở đây là so giá trị, không phải so định dạng.
_E3_CHILD = textwrap.dedent(
    """
    import asyncio, sys
    from studio_engine.demo_stubs import StubEmbedding

    async def main() -> None:
        impls = [StubEmbedding("smoke-01")]
        texts = ["cùng một đầu vào", "lần hai", "ba"]
        out = [await impl.embed(texts) for impl in impls]
        sys.stdout.write(repr(out))

    asyncio.run(main())
    """
)


def _embed_in_child(hash_seed: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", _E3_CHILD],
        capture_output=True,
        text=True,
        check=True,
        env={**_child_env(), "PYTHONHASHSEED": hash_seed},
    )
    return completed.stdout.strip()


def _child_env() -> dict[str, str]:
    """Env tối thiểu cho tiến trình con: giữ `PYTHONPATH` để nó import được
    `studio_engine` giống hệt tiến trình cha (uv workspace, không cài site-packages)."""
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    return env


def test_e3_tat_dinh_qua_hai_tien_trinh_khac_pythonhashseed() -> None:
    """E-3, nửa BẮT BUỘC — cùng đầu vào ra cùng vector **qua hai tiến trình** với
    `PYTHONHASHSEED` khác nhau.

    Vì sao nửa này tồn tại (finding @dholmes0207, engine#15): bên tiêu thụ đòi mạnh
    hơn E-3 bản cũ. `evalhub/tests/test_determinism.py::test_bang_diem_bat_bien_qua_pythonhashseed`
    chạy đường chấm điểm ở hai process khác `PYTHONHASHSEED` rồi assert cùng một hash
    bảng điểm. Một impl seed theo `PYTHONHASHSEED` / `id()` / thứ tự iterate một `set`
    **thoả E-3 bản cũ nguyên văn** mà vẫn làm bài đó đỏ — và lúc đó evalhub không có
    chỗ nào trong hợp đồng để dựa vào. Python randomize hash của `str` theo từng
    process (PEP 456), nên đây là lớp bất định mà không bài nào chạy-một-process thấy.

    Dùng ba seed chứ không hai: hai seed trùng nhau có thể là trùng may; seed `0` tắt
    hẳn randomization nên nó là mốc đối chiếu khác bản chất với hai seed bật.
    """
    payloads = {seed: _embed_in_child(seed) for seed in ("0", "1", "424242")}

    distinct = set(payloads.values())
    assert len(distinct) == 1, f"embedding đổi theo PYTHONHASHSEED — vi phạm E-3: {payloads}"
    assert distinct.pop().startswith("[["), "tiến trình con không trả về payload vector — bài này đang đo nhầm thứ"


async def test_empty_embedding_khong_tuan_thu_e1() -> None:
    """Ghim **non-conformance đã biết** của `EmptyEmbedding` (contract §4).

    Test này KHÔNG khẳng định hành vi đó đúng — nó biến một cái bẫy ngầm thành một
    sự thật đã ghi. `EmptyEmbedding.embed` trả `[]` bất kể `texts` dài bao nhiêu, mà
    nó lại đang là tham số `embedding=` ở ~30 call-site trong `studio_engine` +
    `studio_workbench`. Hôm nay chưa nổ **chỉ vì** `LlmStepExecutor` chưa từng gọi
    `embed()` (`executors.py:174-175`) — an toàn do chưa ai chạm, không do hợp đồng.

    Phạm vi dùng hợp lệ: chỗ chèn constructor trên đường KHÔNG gọi `embed()`. Nối nó
    vào đường embed thật là lỗi, và thuộc loại tệ nhất — `zip` cắt cụt nên nó biểu
    hiện thành "corpus thiếu chunk", không thành exception.

    Khi A1-3 land (đổi sang fail-loud, PR riêng), test này phải đổi theo — đó là
    chủ ý: nó là cái chuông báo hành vi đã đổi, không phải cái khoá giữ nguyên.
    """
    result = await EmptyEmbedding().embed(["một", "hai", "ba"])

    assert result == [], "EmptyEmbedding đã đổi hành vi — đọc lại contract §4 + A1-3"
    assert len(result) != 3, "vi phạm E-1 là điều đang được ghim, không phải điều mong muốn"


def test_moi_impl_thoa_protocol_o_muc_cau_truc() -> None:
    """`runtime_checkable` chỉ kiểm được đến đây — và đây chính là lý do 3 test trên
    phải tồn tại: `EmptyEmbedding` cũng qua được phép kiểm này dù vi phạm E-1."""
    for impl in _conforming_impls():
        assert isinstance(impl, EmbeddingService)

    assert isinstance(EmptyEmbedding(), EmbeddingService)
