# D14 — Seam `EmbeddingService` cắm-được + bảng grid chunking × embedding

AIE-1, D14 #96, phase-3 (`plans/260806-0938-d14-aie1-node-executors-grid-prep/phases/phase-3-embedding-grid-harness.md`).

## Tóm tắt

`measure_chunk_embed.py` (D11 #81) trước phase này tự chép công thức `derive()` thay vì đi
qua Protocol `EmbeddingService` (`packages/contracts/src/studio_contracts/protocols.py:23`), và
khoá cứng golden = `smoke-10.yaml`. Phase này tách phần seam thuần-stdlib (`as_embedder` +
`load_cases`) ra `embed_harness.py`, cho golden trở thành tham số, và thêm chế độ `--grid`
chấm thật qua registry `SERVICES` gồm 3 `EmbeddingService` — 2 impl thật + 1 đối chứng.

## Lệnh tái lập

```
uv run python packages/engine/scripts/measure_chunk_embed.py --grid
uv run python packages/engine/scripts/measure_chunk_embed.py --golden packages/kb/golden/smoke-5.yaml --grid
```

Corpus: `packages/kb/docs/callisto/` (42 doc). Golden mặc định: `packages/kb/golden/smoke-10.yaml`
(6 case dương). Submodule `packages/kb` tại SHA `51df3a4` lúc chạy các số dưới đây
(2026-08-06). Không mạng, không model, không Postgres.

## Bảng grid thật — `--grid` (golden mặc định, 6 case dương)

| cắt | impl | recall@1 | tranh | mất | margin tb |
|---|---|---|---|---|---|
| paragraph (330 hàng) | kb.derive_vector (dim=8, bản đang chạy) | 3/6 | 6 | 0 | 0.004 |
| paragraph (330 hàng) | bag-of-words dim=256 (biến thể bề rộng) | 6/6 | 6 | 0 | 0.097 |
| paragraph (330 hàng) | CONTROL: vector hằng số (0 bit thông tin) | 0/6 | 6 | 0 | 0.000 |
| heading — đang chạy (140 hàng) | kb.derive_vector (dim=8, bản đang chạy) | 3/6 | 6 | 0 | 0.014 |
| heading — đang chạy (140 hàng) | bag-of-words dim=256 (biến thể bề rộng) | 6/6 | 6 | 0 | 0.114 |
| heading — đang chạy (140 hàng) | CONTROL: vector hằng số (0 bit thông tin) | 0/6 | 6 | 0 | 0.000 |
| whole-doc, giữ vai đầu (42 hàng) | kb.derive_vector (dim=8, bản đang chạy) | 1/6 | 5 | 1 | -0.023 |
| whole-doc, giữ vai đầu (42 hàng) | bag-of-words dim=256 (biến thể bề rộng) | 5/6 | 5 | 1 | 0.098 |
| whole-doc, giữ vai đầu (42 hàng) | CONTROL: vector hằng số (0 bit thông tin) | 0/6 | 5 | 1 | 0.000 |
| whole-doc, gộp vai (47 hàng) | kb.derive_vector (dim=8, bản đang chạy) | 2/6 | 6 | 0 | -0.007 |
| whole-doc, gộp vai (47 hàng) | bag-of-words dim=256 (biến thể bề rộng) | 6/6 | 6 | 0 | 0.069 |
| whole-doc, gộp vai (47 hàng) | CONTROL: vector hằng số (0 bit thông tin) | 0/6 | 6 | 0 | 0.000 |

Chạy `--grid` toàn bộ (4 cắt × 2 impl thật + 1 control = 12 hàng, ~583 lượt gọi
`as_embedder` mỗi impl qua `asyncio.run`) mất **2.35s** trên máy đo — dưới ngưỡng lo ngại
60s ở P3-R2 của phase, không cần gom batch.

## Tham số hoá golden — `--golden packages/kb/golden/smoke-5.yaml --grid` (3 case dương)

| cắt | impl | recall@1 | tranh | mất | margin tb |
|---|---|---|---|---|---|
| paragraph (330 hàng) | kb.derive_vector (dim=8) | 1/3 | 3 | 0 | 0.002 |
| paragraph (330 hàng) | bag-of-words dim=256 | 3/3 | 3 | 0 | 0.124 |
| heading — đang chạy (140 hàng) | kb.derive_vector (dim=8) | 2/3 | 3 | 0 | 0.006 |
| heading — đang chạy (140 hàng) | bag-of-words dim=256 | 3/3 | 3 | 0 | 0.139 |
| whole-doc, giữ vai đầu (42 hàng) | kb.derive_vector (dim=8) | 0/3 | 2 | 1 | -0.057 |
| whole-doc, giữ vai đầu (42 hàng) | bag-of-words dim=256 | 2/3 | 2 | 1 | 0.110 |
| whole-doc, gộp vai (47 hàng) | kb.derive_vector (dim=8) | 1/3 | 3 | 0 | -0.014 |
| whole-doc, gộp vai (47 hàng) | bag-of-words dim=256 | 3/3 | 3 | 0 | 0.074 |

Số đổi theo golden (3 case thay vì 6, tỉ lệ recall khác) — chứng minh golden đã trở thành
tham số thật (`--golden <tên|path>`), không còn khoá cứng `smoke-10.yaml`. Con đường dẫn
tường minh (`packages/kb/golden/smoke-5.yaml`) và tên bare (`smoke-5.yaml`, resolve dưới
`packages/kb/golden/`) đều chạy được — đã kiểm cả hai bằng tay.

## Kết luận

- `bag-of-words dim=256` (biến thể bề rộng của impl đang chạy) áp sát trần recall@1 ở cả 4
  cách cắt trên cả 2 golden (5/6–6/6 hoặc 2/3–3/3), so với `kb.derive_vector (dim=8)` thấp
  hơn rõ rệt (1/6–3/6 hoặc 0/3–2/3) — khớp bảng sweep D11 gốc (dim càng lớn, collision
  càng thấp, recall càng cao), không phải phát hiện mới.
- Cả 2 impl **cùng họ bag-of-words** (blake2b hash-bucket, chuẩn hoá L2) — số trên đo được
  "rộng hơn giúp gì trong CÙNG họ thuật toán", không đo được "impl khác họ (gateway/model)
  có tốt hơn không". Impl gateway/model không ship trong kit này
  (`docs/system-architecture.md` §6, bảng `EmbeddingService`); impl thứ hai thật sự khác
  họ của DE là follow-up #95, chưa land ở thời điểm đo.

## Giới hạn

1. **1 corpus, 6 case dương** (golden mặc định `smoke-10.yaml`) — corpus Callisto (42 doc)
   và golden-set do DE gán tay, không có corpus/golden thứ hai để chéo kiểm; số trên chỉ
   nói về TẬP DỮ LIỆU này, không tổng quát hoá được.
2. **2 impl đăng ký trong `SERVICES` (`kb-derive`, `bow-256`) đều thuộc CÙNG họ
   bag-of-words** (khác bề rộng vector, không khác thuật toán). Impl gateway/model —
   loại thứ ba đủ khác để so sánh "họ thuật toán khác nhau" — không ship trong kit này
   (`docs/system-architecture.md` §6). Đọc bảng số trên như "rộng hơn trong họ BoW giúp
   được bao nhiêu", không phải "embedding thật (model) có đủ tốt không".
3. **Rationale null-control của DEC-2 (`docs/decisions.md:17-27`) không còn tái lập.**
   DEC-2 đo trên corpus **5 doc** (2026-08-03) và ghi nhận null-control cũng đạt 6/6 —
   tức bộ golden thời điểm đó KHÔNG phân biệt được embedding thật với embedding rỗng.
   Corpus đã lên **42 doc** ngày 2026-08-04 (`cdc3bb0`,
   `git -C packages/kb log -- docs/callisto/`), và bảng `--grid` ở trên đo được
   `CONTROL: vector hằng số` = **0/6** ở mọi cách cắt — khác hẳn 6/6 DEC-2 từng ghi. Đây
   là **sự thật đo được ở corpus 42 doc hiện tại**, không phải sửa lại phát biểu của
   DEC-2 (DEC-2 vẫn đúng cho corpus 5 doc tại thời điểm nó được ghi) — quyết định có cần
   một DEC mới ghi lại kết luận ở corpus 42 doc hay không thuộc phạm vi P4, phase này chỉ
   ghi nhận số đo, không tự sửa DEC-2.

## Ghi chú cơ chế test (thay cho `conftest.py`)

Kế hoạch ban đầu của phase-3 dùng `packages/engine/tests/conftest.py` để chèn `scripts/`
vào `sys.path` cho `test_embed_harness.py`. Khi thực thi, phát hiện `apps/studio/tests/`
đã có sẵn một `conftest.py` khác (từ D13, không liên quan phase này, commit `372908f`
2026-08-05) — hai `conftest.py` cùng basename ở hai `tests/` khác nhau, không thư mục nào
có `__init__.py`, làm `uv run mypy packages apps` nổ `Duplicate module named "conftest"`.
Thêm `__init__.py` để tách namespace lại phá 10 file test khác trong
`packages/engine/tests/` đang import chéo phẳng (`from test_xxx import ...`), việc phase
này không được phép làm ("không đụng ... mọi test khác").

Xử lý theo đúng fallback mà chính rủi ro **P3-R1** của phase đã cho phép trước ("test import
module bằng `importlib.util.spec_from_file_location`"): xoá `conftest.py`,
`test_embed_harness.py` tự nạp `embed_harness.py` (và `measure_chunk_embed.py`, lazy, sau
`pytest.importorskip`) qua `importlib.util.spec_from_file_location` +
`module_from_spec` + đăng ký `sys.modules[<tên>]` trước `exec_module` (cần thiết vì
`measure_chunk_embed.py` tự nó viết `from embed_harness import ...`, và
`@dataclass`/`from __future__ import annotations` cần `sys.modules[cls.__module__]` có
thật để suy giải kiểu). Kết quả: `uv run mypy packages apps` → `Success: no issues found`,
`uv run pytest packages/engine/tests -q` → 130 passed, không cần `conftest.py`.
