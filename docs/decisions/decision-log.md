---
id: studio.decision-log.engine
type: decision-log
owner: AIE-1 — Trần Bá Đạt
scope: agentcore-studio-engine (seam AIE-1 cầm: EmbeddingService · tiêu thụ kb.search · 6 node executor)
started: 2026-08-03
canonical_location: file này (nội dung thật) + link từ index kit/docs/decisions/ — ADR-D11-01
---

# Decision-log — engine (AIE-1)

> **Vị trí canon — đã rõ (cập nhật 03/08 chiều).** Nhánh `origin/docs/d11-shared-decision-log` trong
> `kit` dựng `docs/decisions/` làm **index theo hợp đồng** (1 file / 1 hợp đồng, không tách theo
> người), với nguyên tắc: *"nội dung thật luôn nằm trong repo của người giữ bút, kit chỉ giữ link —
> tránh 2 bản dễ lệch."* → **file này đang đúng chỗ**, không phải bản tạm. Việc còn lại chỉ là thêm
> một dòng link trỏ về đây trong index của kit.
>
> ⚠️ Index hiện liệt kê **4 hợp đồng** (recipe · trace-event · kb.search · scorecard) và **không có
> dòng cho engine** — vì D11 đóng khung AIE-1 là *bên tiêu thụ*, không giữ bút hợp đồng nào trong 4.
> `embedding-service.v0.md` là artifact thứ 5 do AIE-1 tự viết; cần hỏi nhóm có đưa vào index không.
>
> Luật freeze: umbrella §3 · **D-12** · **INV-5**. Đổi contract sau freeze = mini-RFC + 4/4 chữ ký
> + decision-log.

## D11 · 2026-08-03 · Contract-freeze workshop (#84 / #81)

| # | Quyết | Lý do | Trạng thái |
|---|---|---|---|
| **DL-11.A1-1** | **Khoá 3 bất biến tiêu thụ `EmbeddingService`** — E-1 cardinality theo vị trí · E-2 bề rộng đồng nhất khớp `EMBEDDING_DIM` · E-3 tất định trong một lần chạy. Ghim bằng `tests/test_embedding_service_contract.py`. | `runtime_checkable` chỉ kiểm *có method `embed`*, không kiểm gì về giá trị trả. Cả 3 đều vỡ **im lặng**: E-1 bị `zip` cắt cụt, E-2 vỡ sâu trong pgvector, E-3 làm test determinism đỏ ngẫu nhiên. | ✅ chốt (AIE-1) — 8 test xanh |
| **DL-11.A1-2** | **KHÔNG ghim giá trị `dim`; chỉ ghim bất biến bề rộng.** | Đo được: recall@1 **không đổi 6/6 từ dim=8 tới dim=256**, và null-control cho thấy **vector hằng số (0 bit thông tin) cũng 6/6**. Thước đo không phân giải nổi → chọn số từ nó là đọc nhiễu thành tín hiệu. `scripts/measure_chunk_embed.py` tái lập. | ✅ chốt (AIE-1) |
| **DL-11.A1-3** | **`EmptyEmbedding` giữ nguyên hôm nay**, thu hẹp phạm vi dùng bằng câu chữ + test ghim; đổi sang **fail-loud** ở PR riêng. | Nó vi phạm E-1 (`[]` bất kể `texts`) và đang là `embedding=` ở ~30 call-site trên 2 package. Đổi hành vi một double dùng rộng như vậy cần PR có test đi kèm — không ghép vào ngày đóng băng hợp đồng. | ⏳ hoãn-có-ghi — PR riêng |
| **DL-11.A1-4** | **Giữ cắt theo heading (`##`), không cắt mịn hơn, không gộp doc.** | Đo được: gộp doc buộc chọn giữa **mất 1 case** (giữ vai chunk đầu → SC-03 không với tới) và **2 hàng rò vai** (gộp vai → thân `finance` phục vụ query `public`). Cắt mịn hơn (paragraph) không mua thêm gì: rò vẫn 0, recall vẫn 6/6, margin không khá hơn, đổi lại 78 hàng thay vì 25. Độ mịn chunk bị chặn dưới bởi **fence**, không bởi chất lượng truy xuất. | ✅ chốt (AIE-1) |
| **DL-11.A1-5** | **Q-3 (DE) — đồng ý cost một-nguồn:** executor chỉ cấp `tokens`, sink tính `cost` từ `tokens` + bảng đơn giá. | Đơn giá đổi một chỗ mà hai nơi cùng tính thì ba mặt lệch nhau không ai biết mặt nào đúng. Code engine **đã đúng sẵn**: `executors.py:258` trả `Tokens`, `interpreter.py:320` để `cost=_NO_COST`. | ✅ trả lời DE |
| **DL-11.A1-6** | **Q-D (DE) — AIE-1 tự dựng stub `kb.search` bên engine**, không nhận `StubKbSearch` dùng chung từ `packages/kb`. | Ràng buộc cứng, không phải khẩu vị: `.importlinter` cấm `studio_engine` import `studio_kb`, nên stub sống trong kb là thứ test engine không gọi tới được nếu không phá lớp. | ✅ trả lời DE |
| **DL-11.A1-7** | **Nơi đóng dấu `FROZEN` cho `embedding-service.v0.md`: lật cờ tại chỗ**, không mở PR vào `contracts`. | Theo ADR-D11-01 nhánh (a): hợp đồng không đổi field/kiểu trong `contracts` thì không cần PR ở đó. Bản này giữ nguyên `protocols.py:17`. | ⏳ phụ thuộc ADR-D11-01 chốt |
| **DL-11.A1-8** | **Bỏ mọi câu mô tả tình trạng cài đặt của `graph_lint` khỏi comment `interpreter.py`;** lý do tồn tại của cycle-guard viết lại thành *"`run()` không gọi `graph_lint`"*. | Comment cũ khẳng định *"4 luật của graph_lint không phủ lasso"* — **sai từ lúc viết**: luật 2 "no forbidden cycle" đã có trong spec từ bản stub, và workbench#12 cài nó bằng DFS 3-màu **có** bắt `a→b→b`. Comment mô tả tình trạng package khác là thứ hỏng lặng lẽ mỗi lần bên kia tiến; mô tả **module này** thì đúng ở mọi trạng thái. | ✅ sửa (chỉ comment, 92 test vẫn xanh) |

## Câu CHẶN chưa đóng

| # | Với ai | Nội dung | Ảnh hưởng |
|---|---|---|---|
| **A1-1** | **DE** | `trace-event.v0.md:121` (bản đang xin freeze) mô tả `studio_engine.interpreter._WALK_ORDER` như cơ chế hiện hành. Hằng số **đã bỏ từ D6 (#27)**; walk suy từ `recipe.dag.edges` (`interpreter.py:9`, `test_dag_edge_walk.py:3`). Chính DE đã ghi đúng ở `trace_reader.py:44-49`: *"Đây không còn là nguồn sự thật."* → mâu thuẫn nằm **bên trong** bộ artifact của DE, giữa code và văn bản sắp đóng băng. | 🔴 **CHẶN chữ ký AIE-1** trên `trace-event` — sửa câu chữ rồi ký |
| **A1-4** | **AIE-2** | Bộ golden hiện tại **không phân biệt được** embedding thật với vector hằng số (6/6 cả hai). 4/6 case do fence tự quyết; 2 case còn lại vector hằng số thắng nhờ hoà điểm + thứ tự sort. → `citation_accuracy` hôm nay đo sức mạnh **fence**, không đo sức mạnh **truy xuất**, và không bắt được hồi quy embedding. | 🟠 AIE-2 cần biết trước khi ký scorecard; sửa ở **bộ golden** (thêm case có ≥2 ứng viên cùng tenant+vai), không sửa ở embedding |
| **A1-6** | **SWE** | `recipe.v0.md` (workbench#12) chốt *"recipe không qua validator = không interpret"*, gọi `graph_lint()` là *"cổng duy nhất"*. Cổng chưa nối: `graph_lint` có **0 caller thật** — chỗ duy nhất gọi là `publish.py`, mà `publish()` vẫn là stub `raise NotImplementedError` (`publish.py:43`); `interpreter.run()` không gọi. Recipe đi builder → interpreter không chạm cổng nào. | 🟠 xác nhận với SWE trước khi ký `recipe` — thân 4 luật đúng, thiếu **điểm nối** |
| **A1-7** | leader / cả nhóm | ADR-D11-01 còn PROPOSED → hình thức "4/4 chữ ký" chưa chốt. Vị trí decision-log **đã rõ**: nhánh `origin/docs/d11-shared-decision-log` dựng `kit/docs/decisions/` làm index, nguyên tắc *"nội dung thật nằm trong repo người giữ bút, kit chỉ giữ link"* → file này **đang đúng chỗ**. | ⏳ chặn DoD ô 4 |
| **A1-8** | **DE + mentor + apps/studio** | **Mini-RFC tenant+RLS mục B sẽ làm CHẾT mọi lần ghi trace.** RFC đề xuất `ENABLE`+`FORCE ROW LEVEL SECURITY` trên `obs.trace_events` với `USING (tenant_id = current_setting('app.tenant_id')::uuid)`. Nhưng `PgTraceWriter.write()` (`apps/studio/src/studio_app/obs/trace_writer.py:26`) **mở connection riêng từ pool** và INSERT thẳng — **không** gọi `set_config('app.tenant_id', …)`. Middleware có set, nhưng bằng `SET LOCAL`, mà `SET LOCAL` là **phạm vi giao dịch** (chính docstring middleware:5 ghi vậy) → connection của writer là giao dịch khác, biến chưa đặt. Với `FORCE`, policy cắn cả owner, `WITH CHECK` mặc định theo `USING` ⇒ **INSERT bị từ chối**, mọi `interpreter.run()` gãy ở bước emit trace. | 🔴 **ĐIỀU KIỆN KÝ của AIE-1**: không ký mục B cho `obs.trace_events` cho tới khi đường ghi bind `app.tenant_id` trên chính connection của nó |

> **Chưa có chữ ký nào (0/4)** trên `embedding-service.v0.md` §0.2. Không ký khống.
