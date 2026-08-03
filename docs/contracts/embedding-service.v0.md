---
id: studio.contract.embedding-service.v0
type: interface-consumption
status: freeze-ready
freeze: FREEZE-READY   # chờ 4/4 chữ ký — workshop #84, D11
freeze_target: D11
contract_ref: umbrella-contract §R-SPEC A1#5 (protocols.py:17)
pen: AIE-1 — Trần Bá Đạt
date: 2026-08-03
---

# 🖊️ EmbeddingService — HỢP ĐỒNG TIÊU THỤ (FREEZE-READY D11)

> ## 🧊 FREEZE-READY (03/08, D11) — khoá **BẤT BIẾN TIÊU THỤ**, không đổi chữ ký.
> Chữ ký `async def embed(texts: list[str]) -> list[list[float]]` đã sống ở
> `studio_contracts.protocols:17` từ P2 và **không đổi hôm nay**. Bản này khoá thứ chữ ký **không
> nói ra được** nhưng mọi bên tiêu thụ đều đang dựa vào — ba bất biến §2. Đổi sau freeze = mini-RFC
> + 4/4 chữ ký + decision-log.

## 0.1 Trạng thái freeze — đã khoá vs còn chờ người

**✅ Đã khoá bằng câu chữ (D11, AIE-1):**
- **§2 ba bất biến tiêu thụ** — cardinality theo vị trí · bề rộng đồng nhất khớp `EMBEDDING_DIM` ·
  tất định trong một lần chạy. Ghim bằng test: `tests/test_embedding_service_contract.py`.
- **§3 hai impl** — `StubEmbedding` (CI, fixture-replay) là impl **duy nhất tuân thủ đủ** hôm nay;
  gateway là impl thứ hai, chưa về, và **không được đổi Protocol** khi về (INV-4).
- **§4 non-conformance đã biết** — `EmptyEmbedding` **vi phạm** cardinality; phạm vi dùng hợp lệ bị
  thu hẹp bằng câu chữ + test ghim.
- **§5 `dim` để MỞ có chủ ý** — bề rộng bị ghim là *bất biến*, **giá trị** thì không, vì bộ đo hiện
  tại không đủ sức phân giải để biện minh cho một con số (design-note §3, §4).

**⏳ Còn chờ người:**
- **4/4 chữ ký** — §7 (để trống, chờ ceremony #84).
- **Nơi đóng dấu `FROZEN`** — theo ADR-D11-01 (SWE đề xuất, #84): hợp đồng **không đổi field/kiểu**
  thì lật cờ tại chỗ, không cần PR vào `contracts`. Bản này không đổi gì trong `contracts` → đi
  đường lật-cờ-tại-chỗ.

## 0.2 Chữ ký freeze (D11) — chờ workshop #84

| Vai | Người | Ký | Ngày |
|---|---|---|---|
| AIE-1 (bút) | Trần Bá Đạt | ⬜ | |
| DE | Nguyễn Đông Anh | ⬜ | |
| SWE | Thiệu Quang Minh | ⬜ | |
| AIE-2 | Lưu Tiến Duy | ⬜ | |

*Không ký khống: ký sau khi đọc §2 + §4.*

**Bút:** AIE-1 · **Neo:** `protocols.py:17` · **Người dùng:** AIE-1 (`llm-step`), DE (`KbPipeline.index`), SWE (nối ở composition root).

---

## 1. Chữ ký (KHÔNG đổi hôm nay)

```python
@runtime_checkable
class EmbeddingService(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

Nguồn duy nhất: `packages/contracts/src/studio_contracts/protocols.py:17`. Engine **không** khai lại.

## 2. Ba bất biến tiêu thụ — thứ chữ ký không nói ra được

`runtime_checkable` chỉ kiểm **có method tên `embed`**, không kiểm gì về giá trị trả. Ba điều dưới
đây mọi bên tiêu thụ đều đang dựa vào; không viết ra thì chúng là giả định ngầm, và giả định ngầm là
thứ vỡ im lặng.

| # | Bất biến | Vỡ thì hỏng thế nào |
|---|---|---|
| **E-1** | **Cardinality theo vị trí** — `len(kết quả) == len(texts)`, vector thứ `i` thuộc `texts[i]`. | Người tiêu thụ ghép bằng `zip(texts, vectors)`; `zip` **cắt cụt im lặng**, không ném lỗi. Trả thiếu vector = một phần corpus lặng lẽ không được index, và không có dòng log nào nói ra. |
| **E-2** | **Bề rộng đồng nhất** — mọi vector cùng độ dài, và độ dài đó **bằng `EMBEDDING_DIM`** (`packages/kb/src/studio_kb/schema.py:33`, hiện `8`). | `kb.chunks.embedding` khai `vector(EMBEDDING_DIM)` + index HNSW cosine. Lệch chiều thì vỡ sâu trong pgvector lúc `index` (`postgres.py:137-140`), xa chỗ gây lỗi. |
| **E-3** | **Tất định trong một lần chạy** — cùng đầu vào ra cùng vector; CI **không gọi mạng/model** (INV-4 fixtures-first). | Embedding nhấp nháy làm mọi test determinism (`test_interpreter_determinism.py`) đỏ ngẫu nhiên, và điểm smoke-eval hết tái lập. |

**E-2 là bất biến LIÊN-PACKAGE nhưng KHÔNG import được.** `.importlinter` cấm `studio_engine` chạm
`studio_kb`, nên `StubEmbedding` đang **khoá cứng số 8** (`demo_stubs.py:173-176` ghi rõ lý do). Đây
là nợ có ý thức, không phải sơ suất: hai hằng số ở hai package phải **đổi cùng lúc bằng tay**. Ai đổi
`EMBEDDING_DIM` mà quên bên engine thì `test_embedding_service_contract.py` đỏ ở một dòng assert, chứ
không vỡ trong pgvector.

## 3. Hai impl (R-SPEC A1#5) — trạng thái thật

| Impl | Ở đâu | E-1 | E-2 | E-3 | Ghi chú |
|---|---|---|---|---|---|
| `StubEmbedding` | `demo_stubs.py:162` | ✅ (xoay vòng đủ `len(texts)`) | ✅ (8) | ✅ (replay file) | impl CI, **tuân thủ đủ** |
| gateway | chưa có | — | — | — | impl thứ 2; về thì **không được đổi Protocol** (INV-4) |
| `EmptyEmbedding` | `demo_stubs.py:149` | ❌ **vi phạm** | n/a | ✅ | xem §4 — KHÔNG phải impl, là chỗ chèn DI |

## 4. Non-conformance đã biết — `EmptyEmbedding`

`EmptyEmbedding.embed()` trả `[]` **bất kể `texts` dài bao nhiêu** → vi phạm E-1. Nó thoả
`runtime_checkable` và đang được dùng làm tham số `embedding=` ở **~30 chỗ** trong `studio_engine` và
`studio_workbench`.

**Vì sao hôm nay chưa nổ:** `LlmStepExecutor` nhận `embedding` qua constructor nhưng **chưa từng gọi**
`embed()` (`executors.py:170-171` ghi rõ). Tức nó an toàn **do chưa ai chạm tới**, không do hợp đồng.

**Phạm vi dùng hợp lệ (khoá bằng câu chữ hôm nay):** `EmptyEmbedding` chỉ được dùng làm **chỗ chèn
constructor ở đường không gọi `embed()`**. Nối nó vào bất kỳ đường nào thật sự embed là **lỗi**, và
là lỗi thuộc loại tệ nhất — `zip` cắt cụt nên nó biểu hiện thành "corpus thiếu chunk" chứ không thành
exception.

**Việc phải làm (không làm hôm nay, có chủ ý):** đổi `EmptyEmbedding.embed` thành **ném** thay vì trả
`[]` — cùng tinh thần fail-loud mà `FixtureError` đã chọn (`demo_stubs.py:39-44`: *"Fail-closed, never
fall back"*). Không làm trong ngày freeze vì đó là đổi hành vi của một double đang dùng ở ~30 call-site
trên 2 package; nó cần một PR riêng có test đi kèm, không phải một dòng sửa kèm theo ngày đóng băng
hợp đồng. Đã ghi decision-log **DL-11.A1-3**.

## 5. `dim`: ghim BẤT BIẾN, mở GIÁ TRỊ

Hợp đồng ghim "mọi vector cùng bề rộng và khớp `EMBEDDING_DIM`" (E-2), **không** ghim con số nên là
bao nhiêu khi gateway về. Lý do có số đo: lưới 4 cách cắt × 6 số chiều trên corpus + golden thật cho
recall@1 **không đổi (6/6) từ dim=8 tới dim=256**, và null-control cho thấy một **vector hằng số mang
0 bit thông tin cũng đạt 6/6** (design-note §3). Thước đo hiện tại không phân giải nổi — chọn một con
số từ nó là đọc nhiễu thành tín hiệu. Ghim lại khi bộ golden có case thật sự tranh chấp.

## 6. Tiêu thụ `kb.search` — phần AIE-1 xác nhận

Chữ ký `kb.search(query, tenant_id: UUID, section_roles, top_k)` (DE giữ bút,
`packages/kb/docs/contracts/kb-search.v0.md`) — AIE-1 **xác nhận tiêu thụ đúng bản này**, gọi tại
`executors.py:150`, không đổi gì. Ba điểm AIE-1 cam kết ở phía executor:

1. **`tenant_id` không bao giờ do executor tự dựng** — không phải `UUID` thật từ `session_context` thì
   `PermissionError` (`executors.py:141-148`), không có sentinel thay thế.
2. **`section_roles` truyền nguyên** — executor không nới, không suy lại phía client (fence-EXECUTOR).
3. **Không truy xuất rộng rồi nhờ LLM lọc** — đúng luật §5.3 bản kb.search.

## 7. Câu chặn / hoãn-có-ghi

| # | Với ai | Nội dung | Trạng thái |
|---|---|---|---|
| **A1-1** | **DE** | `trace-event.v0.md:121` mô tả `_WALK_ORDER` như cơ chế hiện hành; hằng số đã bỏ từ D6, walk suy từ `recipe.dag.edges` (`interpreter.py:9`). Chính `trace_reader.py:44-49` của DE đã ghi đúng. | 🔴 **CHẶN chữ ký AIE-1** — sửa câu chữ rồi ký |
| A1-2 | DE | Q-3 cost một-nguồn: executor chỉ cấp `tokens`, sink tính `cost` | ✅ **AIE-1 đồng ý** — code đã đúng (`executors.py:258`, `interpreter.py:320`) |
| A1-3 | AIE-1 | `EmptyEmbedding` fail-loud thay vì trả `[]` (§4) | 🟡 hoãn-có-ghi — PR riêng, không làm ngày freeze |
| A1-4 | AIE-2 | Bộ golden không phân biệt được embedding thật với vector hằng số (design-note §3) → `citation_accuracy` chưa bắt được hồi quy embedding | 🟠 **cần AIE-2 biết trước khi ký scorecard** |
| A1-5 | DE | Q-D stub `kb.search`: AIE-1 **tự dựng bên engine** | ✅ đóng — `.importlinter` cấm engine import kb |
| **A1-6** | **SWE** | `recipe.v0.md` (workbench#12) chốt *"recipe không qua validator = không interpret"* và gọi `graph_lint()` là *"cổng duy nhất giữa người dùng khai báo và engine thực thi"*. Nhưng cổng đó **chưa nối vào gì**: `graph_lint` có **0 caller thật** trong workspace — chỗ duy nhất gọi nó là `publish.py`, mà `publish()` tự nó vẫn là stub `raise NotImplementedError` (`publish.py:43`). `interpreter.run()` cũng không gọi. Tức recipe đi thẳng builder → interpreter mà không chạm cổng nào. | 🟠 **cần SWE xác nhận trước khi ký `recipe`** — thân 4 luật là thật và đúng; vấn đề là **điểm nối**, không phải nội dung |
| A1-7 | SWE + AIE-1 | 4 điều kiện tiền đề của `interpreter.run()` **chặt hơn** 4 luật `graph_lint`: (2) ≤1 cạnh ra và (4) phải kết thúc ĐÚNG trên node `end` không có bản đối ứng phía lint. Recipe qua lint vẫn có thể bị `run()` từ chối. | 🟡 hoãn-có-ghi — khoảng vênh có chủ ý giữa **hai** hợp đồng đã công bố, khép lại là việc cross-lane |

---

## 8. Nhật ký sửa đổi

| Bản | Ngày | Nội dung |
|---|---|---|
| **freeze-ready** | 2026-08-03 (D11, #81) | Bản đầu. **Không đổi chữ ký** (`protocols.py:17` giữ nguyên) — khoá **bất biến tiêu thụ** E-1/E-2/E-3 + ghim bằng `tests/test_embedding_service_contract.py`; ghi non-conformance `EmptyEmbedding` (§4) và thu hẹp phạm vi dùng bằng câu chữ; để **mở giá trị `dim`** có lý do đo được (§5); xác nhận tiêu thụ `kb.search` (§6). `FROZEN` + 4/4 chữ ký **chưa đóng** — chờ ceremony + A1-1 |
