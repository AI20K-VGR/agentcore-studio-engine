# Descope-ladder — AIE-1 (Trần Bá Đạt)

Bản riêng của AIE-1, viết theo quyết định của team ở Day 2: mỗi người tự viết descope cho mảng
việc mình sở hữu thay vì 1 file `DESCOPE.md` chung cho cả 4 quadrant (xem
`docs/reports/daily-notes/2026-07-21-TranBaDat2607.md`). File này chỉ bao phủ mảng AIE-1 sở hữu:
**interpreter + node-executor + chất lượng truy xuất (chunking × embedding)**.

Nguyên tắc chung (umbrella-contract, không đổi): khi kẹt thời gian, cắt **đúng theo thứ tự dưới
đây**, không tự bịa cách cắt khác. Mỗi lần cắt, bản demo vẫn phải **chạy được** — chỉ kém đầy đủ
hơn, không được đứt gãy walking-skeleton.

## Nấc 1 — Retrieval quality đo bằng số (chunking × embedding trade-off)

- **Đủ bản (S2, Day 14 trở đi):** chạy nhiều tổ hợp chunk-size × embedding-model thật, đo số
  (recall/precision hoặc proxy tương đương) trên golden-set, có bảng so sánh.
- **Cắt xuống:** so sánh định tính trên 1-2 cấu hình cố định, không chạy grid nhiều tổ hợp; ghi
  rõ "chưa đo số" thay vì báo cáo số giả.
- **Trạng thái hiện tại:** chưa tới lượt — nằm ở S2 (Day 13-14), Day 3 chưa động tới.

## Nấc 2 — `EmbeddingService` thật (nhiều provider / batching thật)

- **Đủ bản (Day 7):** `EmbeddingService` Protocol có impl thật (gateway-stub client, batching,
  nhiều provider nếu cần).
- **Cắt xuống:** `StubEmbedding` — trả vector giả cố định/deterministic, đủ để executor gọi được
  qua đúng Protocol mà không cần model thật.
- **Trạng thái hiện tại:** chưa tới lượt (Day 7). Day 3 `LlmStepExecutor` đã nhận `embedding` qua
  constructor-DI nhưng **chưa gọi** — recipe demo chưa yêu cầu bước embed nào.

## Nấc 3 — Node-executor đủ cả 6 loại (`condition`, `hitl-pause` chạy thật)

- **Đủ bản (S1 cuối / S2):** cả 6 node-type (`kb-retrieve`, `llm-step`, `condition`, `tool-call`,
  `hitl-pause`, `end`) có thân `execute()` thật, không còn `NotImplementedError`.
- **Cắt xuống (Day 3, ĐANG Ở NẤC NÀY):** chỉ điền thân 4/6 executor
  (`kb-retrieve`/`llm-step`/`tool-call`/`end`); `ConditionExecutor`/`HitlPauseExecutor` giữ nguyên
  `NotImplementedError` có chủ đích — khóa bằng
  `tests/test_executors_behavior.py::test_condition_hitl_still_not_implemented`, không phải bỏ
  sót.
- **Trạng thái hiện tại:** **đã cắt, đúng kế hoạch** — PR #2 (`agentcore-studio-engine`,
  `day3/interpreter-3node`) chỉ điền 4/6, ghi rõ 2 executor còn lại trong docstring + test khóa.

## Nấc 4 — Interpreter đọc `dag` động từ recipe (factory/DI-container generic)

- **Đủ bản (Day 6, issue #27):** `interpreter.run()` đọc `recipe.dag.nodes`/`edges` động, dispatch
  qua `registry.get_executor_class(node.type)` như 1 factory thật, rẽ nhánh theo `edges[].when`.
- **Cắt xuống (Day 3, ĐANG Ở NẤC NÀY):** chain 4 node **hardcode tường minh** trong `run()`
  (`kb-retrieve → llm-step → tool-call → end`); `registry.py` chỉ giữ vai closed-set guard (khóa
  bởi `test_registry_has_exactly_six`), KHÔNG dựng thành DI-container generic.
- **Trạng thái hiện tại:** **đã cắt, đúng kế hoạch** — quyết định ghi rõ trong plan
  `260722-0956-day3-interpreter-3node` ("Executor DI"), tự chống anti-pattern "làm sớm việc Day
  6" thay vì bị mentor chặn giữa chừng.

## Luật KHÔNG được phá dù cắt tới nấc nào

- 6 node-type là trần — không thêm loại thứ 7, không viết DSL tự do, không import
  LangGraph/Camunda (umbrella-contract, khóa bởi `test_node_type_closed.py`).
- Fence-EXECUTOR (`kb-retrieve`): dù cắt tới nấc nào, executor không bao giờ được tự ý mở rộng
  `section_roles` hay lấy chunk vượt scope rồi nhờ LLM lọc sau ("fake fence").
- Fixtures-first: mọi node `llm-step` chạy qua fixture VCR-style đã chốt Day 2
  (`tests/fixtures/llm_step/`), không gọi model thật trong CI.

## Tóm tắt trạng thái Day 3 (hôm nay)

| Nấc | Đủ bản | Đang ở | Ghi chú |
|---|---|---|---|
| 1. Retrieval quality đo số | S2/Day 14 | chưa tới | ngoài scope Day 3 |
| 2. EmbeddingService thật | Day 7 | chưa tới | constructor đã wire, chưa gọi |
| 3. Đủ 6 node-executor | S1 cuối/S2 | **cắt xuống 4/6** | đúng kế hoạch, khóa bằng test |
| 4. Interpreter đọc `dag` động | Day 6 | **cắt xuống hardcode** | đúng kế hoạch, khóa bằng test |
