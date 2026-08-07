# D15 — batch ghép thật (`StaticKbSearch`) + xác nhận trace schema + citations C-1

AIE-1, D15 #101 (`plans/260807-1001-d15-aie1-real-batch-trace-citations`, phase 1+2).

## Tóm tắt

D14 chứng minh DAG 4-node `kb-retrieve -> llm-step -> condition -> end` chạy e2e qua
`interpreter.run()`, nhưng `kb-retrieve` gọi vào double test-local `FixtureKbSearch` (1 chunk
giả cố định, `chunk_id="chunk-042"`). Phase này nâng đúng 1 bậc: `run_real_batch.py` tiêm
`StaticKbSearch` (DE, 42 doc Callisto thật) qua đúng chữ ký `KbSearch` Protocol thay
`FixtureKbSearch`, chạy cùng DAG qua `interpreter.run()` thật, rồi tự assert 2 việc DoD #101
đòi: (a) mọi `TraceEvent` đúng schema `trace-event.v0.md` (FROZEN D11), (b) đúng clause C-1
(`trace-citations.v0.md`) — `citations` chỉ trên `llm-step`, nguồn thật từ `kb-retrieve`.
Không sửa `executors.py`/`interpreter.py` (D-3, script read-only với engine).

## Lệnh tái lập

```
uv run python packages/engine/scripts/run_real_batch.py
```

Chạy từ **kit root** (`agentcore-studio-kit/`) — `uv run` resolve `studio_kb` qua uv-workspace;
không chạy standalone được trong repo con `agentcore-studio-engine` (R-1, script tự guard bằng
kiểm file tồn tại trước khi import, in thông báo rõ nếu chạy sai chỗ).

Submodule tại lúc đo (2026-08-07): `packages/engine` SHA `c8551f7`, `packages/kb` SHA `3d6d2f5`.
Không mạng, không model gateway thật — `_CitingLLM` là double trích dẫn lại chunk_id THẬT SỰ
truy xuất được (đọc từ prompt), không phải fixture VCR cố định.

## Kết quả đo thật

- **4 `TraceEvent`** — đúng 1/node, thứ tự `kb-retrieve -> llm-step -> condition -> end`.
- `kb-retrieve` (`n_kb`) trả **5 chunk thật** từ `StaticKbSearch` (query
  `"Nhân viên xin nghỉ phép cần báo trước bao lâu?"`, tenant `ankor`, `section_roles=["public"]`),
  đúng thứ tự xếp hạng tất định (3/3 lần chạy giống hệt, `score` giảm dần rồi `chunk_id` làm
  khoá phụ — `static_search.py:99-101`): `ankor-leave-001#c1`, `ankor-leave-001#c3`,
  `ankor-onboarding-001#c2`, `ankor-holidays-001#c3`, `ankor-leave-001#c2` — **không phải**
  `"chunk-042"` của `FixtureKbSearch` cũ, chứng minh đang chạy trên data thật.
- `llm-step` (`n_llm`) cite lại đúng cả 5 chunk_id đó (`_CitingLLM` echo mọi chunk_id có trong
  prompt) — `refused=False`, `citations` khớp `outputs["chunks"]` của `kb-retrieve` liền trước.
- `condition` (`n_cond`) đọc `refused == false` từ output `llm-step` thật, `result=True`,
  `reason="ok"` — chứng minh routing đọc dữ liệu upstream THẬT, không phải hardcode.
- `end` (`n_end`): `{"terminated": true}`.
- Script exit **0**, in `"schema OK"` + `"C-1 OK"`.

## Assertion thật sự bắt được lỗi — bằng chứng "có răng"

Trước khi khoá assertion, chạy thử với 3 case cố ý sai (throwaway probe, không commit — feed
`TraceEvent` đã bị sửa tay vào thẳng `_assert_schema`/`_assert_citations_c1`, không đụng
`interpreter.py` per D-3):

1. Gán `citations=["fake-chunk-id"]` cho event `condition` (không phải `llm-step`) →
   `_assert_citations_c1` bắt đúng (message verbatim): `"C-1 FAIL: event '<id>'
   node_type=<NodeType.CONDITION: 'condition'> mang citations=['fake-chunk-id'], phải là
   None"`, `sys.exit(1)`.
2. Gán `citations=["not-a-real-chunk-id"]` cho event `llm-step` (chunk_id không tồn tại trong
   `kb-retrieve.outputs["chunks"]`) → bắt đúng: `"C-1 FAIL: ... không khớp chunk_id nào trong
   kb-retrieve.outputs['chunks']=[...]"`, `sys.exit(1)`.
3. Gán `ts="not-a-timestamp"` cho event đầu → `_assert_schema` bắt đúng: `"SCHEMA FAIL: ...
   không parse được ISO-8601"`, `sys.exit(1)`.

Cả 3 case đều exit code 1 đúng như kỳ vọng; chạy lại trên events THẬT (không sửa) → cả 2
assertion PASS (`"schema OK"`/`"C-1 OK"`). Assertion đã được nhìn thấy đỏ ít nhất 1 lần trước
khi khoá — không phải "viết-cho-có".

**Review D15 (I-1)** phát hiện thêm 1 lỗ: cả hai hàm ban đầu chỉ lặp trên TỪNG event có sẵn,
mù với sự vắng mặt của cả **tập** — `events=[]`, walk bị cắt (thiếu `llm-step`), trùng node,
hoặc sai thứ tự đều lọt qua và in "OK" (đúng chế độ hỏng `trace-event.v0.md` §4.2a cảnh báo:
"thiếu một event ở giữa nguy hiểm hơn hỏng hẳn", "trùng cũng là sai"). Vá bằng
`_EXPECTED_WALK` — so khớp tuple `node_type` thật với chuỗi kỳ vọng
`(kb-retrieve, llm-step, condition, end)` TRƯỚC vòng lặp per-event. Probe lại 4 case (rỗng,
thiếu `llm-step`, trùng `llm-step`, sai thứ tự) — cả 4 bị `SCHEMA FAIL` + `sys.exit(1)` đúng
như kỳ vọng; events thật vẫn `schema OK`.

## Kết luận

- **4/6 node type** (`kb-retrieve`, `llm-step`, `condition`, `end`) dispatch đúng qua
  `interpreter.run()` khi ghép với KB thật (`StaticKbSearch`, không phải double test-local) —
  batch này KHÔNG dựng `tool-call`/`hitl-pause` (recipe của script chỉ 4 node, cùng hình D14's
  `test_condition_dag_e2e.py`). Bằng chứng "6 executor" đầy đủ (gồm `tool-call`/`hitl-pause`)
  vẫn là D14's grid harness với double test-local, không phải batch KB-thật hôm nay — số đo ở
  đây thu hẹp DoD #101 xuống đúng phạm vi đã chạy: 4 node type, KB thật.
- Trace đúng schema `trace-event.v0.md`: walk đúng chuỗi kỳ vọng (không thiếu/trùng node, I-1),
  `node_type` ∈ 6 giá trị đóng, `ts` parse được, `inputs_hash`/`outputs` có mặt mọi event.
- Citations đúng C-1: chỉ `llm-step` mang `citations`, nguồn grounded thật từ chunk `kb-retrieve`
  vừa truy xuất — không phải marker rỗng, không phải chunk giả.

## Giới hạn

1. **`StaticKbSearch` không phải Postgres** — lọc `tenant_id`/`section_roles` bằng so sánh
   Python thuần trên 42 chunk nạp sẵn trong bộ nhớ (`load_callisto()`), không qua RLS thật
   (`FORCE ROW LEVEL SECURITY` + `WITH CHECK` trên `kb.chunks`). `PgKbSearch` (Postgres +
   pgvector, fail-closed thật) chưa nối vào interpreter (DE tự ghi "CHƯA NỐI VÀO ĐÂU" —
   `postgres.py:2-6`) — số đo ở đây KHÔNG chứng minh gì về fence RLS thật, chỉ chứng minh
   interpreter/executor thread đúng dữ liệu qua đúng seam Protocol.
2. **1 query, 1 tenant** — chỉ chạy 1 case (`SC-01` của `smoke-5.yaml`) để có kết quả retrieval
   ổn định tất định; không phải chạy toàn bộ golden-set qua batch (đó là phạm vi evalhub/AIE-2,
   ngoài DoD #101 của AIE-1 hôm nay).
3. **`_CitingLLM` là double, không phải gateway LLM thật** — nó trích lại chunk_id có sẵn trong
   prompt, không tự suy luận câu trả lời. Đủ để chứng minh threading citations đúng seam, không
   chứng minh chất lượng câu trả lời của một model thật.
4. **`section_roles` đến từ recipe (`node.params`), không từ session** — `interpreter.run()`
   chỉ inject `tenant_id` server-side vào `kb-retrieve` (INV-1 Tenant-Wall); `section_roles`
   script này khai tay trong `_build_recipe()` (`["public"]`), giống mọi recipe D14 hiện có.
   Đây không phải hoãn-tuỳ-ý — đã có món nợ mở với ID: `docs/backlog.yaml`
   **FENCE-SEAM-1** (P1, debt) — "the real KbSearch impl (Day 4/5) must resolve
   `section_roles` server-side, not trust `node.params`, to avoid T6 label-spoof". Script
   này không đóng món nợ đó, chỉ tái xác nhận nó còn mở.
