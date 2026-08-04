---
id: studio.design-note.aie1.day-11
type: design-note
role: AIE-1 — Trần Bá Đạt
day: 11
date: 2026-08-03
status: draft (chờ duyệt — DoD #81 ô 2)
scope: 6 node-type executor · chunking×embedding trade-off measurement
length_target: ≤2 trang
---

# Design-note AIE-1 (D11) — 6 node-type executor + đo trade-off chunking×embedding

> Neo: issue **#81** (*"Design-note: 6 node-type executor + chunking×embedding trade-off
> measurement"*). Không tóm tắt contract — hợp đồng tiêu thụ nằm ở
> [`embedding-service.v0.md`](../contracts/embedding-service.v0.md). Note này là **thiết kế + đánh
> đổi có số đo**. Mọi con số §3 tái lập bằng `uv run python packages/engine/scripts/measure_chunk_embed.py`.

## 1. Bài toán một câu

Interpreter phải đi hết `recipe.dag` bằng **đúng 6 loại node đóng**, mỗi loại một executor, sao cho
(a) không loại nào tự dựng được danh tính tenant, (b) mọi trích dẫn ra trace đều **có căn cứ**, và
(c) seam `EmbeddingService` chốt được **hôm nay** dù impl gateway thật chưa về.

## 2. 6 node-type executor — hình dạng và vì sao

**Một enum, hai chốt.** `NodeType` (6 giá trị) sống duy nhất ở `studio_contracts.nodes`; cả
`graph_lint` (SWE) lẫn `registry.py` (AIE-1) đều import từ đó. `registry.py::REGISTRY` là *lớp chặn
thứ hai* — `test_node_type_closed.py::test_registry_has_exactly_six` khoá tập khoá đúng 6, nên kể cả
ai lách được enum pydantic thì registry vẫn đỏ. Thêm loại thứ 7 = breaking change, mini-RFC + 4/4 ký.

**Mỗi executor nhận collaborator qua constructor, không tự dựng.** Đây là điều kiện để composition
root (`apps/studio`, D6) là nơi DUY NHẤT biết impl thật là gì — engine không được import `studio_kb`
(`.importlinter`), nên seam bắt buộc phải là Protocol.

| Node | Collaborator | Trạng thái | Ghi chú thiết kế |
|---|---|---|---|
| `kb-retrieve` | `KbSearch` | đã điền (`executors.py:82`) | fence-EXECUTOR |
| `llm-step` | `LLM` + `EmbeddingService` | đã điền (`:153`) | trích dẫn phải có căn cứ |
| `condition` | — | `NotImplementedError` (`:260`) | grammar `Edge.when` do SWE đồng sở hữu |
| `tool-call` | `ToolDispatch` (seam engine-local) | đã điền (`:286`) | belt thứ 2 sau recipe-validator |
| `hitl-pause` | — | `NotImplementedError` (`:317`) | INV-2, cần đường yield về playground |
| `end` | — | đã điền (`:329`) | mốc dừng cho walk |

Ba quyết định đáng nói, vì cả ba đều là chỗ dễ làm sai mà vẫn "chạy xanh":

1. **`kb-retrieve` fail-closed trên `tenant_id` (`executors.py:141`).** Executor **không** tự suy
   danh tính tenant: `tenant_id` không phải `UUID` thật lấy từ `session_context` → `PermissionError`.
   Bản trước dùng nil-UUID làm sentinel — fail-closed **do may** (0 hàng tình cờ khớp), không do hợp
   đồng, và một lỗi nối dây phía trên sẽ đọc y hệt "tenant này không có chunk nào".
2. **`llm-step`: trích dẫn phải vừa được truy xuất vừa được nhắc (`:254`).** `citations` chỉ giữ
   `[chunk_id]` **có mặt trong `retrieved_chunks`**. Bỏ vế "có căn cứ" thì một marker bịa lọt vào
   trace như trích dẫn thật và `citation_accuracy` của AIE-2 ăn điểm giả.
3. **`refused = not citations` (`:260`), thuần cấu trúc.** Hai tín hiệu trước đã **đo được là sai**
   (`tests/test_refusal_from_grounding.py`): `not retrieved_chunks` sai vì SC-04 vẫn truy xuất được
   3 chunk ankor sau khi fence bỏ hết chunk borea; sentinel `[[REFUSED]]` không tồn tại ở đâu trong
   workspace. Hạn chế còn lại nói thẳng: model trả lời đúng mà quên ngoặc vuông thì bị chấm là từ chối.

**Đường đi giờ do recipe quyết định, không do hằng số.** Từ D6 (#27) `_WALK_ORDER` đã bị bỏ; walk suy
từ `recipe.dag.edges` (`interpreter.py:9`). Điều này **mâu thuẫn với bản freeze-ready của DE** —
xem §5.

## 3. Chunking × embedding — SỐ ĐO, không phải lập luận

Lưới đo: 4 cách cắt × 6 số chiều, trên corpus Callisto thật (5 doc) + `golden/smoke-10.yaml`
(6 case dương). `margin` = cos(query, chunk đúng) − cos(query, chunk sai tốt nhất), đo **bên trong
tập đã qua fence** — đo ngoài fence là đo nhầm tầng.

| Cách cắt | Hàng | Rò vai | recall@1 | Case còn tranh chấp |
|---|---|---|---|---|
| paragraph | 78 | **0** | 6/6 | 2/6 |
| **heading (đang chạy)** | 25 | **0** | 6/6 | 2/6 |
| whole-doc (giữ vai đầu) | 5 | **1** | **5/6** | 2/6 |
| whole-doc (gộp vai) | 6 | **2** | 6/6 | 2/6 |

**Kết luận 1 — độ mịn chunk bị chặn dưới bởi FENCE, không bởi chất lượng truy xuất.** Gộp doc buộc
phải chọn một trong hai đằng nào cũng hỏng: giữ vai của chunk đầu thì phần `finance` của
`ankor-expense-001` bị xếp sai vai và **mất hẳn 1 case** (SC-03 không với tới được); gộp hợp các vai
thì không mất gì nhưng **2 hàng rò vai** — thân `finance` phục vụ cho query phạm vi `public`. Luật
"1 chunk = 1 `section_role`" (callisto-doc-schema §5) không phải quy ước gọn gàng, nó là thứ giữ cho
fence có nghĩa. Cắt mịn hơn (paragraph) **không mua thêm gì đo được**: rò vẫn 0, recall vẫn 6/6,
margin không khá hơn — đổi lại 78 hàng thay vì 25. Giữ heading.

**Kết luận 2 — `dim` không phải nút thắt, và tăng chiều không đơn điệu tốt lên.** Ở
`EMBEDDING_DIM = 8` (`kb/schema.py:33`), 512 token phân biệt nhồi vào 8 ô → **98.4% token chia ô với
token khác**. Vậy mà recall@1 vẫn 6/6 ở mọi chiều từ 8 tới 256, còn margin thì nhấp nhô chứ không
tăng theo chiều (heading: 0.163 ở dim=8 → 0.224 ở dim=64 → 0.171 ở dim=256). Nói cách khác: **bộ đo
hiện tại không đủ sức phân giải để biện minh cho bất kỳ lựa chọn `dim` nào.**

**Kết luận 3 (quan trọng nhất) — thước đo hiện tại KHÔNG đo được chất lượng embedding.**
Null control, cùng corpus cùng golden:

| Embedding | recall@1 | Top-1 thắng không do hoà |
|---|---|---|
| bag-of-words dim=8 (đang chạy) | 6/6 | 2 |
| bag-of-words dim=256 | 6/6 | 2 |
| **vector hằng số — 0 bit thông tin** | **6/6** | **0** |
| băm cả câu (không cấu trúc cosine) | 5/6 | 1 |

Một embedding mang **không một bit thông tin nào** vẫn được 6/6 — bằng đúng bản thật. Lý do: fence đã
tự quyết **4/6 case** (tập sau fence còn đúng 1 ứng viên, ranking không quyết định gì), và 2 case còn
lại vector hằng số thắng nhờ **hoà điểm rồi ăn may thứ tự sort** (cột phải = 0). Hệ quả phải nói
thẳng: `citation_accuracy` dựng trên bộ này **không phát hiện được hồi quy embedding** — hôm nay nó
đo sức mạnh của fence, không đo sức mạnh của truy xuất. Đây là việc phải sửa ở bộ golden (thêm case
có ≥2 ứng viên cùng tenant+vai), không phải sửa ở embedding.

## 4. Một phương án đã BỎ — "ghim `dim` theo bộ đo"

**Phương án:** chạy lưới trên, chọn `dim` cho margin cao nhất (dim=64), ghim vào hợp đồng làm số chuẩn
khi gateway về. **Bỏ vì** §3 kết luận 3: bộ đo không phân biệt nổi embedding thật với vector hằng số,
nên "dim=64 tốt nhất" là đọc nhiễu thành tín hiệu. Ghim một con số rút từ thước hỏng còn tệ hơn không
ghim — nó mang dáng vẻ đã-đo mà không có nội dung, và người sau sẽ tin. Thay vào đó hợp đồng ghim
**bất biến bề rộng** (mọi vector cùng chiều, khớp `EMBEDDING_DIM`) và để **giá trị** mở tới khi có bộ
đo đủ sức phân giải.

## 5. Câu chặn phải giải trước khi ký

- **[CHẶN — với DE]** `trace-event.v0.md:121` (bản đang xin freeze) viết *"Chuỗi kỳ vọng hiện tại là 4
  node… `studio_engine.interpreter._WALK_ORDER`"*. Hằng số đó **đã bị bỏ từ D6** và walk suy từ
  `recipe.dag.edges` (`interpreter.py:9`, `test_dag_edge_walk.py:3`). Chính DE đã ghi đúng điều này ở
  `trace_reader.py:44-49` (*"Đây không còn là nguồn sự thật"*) — tức mâu thuẫn nằm **bên trong** bộ
  artifact của DE, giữa code và văn bản sắp đóng băng. Sửa câu chữ trước, rồi AIE-1 ký.
- **[Q-3 — trả lời DE]** cost một-nguồn: **đồng ý**, executor chỉ cấp `tokens`, sink tính `cost`. Code
  đã đúng sẵn: `executors.py:258` trả `Tokens`, `interpreter.py:320` để `cost=_NO_COST`.
- **[Q-D — quyết của AIE-1]** stub `kb.search`: **AIE-1 tự dựng bên engine**, không nhận
  `StubKbSearch` dùng chung từ `packages/kb`. Lý do là ràng buộc cứng, không phải khẩu vị:
  `.importlinter` cấm `studio_engine` import `studio_kb`, nên một stub sống trong kb là thứ test
  engine **không gọi tới được** nếu không phá lớp.
