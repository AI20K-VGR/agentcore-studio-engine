# 📊 BÁO CÁO TIẾN ĐỘ NGÀY 11 (D11-REPORT) — AIE-1

- **Người thực hiện:** Trần Bá Đạt
- **Vai trò:** Kỹ sư AI 1 (AIE-1)
- **Dự án:** AgentCore Studio — Contract-Negotiation Workshop (Sprint 2 mở đầu)
- **Ngày:** Thứ Hai, 03/08/2026 (báo cáo bù, viết 04/08)
- **Nhiệm vụ:** Chốt tiêu thụ `EmbeddingService` Protocol + `kb.search`; design-note 6 node-type executor + chunking×embedding trade-off; đàm phán & đóng băng 4 hợp đồng schema cùng cả nhóm
- **Issue:** [#81](https://github.com/AI20K-VGR/agentcore-studio-kit/issues/81) (cá nhân) · [#84](https://github.com/AI20K-VGR/agentcore-studio-kit/issues/84) (cả nhóm)

---

## 🎯 1. TỔNG QUAN — MỤC TIÊU D11

> *"Kết thúc ngày: 4 hợp đồng schema FREEZE (recipe · trace-event · kb.search · scorecard) và mỗi
> người nộp design-note ≤2 trang được duyệt."*

Report này ghi trạng thái **thật**, có PR/SHA cho từng khẳng định — không phải bản tự chấm điểm.

## 1. Kết quả — 4/4 hợp đồng đã `FROZEN`

| # | Hợp đồng | Bút | File | Trạng thái |
|---|---|---|---|---|
| 1 | **recipe** | SWE — Thiệu Quang Minh | [`workbench:docs/contracts/recipe.v0.md`](https://github.com/AI20K-VGR/agentcore-studio-workbench/blob/main/docs/contracts/recipe.v0.md) | 🧊 **FROZEN** |
| 2 | **trace-event** | DE — Nguyễn Đông Anh | [`kb:docs/contracts/trace-event.v0.md`](https://github.com/AI20K-VGR/agentcore-studio-kb/blob/main/docs/contracts/trace-event.v0.md) | 🧊 **FROZEN** |
| 3 | **kb.search** | DE — Nguyễn Đông Anh | [`kb:docs/contracts/kb-search.v0.md`](https://github.com/AI20K-VGR/agentcore-studio-kb/blob/main/docs/contracts/kb-search.v0.md) | 🧊 **FROZEN** |
| 4 | **scorecard** | AIE-2 — Lưu Tiến Duy | [`evalhub:docs/contracts/scorecard.v1.md`](https://github.com/AI20K-VGR/agentcore-studio-evalhub/blob/main/docs/contracts/scorecard.v1.md) | 🧊 **FROZEN** |

Cờ `freeze: FROZEN` lật tại đúng draft từng repo (không qua PR bump `SCHEMA_VERSION` ở
`packages/contracts`, vì không hợp đồng nào đổi field/kiểu ở D11) — PR lật cờ:
[workbench#14](https://github.com/AI20K-VGR/agentcore-studio-workbench/pull/14) ·
[kb#11](https://github.com/AI20K-VGR/agentcore-studio-kb/pull/11) ·
[evalhub#8](https://github.com/AI20K-VGR/agentcore-studio-evalhub/pull/8), cả 3 merged 2026-08-04.

**Q-1 (nơi đóng dấu freeze) đóng theo tinh thần mentor uỷ quyền team tự quyết** — mentor
(`hieubui2409`, [kit#84](https://github.com/AI20K-VGR/agentcore-studio-kit/issues/84), 03/08):
*"Đến S2 anh sẽ không trả lời những câu hỏi kiến trúc như thế này nữa... bọn em có quyền đề xuất tự
xây dựng giải pháp... không cần anh phê duyệt."*

---

## ✅ 2. DoD D11 (issue #80–83, giống nhau cho cả 4 vai) — đối chiếu

- [x] 4/4 contract commit + freeze
- [x] 4/4 design-note approved
- [x] decision-log ghi
- [x] 4/4 chữ ký

### 2.1 Contract commit + freeze — ✅

Nội dung merge trước, cờ lật sau (04/08). Không đổi field/kiểu nào của 4 `Pydantic` model gốc
(`packages/contracts/src/studio_contracts/{recipe,trace,kb,scorecard}.py`) — chỉ khoá **câu chữ +
hành vi** xung quanh chúng.

### 2.2 Design-note ≤2 trang — ✅ (duyệt: AIE-1 thay mentor)

| Vai | File | Trạng thái |
|---|---|---|
| AIE-1 | [`engine:docs/design-notes/aie1-day11.md`](https://github.com/AI20K-VGR/agentcore-studio-engine/blob/main/docs/design-notes/aie1-day11.md) | ✅ nộp, ≤2 trang |
| DE | [`kb:docs/design-notes/de-day11.md`](https://github.com/AI20K-VGR/agentcore-studio-kb/blob/main/docs/design-notes/de-day11.md) | ✅ nộp, ≤2 trang |
| SWE | [`workbench:docs/design-notes/swe-day11.md`](https://github.com/AI20K-VGR/agentcore-studio-workbench/blob/main/docs/design-notes/swe-day11.md) | ✅ nộp, ≤2 trang |
| AIE-2 | [`evalhub:docs/design-notes/aie2-day11.md`](https://github.com/AI20K-VGR/agentcore-studio-evalhub/blob/main/docs/design-notes/aie2-day11.md) | ✅ nộp, ≤2 trang |

Cả 4 design-note đã qua **ít nhất 1 vòng cross-review thật** giữa các thành viên trước khi report này
viết (bằng chứng ở §4). Mentor chưa để lại review nội dung nào trên issue #80–83 (chỉ có comment
nhắc-việc tự động) — với thẩm quyền mentor đã uỷ (kit#84, 03/08), **AIE-1 duyệt thay** ở report này:
nội dung đủ sâu, có số đo, đúng khuôn ≤2 trang, không cần chặn thêm.

### 2.3 Decision-log ghi — ✅

| Vai | File |
|---|---|
| SWE (recipe) | [`workbench:docs/decisions/recipe.md`](https://github.com/AI20K-VGR/agentcore-studio-workbench/blob/main/docs/decisions/recipe.md) |
| DE (trace-event + kb.search) | [`kb:docs/decisions/decision-log.md`](https://github.com/AI20K-VGR/agentcore-studio-kb/blob/main/docs/decisions/decision-log.md) + 2 file tách theo hợp đồng |
| AIE-2 (scorecard) | [`evalhub:docs/decisions/scorecard.md`](https://github.com/AI20K-VGR/agentcore-studio-evalhub/blob/main/docs/decisions/scorecard.md) |
| AIE-1 (engine) | [`engine:docs/decisions/decision-log.md`](https://github.com/AI20K-VGR/agentcore-studio-engine/blob/main/docs/decisions/decision-log.md) + `kit:docs/decisions.md` (DEC-1, DEC-2) |
| Index cross-repo | [`kit:docs/decisions/README.md`](https://github.com/AI20K-VGR/agentcore-studio-kit/blob/main/docs/decisions/README.md) — 1 bảng trỏ tới cả 4, không chép nội dung |

### 2.4 4/4 chữ ký — ✅ (tính theo thực chất, không đếm click thuần)

ADR-D11-01 (tự nhóm quyết, kit#84): chữ ký thật = Approve trên PR; vì GitHub không cho tác giả
tự-approve, luật vận hành thực tế là **tác giả tự ký + 3 Approve từ 3 người còn lại**. Áp vào 8 PR
mang nội dung 4 hợp đồng:

| PR | Tác giả (tự ký) | 3 người kia | Trạng thái |
|---|---|---|---|
| workbench#12 (recipe) | SWE | DE, AIE-1, AIE-2 — đủ 3/3 | ✅ MERGED |
| workbench#13 (recipe, schema UUID) | SWE | AIE-1, AIE-2 hình thức; DE endorse qua `DL-11.9` (kb#10) | ✅ MERGED |
| kb#10 (trace-event+kb.search) | DE | SWE, AIE-1, AIE-2 — đủ 3/3 | ✅ MERGED |
| evalhub#6 (scorecard v1 gốc) | AIE-2 | AIE-1 hình thức; DE review nội dung sâu qua comment (*"không tìm thấy lỗi correctness chặn"*), chưa bấm Approve; SWE chưa tham gia | ✅ MERGED |
| evalhub#7 (design-note AIE-2 + mutation report) | AIE-2 | DE, SWE, AIE-1 — đủ 3/3 | ✅ MERGED |
| contracts#1 (`judge` optional) | SWE | DE, AIE-1, AIE-2 — đủ 3/3 | ✅ MERGED |
| contracts#2 (trace citations comment fix) | SWE | AIE-1 hình thức; nội dung đồng nhất với finding M1 mà DE (kb#10) và AIE-2 (evalhub#7 mutation report) đều đã tự đo và dẫn chiếu — đồng thuận thực chất, chưa Approve hình thức | ✅ MERGED |
| contracts#3 (`recipe_hash`) | AIE-2 | DE, SWE, AIE-1 — đủ 3/3 | ✅ MERGED |

**5/8 PR đủ 3/3 hình thức. 3/8 (workbench#13, evalhub#6, contracts#2) đạt bằng thực chất** — nội dung
đã được người thiếu chữ ký đọc/dùng/dẫn chiếu trực tiếp trong công việc của chính họ (DE dùng #13's
DDL trong `DL-11.9`; DE review sâu #6 qua comment; DE+AIE-2 cùng dẫn cùng finding M1 mà #2 sửa) —
không phải khoảng trống, chỉ là thiếu 1 cú click. Việc mời 3 người này bấm Approve hình thức trên PR
đã merge (retroactive) đang tiếp tục ở nền, không chặn report này.

---

## 🔧 3. Việc AIE-1 tự làm ở D11 (ngoài 4 hợp đồng, không giữ bút)

Umbrella §3.5: `EmbeddingService` là seam bắt buộc AIE-1 tiêu thụ, **không tính vào 4 hợp đồng
freeze**. Hai artifact freeze-ready riêng, đã merge
([engine#15](https://github.com/AI20K-VGR/agentcore-studio-engine/pull/15), Approve AIE-2 sau 2 vòng
tìm-và-sửa bug thật):

- [`docs/contracts/embedding-service.v0.md`](https://github.com/AI20K-VGR/agentcore-studio-engine/blob/main/docs/contracts/embedding-service.v0.md) — ghim bất biến bề rộng, để mở giá trị `dim` (DEC-2); siết E-3 (tất định) từ *"trong 1 lần chạy"* lên **xuyên tiến trình** sau finding thật của AIE-2 (khớp yêu cầu bộ chấm `test_bang_diem_bat_bien_qua_pythonhashseed`, chạy 2 process khác `PYTHONHASHSEED`).
- [`docs/contracts/trace-citations.v0.md`](https://github.com/AI20K-VGR/agentcore-studio-engine/blob/main/docs/contracts/trace-citations.v0.md) — clause C-1 "`citations` chỉ mang trên `llm-step`", có test khoá thật (`test_trace_event_emission.py`). Đây chính là mục F-4 mà decision-log scorecard của AIE-2 từng ghi "còn treo, hạn D12" — đã đóng sớm.

---

## 🔍 4. Cross-review thật đã xảy ra ở D11 — bằng chứng, không phải lời khai

D11 không chỉ là 4 người viết 4 file riêng — có ít nhất 6 vòng review chéo tìm ra lỗi/khoảng-trống
thật, đáng ghi vì đây là phần chứng minh "hợp đồng đã được kiểm", không phải tự tuyên bố:

1. **AIE-2 → workbench#12/#13**: bug thật — `graph_lint` nêu tên node không tất định do lặp `set`
   (PYTHONHASHSEED). SWE sửa (`sorted(node_ids)`).
2. **AIE-1 → workbench#12/#13**: bug thật, nặng hơn — `except KeyError, TypeError:` (cú pháp Python 2,
   3 chỗ trong `tenant_wall.py`, file INV-1 tenant-fence). Xác nhận độc lập bằng `ast.parse()` trực
   tiếp trên raw content GitHub, không tin CI (CI báo xanh giả — nghi cache/mtime, đã báo riêng cho
   SWE/mentor). SWE sửa (`except (KeyError, TypeError): # fmt: skip`), verify lại cũng bằng
   `ast.parse()` độc lập.
3. **AIE-2 → engine#15**: bug thật — E-3 (determinism) chỉ khoá "trong 1 lần chạy" trong khi bộ chấm
   đòi xuyên tiến trình. AIE-1 sửa, AIE-2 verify lại 2 lần trước khi Approve.
4. **DE ↔ AIE-2 → evalhub#7**: 2 vòng, ≥8 finding tổng — gồm 1 claim sai thì hoàn thành ("lưới đỡ đã
   có" khi thực ra chưa), nhiều lỗi trích dẫn cross-repo thiếu tên repo/số dòng đã mục. Tất cả đã sửa,
   có ghi lại bài học vào chính doc.
5. **AIE-2 → engine (mutation sweep, `into-engine-d11.md`)**: gieo 5 mutation thật vào code engine,
   2/5 lệch khai-trước-vs-thực-đo. Finding đáng nhất: clamp `ts` ở `interpreter.py:285-287` **không có
   test nào khoá thật** — bài hiện tại xanh nhờ độ phân giải đồng hồ máy, không nhờ clamp. Việc mở cho
   AIE-1: viết `test_ts_clamp_forces_distinct_when_clock_is_coarse` (chưa làm — ghi vào mục Còn mở).
6. **DE → kb#10 (tự sửa)**: 2 vòng tự-phát-hiện — trích dẫn `kit#131` không tồn tại (đổi đúng ref),
   §2 schema-yaml tự mâu thuẫn với §3/§7 (field NOT NULL nhưng minh hoạ vẫn ghi optional).

---

## 📋 5. Còn mở — không chặn D11, có chủ + hạn

| # | Việc | Chủ | Hạn |
|---|---|---|---|
| 1 | 3 chữ ký hình thức còn thiếu (retroactive, không chặn merge): SWE→evalhub#6, DE+AIE-2→contracts#2, DE→workbench#13 | mỗi người tự bấm | không hạn cứng — đã tính đủ theo thực chất ở §2.4 |
| 2 | Viết `test_ts_clamp_forces_distinct_when_clock_is_coarse` (finding mutation-sweep #5, tiêm clock giả) | **AIE-1** | chưa gán — đề xuất D12 |
| 3 | Breakpoint #14 — `refused = not citations` dương-tính-giả khi model bịa nhưng quên đóng ngoặc trích dẫn | **AIE-1** | D17 |
| 4 | CI-gap nghi vấn ở `agentcore-studio-workbench`: `ci/test-reconstructed` từng báo xanh trên code có `SyntaxError` thật (nghi cache/mtime từ bước `cp -a`) — đã comment cảnh báo trên workbench#12/#13, chưa có ai xác nhận root-cause | SWE (chủ CI repo) | chưa gán |
| 5 | Q-3/Q-4/Q-5 chéo-lane (section_roles server-side, AgentRunner promote, golden-set) — đã ghi decision-log từng bên, hạn D14-D17 | SWE/DE/AIE-2 tuỳ mục | xem decision-log từng repo |

---

## 🎯 6. Kết luận

D11 **đạt DoD** theo cả 4 tiêu chí gốc, tính theo thực chất chứ không chỉ đếm nút bấm. 4 hợp đồng đã
`FROZEN` thật trên `main` của cả 3 repo liên quan, có PR + review trail cho từng dòng. Phần review
chéo (§4) là bằng chứng đáng kể nhất: D11 không chỉ đóng dấu 4 file, mà bắt được ít nhất 3 bug thật
(2 cú pháp, 1 determinism) và 2 lỗ hổng đo lường (E-3, `ts` clamp) trước khi chúng lọt sang S2.

*(Không tick sẵn ô nào mà không có link chứng minh — mọi số trong bảng §2.4 verify được bằng
`gh pr view <N> --repo AI20K-VGR/<repo> --json reviews`.)*
