"""Đo trade-off chunking × embedding cho seam `EmbeddingService` (AIE-1, D11 #81).

Issue #81 đòi "chunking×embedding trade-off **measurement**" — đo, không phải lập luận.
Script này là phần "đo": quét lưới (độ mịn chunk) × (số chiều embedding) trên **corpus
Callisto thật** + **golden-set thật**, và in ra 4 con số cho từng ô:

- `collision`  — tỉ lệ token phân biệt bị băm trùng ô. Đây là giới hạn thông tin cứng
                 của bag-of-words: 512 token nhồi vào 8 ô thì 98.4% token chia ô với
                 token khác, và không phép xếp hạng nào lấy lại được phần đã mất.
- `margin`     — cos(query, chunk đúng) − cos(query, chunk sai tốt nhất), đo **BÊN TRONG
                 tập đã qua fence** (đúng tenant + đúng vai). Đo ngoài fence là đo nhầm:
                 fence là tầng quyết định trước, ranking chỉ tranh phần còn lại.
- `tranh`      — số case mà tập sau fence còn >1 ứng viên, tức ranking THẬT SỰ quyết định
                 điều gì đó. Case còn đúng 1 ứng viên thì embedding có tốt hay tệ cũng ra
                 cùng kết quả — đếm nó vào điểm là tự lừa.
- `mất`        — số case mà chunk chứa đáp án bị chính fence loại (ranking không cứu được).

Kèm **null control** (`--null`): chấm lại bằng vector hằng số — một embedding mang 0 bit
thông tin. Nếu điểm không tụt, bộ golden không đo được chất lượng embedding, và mọi con số
recall/citation-accuracy dựng trên nó là điểm rỗng. Đây là phép thử rẻ nhất để biết thước đo
có đo gì không, và kết quả D11 là **không** (xem `docs/design-notes/aie1-day11.md` §3).

Vì sao script nằm ở `scripts/` chứ không ở `src/studio_engine/`
---------------------------------------------------------------
Nó import `studio_kb` (corpus + máy cắt của DE), mà `.importlinter` CẤM `studio_engine`
chạm `studio_kb`. Ràng buộc đó áp cho **package** `studio_engine` (`src/studio_engine/`);
`scripts/` không nằm trong package nên không bị quét — đã kiểm bằng `lint-imports` (exit 0),
không phải suy đoán. Đây là công cụ đo, không phải đường chạy production; giữ nó ngoài
package là cách nói rõ "đây không phải code chạy thật".

Chạy lại
--------
    uv run python packages/engine/scripts/measure_chunk_embed.py
    uv run python packages/engine/scripts/measure_chunk_embed.py --null

Không mạng, không model, không Postgres. Cùng công thức dẫn xuất với
`studio_kb.embeddings.derive_vector` (blake2b bag-of-words, chuẩn hoá L2) để số ở đây so
được trực tiếp với bảng cosine D7 của DE trong docstring module đó.
"""

from __future__ import annotations

import hashlib
import math
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_KB = _ROOT / "packages" / "kb"
sys.path.insert(0, str(_ROOT / "packages" / "contracts" / "src"))
sys.path.insert(0, str(_KB / "src"))

import yaml  # type: ignore[import-untyped]  # noqa: E402  — `types-PyYAML` không có trong lock; thêm một dep chỉ để chú kiểu cho 1 script đo là không đáng
from studio_kb.doc_factory import chunk_document  # noqa: E402

GOLDEN = "smoke-10.yaml"
DIMS = (8, 16, 32, 64, 128, 256)

Row = tuple[str, str, str, str]
"""Một hàng đem chấm: `(chunk_id, text, tenant_slug, section_role)`.

Là tuple chứ không phải `Chunk`: bản `whole-doc (gộp vai)` cố ý phát cùng `chunk_id` dưới
nhiều vai, thứ `Chunk` (1 chunk = 1 vai) không biểu diễn được — mà chính khả năng biểu diễn
đó là cái làm lộ ra thế lưỡng nan rò-vs-mất."""

Cutter = Callable[[str], list[Row]]
Embedder = Callable[[str], list[float]]


def derive(text: str, dim: int) -> list[float]:
    """`studio_kb.embeddings.derive_vector` với `dim` là tham số tự do.

    Chép công thức (không import) là **có chủ ý**: hàm gốc khoá cứng `EMBEDDING_DIM`, mà
    cả điểm của bài đo này là quét nhiều `dim`. Công thức phải khớp nguyên văn bản gốc —
    lệch một chi tiết thì số đo ở đây không so được với bảng D7 của DE.
    """
    buckets = [0.0] * dim
    for token in text.lower().split():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=2).digest()
        buckets[int.from_bytes(digest, "big") % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in buckets))
    if norm == 0.0:
        return [1.0] + [0.0] * (dim - 1)
    return [x / norm for x in buckets]


def cos(a: list[float], b: list[float]) -> float:
    """Tích vô hướng = cosine vì cả hai vector đã chuẩn hoá L2 ở `derive`."""
    return sum(x * y for x, y in zip(a, b, strict=True))


def _slug(chunk_id: str) -> str:
    """Slug tenant là tiền tố `chunk_id` (`ankor-leave-001#c1` → `ankor`).

    `Chunk.tenant_id` là UUID (D-13) nên không đọc slug từ đó được; slug chỉ còn sống
    trong `chunk_id` làm nhãn hiển thị, đúng chủ ý D-13.
    """
    return chunk_id.split("-")[0]


# ─────────────────────────────────────────────────────── 4 cách cắt đem ra so


def chunks_heading(raw: str) -> list[Row]:
    """Bản đang chạy: 1 chunk = 1 heading `##` + thân (callisto-doc-schema §5)."""
    return [(c.chunk_id, c.text, _slug(c.chunk_id), c.section_role) for c in chunk_document(raw)]


def chunks_paragraph(raw: str) -> list[Row]:
    """Mịn hơn: cắt tiếp mỗi chunk heading theo dòng trống."""
    out: list[Row] = []
    for c in chunk_document(raw):
        parts = [p.strip() for p in re.split(r"\n\s*\n", c.text) if p.strip()]
        for i, part in enumerate(parts, start=1):
            out.append((f"{c.chunk_id}p{i}", part, _slug(c.chunk_id), c.section_role))
    return out


def chunks_doc_strict(raw: str) -> list[Row]:
    """Thô hơn, giữ fence: cả doc là 1 chunk, mang vai của chunk ĐẦU.

    Doc có heading override `{section: X}` thì phần thân đó bị xếp sai vai → đáp án
    thành **không với tới được**. Đây là nửa "mất dữ liệu" của thế lưỡng nan whole-doc.
    """
    cs = chunk_document(raw)
    if not cs:
        return []
    joined = "\n\n".join(c.text for c in cs)
    return [(cs[0].chunk_id.split("#")[0] + "#whole", joined, _slug(cs[0].chunk_id), cs[0].section_role)]


def chunks_doc_union(raw: str) -> list[Row]:
    """Thô hơn, VỠ fence: cả doc là 1 chunk, hiện dưới HỢP của mọi vai trong doc.

    Không mất gì — đổi lại phần thân `finance` của doc giờ phục vụ cho query phạm vi
    `public`. Phát mỗi vai một hàng để đếm được rò. Đây là nửa "rò" của thế lưỡng nan.
    """
    cs = chunk_document(raw)
    if not cs:
        return []
    joined = "\n\n".join(c.text for c in cs)
    cid = cs[0].chunk_id.split("#")[0] + "#whole"
    return [(cid, joined, _slug(cs[0].chunk_id), role) for role in sorted({c.section_role for c in cs})]


GRANULARITY: dict[str, Cutter] = {
    "paragraph": chunks_paragraph,
    "heading (đang chạy)": chunks_heading,
    "whole-doc (giữ vai đầu)": chunks_doc_strict,
    "whole-doc (gộp vai)": chunks_doc_union,
}


def load_corpus(cutter: Cutter) -> list[Row]:
    rows: list[Row] = []
    for path in sorted((_KB / "docs" / "callisto").glob("*.md")):
        rows.extend(cutter(path.read_text(encoding="utf-8")))
    return rows


def load_cases(name: str = GOLDEN) -> list[dict[str, Any]]:
    """Chỉ lấy case DƯƠNG (có `expected_citation`). Case từ chối chấm nhánh khác."""
    raw: dict[str, Any] = yaml.safe_load((_KB / "golden" / name).read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = raw["cases"]
    return [c for c in cases if c.get("expected_citation")]


def count_leak(rows: Sequence[Row]) -> int:
    """Đếm hàng `(chunk, vai)` phục vụ text thật ra thuộc vai KHÁC.

    Chuẩn đối chiếu là bản cắt heading đang chạy — nơi luật "1 chunk = 1 section_role"
    (callisto-doc-schema §5) còn đúng. Một hàng rò khi text của nó bọc trọn một section
    mang vai khác vai đang gắn: query đúng phạm vi vẫn đọc được thứ không thuộc về mình.
    """
    truth = [
        (c.text.strip(), c.section_role)
        for path in sorted((_KB / "docs" / "callisto").glob("*.md"))
        for c in chunk_document(path.read_text(encoding="utf-8"))
    ]
    return sum(
        any(sec_text in text and sec_role != role for sec_text, sec_role in truth) for _cid, text, _ten, role in rows
    )


def score(rows: Sequence[Row], cases: Sequence[dict[str, Any]], embed: Embedder) -> tuple[list[float], int, int, int]:
    """Chấm một (cách cắt × hàm embed). Trả `(margins, hits, tranh, mất)`.

    Khoá theo CHỈ SỐ HÀNG, không theo `chunk_id`: bản `whole-doc (gộp vai)` cố ý phát
    cùng `chunk_id` nhiều lần (mỗi vai một hàng), dict khoá theo `chunk_id` sẽ nuốt mất
    tất cả trừ vai cuối — và bảng số sẽ nói dối là bản gộp cũng mất case như bản strict.
    """
    vecs = {i: embed(text) for i, (_c, text, _t, _r) in enumerate(rows)}
    meta = {i: (cid, ten, role) for i, (cid, _t, ten, role) in enumerate(rows)}

    margins: list[float] = []
    hits = contested = unreachable = 0
    for case in cases:
        qv = embed(case["query"])
        want = str(case["expected_citation"][0]).split("#")[0]
        cand = [i for i in vecs if meta[i][1] == case["tenant"] and meta[i][2] == case["expected_section_role"]]
        correct = [i for i in cand if meta[i][0].startswith(want)]
        if not correct:
            unreachable += 1  # fence đã loại đáp án — ranking không cứu được
            continue
        distractors = [i for i in cand if not meta[i][0].startswith(want)]
        if not distractors:
            hits += 1  # fence để lại đúng 1 ứng viên: ranking KHÔNG quyết định gì
            continue
        contested += 1
        margins.append(max(cos(qv, vecs[i]) for i in correct) - max(cos(qv, vecs[i]) for i in distractors))
        top = max(cand, key=lambda i: cos(qv, vecs[i]))
        if meta[top][0].startswith(want):
            hits += 1
    return margins, hits, contested, unreachable


def _embedder(dim: int) -> Embedder:
    """Đóng gói `dim` lại thành hàm 1 tham số cho `score`."""
    return lambda text: derive(text, dim)


def sweep(cases: Sequence[dict[str, Any]]) -> None:
    print(f"corpus: packages/kb/docs/callisto/ · golden: {GOLDEN} · {len(cases)} case dương\n")
    for gname, cutter in GRANULARITY.items():
        rows = load_corpus(cutter)
        tokens = {t for _c, text, _t, _r in rows for t in text.lower().split()}
        print(
            f"=== cắt = {gname} · {len(rows)} hàng · {len(tokens)} token phân biệt "
            f"· RÒ VAI: {count_leak(rows)} hàng ==="
        )
        print(
            f"{'dim':>5} {'collision':>10} {'margin tb':>11} {'margin min':>11} {'recall@1':>9} {'tranh':>6} {'mất':>5}"
        )
        for dim in DIMS:
            used = {int.from_bytes(hashlib.blake2b(t.encode(), digest_size=2).digest(), "big") % dim for t in tokens}
            collision = 1.0 - len(used) / len(tokens)
            margins, hits, contested, unreachable = score(rows, cases, _embedder(dim))
            avg = f"{sum(margins) / len(margins):.3f}" if margins else "n/a"
            worst = f"{min(margins):.3f}" if margins else "n/a"
            print(
                f"{dim:>5} {collision:>9.1%} {avg:>11} {worst:>11} "
                f"{f'{hits}/{len(cases)}':>9} {contested:>6} {unreachable:>5}"
            )
        print()


def null_control(cases: Sequence[dict[str, Any]]) -> None:
    """Thước đo có đo gì không? Chấm lại bằng embedding mang 0 bit thông tin.

    Điểm không tụt = bộ golden không phân biệt được embedding thật với embedding rỗng,
    nên mọi kết luận "embedding đủ tốt" rút ra từ nó là kết luận rỗng.
    """
    rows = load_corpus(chunks_heading)
    print("=== NULL CONTROL (cắt heading, golden như trên) ===")
    print(f"{'embedding':<44} {'recall@1':>9} {'tranh':>6} {'top1 không hoà':>16}")

    def _constant(_text: str) -> list[float]:
        return [1.0] + [0.0] * 7

    def _sentence_hash(text: str) -> list[float]:
        return [b / 255 for b in hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()]

    variants: dict[str, Embedder] = {
        "bag-of-words dim=8 (đang chạy)": _embedder(8),
        "bag-of-words dim=256": _embedder(256),
        "NULL: vector hằng số (0 thông tin)": _constant,
        "NULL: băm cả câu (không cấu trúc cosine)": _sentence_hash,
    }
    for label, embed in variants.items():
        vecs = {i: embed(text) for i, (_c, text, _t, _r) in enumerate(rows)}
        meta = {i: (cid, ten, role) for i, (cid, _t, ten, role) in enumerate(rows)}
        hits = uniq = contested = 0
        for case in cases:
            qv = embed(case["query"])
            want = str(case["expected_citation"][0]).split("#")[0]
            cand = [i for i in vecs if meta[i][1] == case["tenant"] and meta[i][2] == case["expected_section_role"]]
            correct = [i for i in cand if meta[i][0].startswith(want)]
            if not correct:
                continue
            if len(cand) == len(correct):
                hits += 1
                continue
            contested += 1
            ranked = sorted(((cos(qv, vecs[i]), i) for i in cand), reverse=True)
            if meta[ranked[0][1]][0].startswith(want):
                hits += 1
                # thắng nhờ điểm cao thật, hay chỉ nhờ thứ tự sort khi hoà?
                if len(ranked) < 2 or abs(ranked[1][0] - ranked[0][0]) > 1e-12:
                    uniq += 1
        print(f"{label:<44} {f'{hits}/{len(cases)}':>9} {contested:>6} {uniq:>16}")


def main() -> None:
    cases = load_cases()
    if "--null" in sys.argv:
        null_control(cases)
    else:
        sweep(cases)
        print()
        null_control(cases)


if __name__ == "__main__":
    main()
