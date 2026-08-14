# Bằng chứng D20 — phần AIE-1 (engine) · GATE-2 + plan-vs-actual

> **Mục đích:** phần AIE-1 trong evidence-pack GATE-2 (issue **kit#126**, con của gate **kit#129**).
> Chuẩn *"đủ để chấm không cần hỏi"* — mọi con số ở §2 **tái lập được** bằng khối lệnh ở §1, không tin
> lời khai. §3 là **plan-vs-actual** đối chiếu [design-note D11](design-notes/aie1-day11.md) §5. §4 là
> điều tra thật cho finding (e)① mà `dholmes0207` nêu ở `kit#126` (13/08) — đo bằng thực nghiệm, không
> phải suy luận.

---

## 1. Chạy lại — khối lệnh dán-là-chạy

Từ **thư mục gốc của kit** (`agentcore-studio-kit/`):

```bash
uv run --package agentcore-studio-engine pytest packages/engine/tests -q   # 168 passed
uv run ruff check packages/engine                                          # All checks passed!
uv run ruff format --check packages/engine                                 # 36 files already formatted
uv run mypy packages/engine                                                # Success: no issues found in 36 source files
uv run lint-imports                                                        # 1 kept, 0 broken

uv run python packages/engine/scripts/run_golden_batch.py                  # 30/30 case khớp nhãn golden
```

`run_golden_batch.py` **không cần Postgres** (dùng `StaticKbSearch` thật, in-memory) — chạy được
standalone. §4 dưới đây (đường `EngineAgentRunner`+`EvalHarness`) **cần** Postgres:

```bash
docker compose -f docker-compose.test.yml up -d --wait
export STUDIO_DATABASE_URL_ADMIN=postgresql://studio_owner:changeme@localhost:5433/studio_test
export STUDIO_DATABASE_URL=postgresql://studio_app:changeme@localhost:5433/studio_test
```

---

## 2. Số đo — DoD `kit#126` từng item

| Item `#126` | Bằng chứng (test / lệnh / SHA) | Kết |
|---|---|---|
| **6 node-type executor chạy DAG thật qua ES** | `test_condition_dag_e2e.py` + `test_executors_behavior.py`: 6/6 `NodeType` (`kb-retrieve`/`llm-step`/`condition`/`tool-call`/`hitl-pause`/`end`) chạy chung **một** DAG thật, khoá bởi `test_node_type_closed.py` (cấm loại thứ 7) | ✅ |
| **Bảng chunking×embedding trade-off có số, ghép vào spine** | `measure_chunk_embed.py` tái lập khớp D14 từng số (`aie1-day14-grid-harness.md`) — verify độc lập trước merge PR#25 | ✅ |
| **Đã ghép vào spine (golden-30 correctness)** | `run_golden_batch.py`: **30/30** case khớp nhãn (citations + refused), qua `StaticKbSearch` thật + `interpreter.run()` thật | ✅ |
| **Regression xanh trước PR** | `168 passed` (pytest) · ruff/mypy/lint-imports sạch — 5 lệnh, khớp CI thật `reusable-domain-ci.yml` job `lint` | ✅ |

**3 PR riêng biệt của lane này (D18→D20), mỗi PR 1 ngày, không gộp:**

| Ngày | PR | Merge commit | Nội dung |
|---|---|---|---|
| D18 | `engine#23` | `941c8a0` | `llm-step` output ổn định cho judge + ES gateway-flag |
| D19 | `engine#24` | `562346b` | Token accounting thật (`Tokens(prompt,completion)`) + idempotent-qua-replay + failure-mode retrieval |
| D20 | `engine#25` | `bfa19cc` | 6-node DAG spine thật + tái lập bảng trade-off |

---

## 3. Plan-vs-actual — đối chiếu design-note D11 §5 "Câu chặn phải giải trước khi ký"

| # | Cam kết D11 (03/08, §5) | Actual D20 | Kết |
|---|---|---|---|
| Q-3 | *"cost một-nguồn: đồng ý, executor chỉ cấp `tokens`, sink tính `cost`. Code đã đúng sẵn: `cost=_NO_COST`."* | Giữ nguyên đúng như cam kết: D19 (`engine#24`) đã thêm **token accounting thật** (`Tokens(prompt, completion)` không còn hard-code `(0,0)`), nhưng `interpreter.py:73,438` **vẫn** `cost=_NO_COST` (0.0) — executor không tự tính `cost`, đúng như đã ký ở D11. Sink (`obs.trace_events`/cost-lineage, lane DE) đọc `tokens` và **tự tính** `cost` on-read (`kb#22`, D19, merge 13/08). | ✅ **đúng như đã ký — không phải một lỗ sót, là kiến trúc đã chốt từ D11** |
| Q-D | *"stub `kb.search` sống trong engine, không nhận `StubKbSearch` từ `packages/kb` — `.importlinter` cấm `studio_engine` import `studio_kb`."* | Giữ nguyên; `run_golden_batch.py` (script, ngoài `src/studio_engine/`) là nơi duy nhất engine chạm `studio_kb`, và nó nằm ngoài vùng `.importlinter` quét — đúng lý do đã nêu ở D11. | ✅ **đúng như đã ký** |
| — | *(D11 không có mục dự đoán riêng cho D20 — file chỉ tới §5, không có §6 "điểm S2 đã biết" như bản DE)* | D20 làm đúng phạm vi đã khoá ở plan `260812-1133-d18-d20-aie1-engine-daily-prs`: 6/6 node-type trong 1 DAG thật, không build gateway LLM/embedding thật (ngoài scope R-6), không đụng `packages/contracts`. | ✅ **không có mục nào D11 hứa mà D20 làm thiếu** |

---

## 4. Finding (e)① — điều tra thật, không phải khai báo

`dholmes0207` nêu ở `kit#126` (13/08, sau `apps/studio` chạy golden-30 qua `EngineAgentRunner` +
`PgKbSearch` + Postgres thật): `success_rate = 0.1667` (5/30), trong khi `DEC-D17-04`
(`evalhub/docs/decisions/scorecard.md:362`) đo `0.2667` (8/30) trên "cùng golden-30". Giả thuyết nêu
ra nhưng chưa xác nhận: *"`engine` hôm nay (`bfa19cc`, D20 6-node) mới hơn bản D17 đã đo — có thể là
nguyên nhân."* Điều kiện lật đã hẹn: chạy lại trên `engine@62773ba` (D17) và so.

### 4.1 Thực nghiệm — checkout `engine@62773ba`, chạy lại đúng harness, so trực tiếp

```text
engine@bfa19cc (D20, hiện tại)  → success_rate=0.16667  citation_accuracy=0.22727  n_scored=22  verdict=FAIL
engine@62773ba (D17, trước D20) → success_rate=0.16667  citation_accuracy=0.22727  n_scored=22  verdict=FAIL
```

Đo bằng cách checkout `packages/engine` sang `62773ba` (dọn `__pycache__` trước — bẫy bytecode đã biết
từ D19/D20), chạy lại chính đường `EngineAgentRunner(kb_search=PgKbSearch, llm=ExtractiveFakeLLM,
trace_writer=PgTraceWriter) → EvalHarness().run(...)` mà `apps/studio/tests/
test_gate2_verdict_from_live_spine.py::test_verdict_fail_tu_run_that` dùng, sau đó checkout lại
`bfa19cc` (trạng thái gốc khôi phục nguyên vẹn, không để lại thay đổi).

**Kết luận: giả thuyết "do `engine` D20" BỊ BÁC BỎ bằng phép đo thật** — hai bản engine cách nhau 2
PR (D18+D19+D20) cho **cùng một số**, sai số 0.

### 4.2 Nguyên nhân thật — hai đường đo khác nhau, không phải hai bản engine khác nhau

Đọc lại chính `DEC-D17-04` (`evalhub/docs/decisions/scorecard.md:357-362`):

> *"`30/30` KHÔNG chuyển thành `success_rate` của scorecard. Đo thật qua `EvalHarness.run` trên chính
> output đó (golden-30, **`StubAgentRunner` nạp từ interpreter thật**)"* → `success_rate=0.2667`,
> **`citation_accuracy=1.0000`**.

So với đường D20 (`test_gate2_verdict_from_live_spine.py`): `EngineAgentRunner` sống, dùng
**`ExtractiveFakeLLM`** trực tiếp (không phải câu trả lời đã ghi lại từ một lần chạy interpreter khác)
→ `citation_accuracy=0.2273`.

**Chênh `citation_accuracy` (1.0000 vs 0.2273) là dấu vết rõ nhất: đây là hai đường đo khác nhau, không
phải cùng một phép đo trên hai commit khác nhau của engine.** `DEC-D17-04` nạp câu trả lời **đã ghi
lại** (recorded) từ một lần chạy trước vào `StubAgentRunner`; D20 chạy **sống** qua `ExtractiveFakeLLM`
mỗi lần. Khác double, khác pha trích câu, khác citation — engine chỉ là seam bị nghi oan vì nó là thứ
mới nhất vừa đổi ngày hôm đó.

**Không sửa số, không sửa harness của AIE-2** (ngoài lane). Kết quả đã đóng câu hỏi ban đầu ("có phải
do `engine` D20") bằng phép đo, và mở lại câu hỏi đúng: hai đường đo có nên hội tụ về một hay không —
đó là quyết định của AIE-2 (chủ `EvalHarness`/`StubAgentRunner`), không phải của lane này.

**Chủ tiếp theo:** AIE-2 (đối chiếu 2 đường đo). **Không còn việc nào chờ AIE-1** trên finding (e)① —
điều kiện lật đã đo xong, kết luận là "không phải engine".

---

## 5. Honest-TODO — không giấu

- **`cost=_NO_COST` (0.0) vẫn hard-code** trong `interpreter.py:73,438` — **đây KHÔNG phải một lỗ
  sót**, là kiến trúc đã ký ở D11 Q-3 (§3 ở trên): executor chỉ cấp `tokens`, sink tính `cost`. Không
  có việc nào của AIE-1 còn treo ở mục này.
- **Gateway LLM/embedding thật** — chính thức ngoài scope kit (quyết định R-6,
  `docs/system-architecture.md §6`). Không build trong 3 PR D18-D20.

---

*Neo state: `packages/engine@bfa19cc` · `apps/studio@` (SHA lúc chạy §4, xem `git -C apps/studio
rev-parse HEAD` tại thời điểm đọc) · Postgres qua `docker-compose.test.yml`.*
