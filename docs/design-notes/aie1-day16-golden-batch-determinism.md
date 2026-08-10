# D16 — batch 30 case golden-set qua interpreter thật + chứng minh lặp lại (N=1 → N=30)

AIE-1, D16 #106 (`plans/260810-1501-d16-aie1-golden-batch-determinism`, phase 1+2).

## Tóm tắt

D15 (`run_real_batch.py`) chứng minh 1 query thật đi hết `kb-retrieve -> llm-step -> condition ->
end` qua `interpreter.run()` với `StaticKbSearch` thật. Phase này nâng từ **N=1 lên N=30**: chạy
toàn bộ golden-set (`packages/kb/golden/callisto-handbook-30-draft.yaml`, 22 case dương + 8 case
refusal) qua walk `kb-retrieve -> llm-step -> end` (bỏ `condition`, không cần định tuyến ở đây, chỉ
cần đọc `TraceEvent` thật ra để đối chiếu nhãn), rồi generalize đúng 3 khẳng định determinism của
D9 (`test_interpreter_determinism.py`, N=1) lên N=30 case. Double `_GoldenAwareLLM` chỉ trích lại
giao `bracket_ids_thật_trong_prompt ∩ expected_citation` — không đọc thẳng nhãn YAML rồi gán ngược
(assertion Goodhart-closing khoá walk 3-event thật cho mọi case, kể cả case refusal).

Không sửa `executors.py`/`interpreter.py` (D-3, script read-only với engine, cùng ràng buộc D15).

## Lệnh tái lập

```
uv run python packages/engine/scripts/run_golden_batch.py
```

Chạy từ **kit root** (`agentcore-studio-kit/`) — cùng lý do D15: `uv run` resolve `studio_kb` qua
uv-workspace, script tự guard bằng kiểm file tồn tại trước khi import, in thông báo rõ nếu chạy sai
chỗ (không chạy standalone được trong repo con `agentcore-studio-engine`).

Test: `uv run --package agentcore-studio-engine pytest packages/engine/tests/test_golden_batch_correctness.py packages/engine/tests/test_golden_batch_determinism.py -q`

## Kết quả đo thật

- **30/30 case khớp nhãn golden** (22 dương + 8 refusal) — `run_golden_batch.py` exit 0, in
  `"30/30 case khớp nhãn golden"`.
- Mỗi case: walk đúng 3 `TraceEvent` (`kb-retrieve -> llm-step -> end`), không thiếu không trùng —
  khoá bằng assertion Goodhart-closing (`test_every_outcome_walk_is_real_kb_retrieve_llm_step_end`)
  chặn một implementation giả đọc thẳng `expected_citation` từ YAML rồi gán ngược vào
  `outcome.citations` mà không hề gọi `interpreter.run()`.
- **Determinism generalize N=1 → N=30** (`test_golden_batch_determinism.py`, gọi
  `run_all_cases()` hai lần độc lập, mỗi lần tự dựng lại `StaticKbSearch()`/`_GoldenAwareLLM`
  mới — không share instance giữa 2 lần, cùng kỷ luật D9):
  - Nội dung 90 event (30 case × 3 event/case) của 2 lần chạy giống hệt nhau, trừ 3 field
    tươi-theo-thiết-kế (`run_id`/`event_id`/`ts`).
  - 3 field đó KHÔNG trùng giữa 2 lần chạy (30 `run_id` × 2 lần = 60 giá trị duy nhất, 90 `event_id`
    × 2 lần = 180 giá trị duy nhất; `ts` tăng dần nghiêm ngặt trong mỗi lần chạy) — chặn bug
    cached-run.
  - Cardinality **22 case dương có citations / 8 case refusal citations rỗng** pin cứng ở CẢ HAI
    lần chạy độc lập, không chỉ so `==` suông giữa 2 lần (chặn một implementation thoái hoá
    trả cùng-sai ở cả 2 lần).
- `139 passed` sau phase 1 (133 baseline D15 + 6 mới), `142 passed` sau phase 2 (+3), `142 passed`
  không đổi sau vòng sửa review W1-3 (không đổi hành vi 30/30). `ruff check` + `mypy` sạch ở mọi
  bước.

## Giới hạn

1. **Đường refusal (8 case) không khả-phủ-chứng bằng `_GoldenAwareLLM`** (ghi lại nguyên văn từ
   docstring double, phát hiện ở review D16 W1): `expected_citation=[]` cho case refusal ⇒
   `self._expected` rỗng ⇒ double LUÔN trả không trích dẫn, BẤT KỂ `StaticKbSearch` có thật sự
   trả về chunk nào hay không (probe review đo được: cả 8/8 case refusal đều retrieve non-empty
   ở token-overlap ranker thô). `30/30 khớp nhãn` là bằng chứng ĐÚNG cho 22 case dương (retrieval
   thật phải tìm đúng chunk); với 8 case refusal nó chỉ xác nhận double trung thực (không bịa
   citation), KHÔNG phải bằng chứng độc lập cho hành vi fence/leak-filtering của `StaticKbSearch`
   trên các case đó. Test fence/leak-detection thật ngoài scope AIE-1 hôm nay
   (`DEC-D16-06`, LLM-judge/`match_mode` hoãn D18).
2. **Determinism đo trong-process, không xuyên-process** — cùng lỗ đã biết từ D9 ("determinism mới
   đo trong-process"); batch runner ở đây gọi `run_all_cases()` 2 lần trong CÙNG một tiến trình
   test, không dựng 2 tiến trình `uv run python` riêng biệt rồi so sánh. Ngoài scope hôm nay.
3. **Không đo `condition`/`tool-call`/`hitl-pause`** — walk batch này chỉ 3/6 node-type
   (`kb-retrieve`, `llm-step`, `end`); recipe của D16 cố tình bỏ `condition` vì không cần định
   tuyến để đối chiếu nhãn. Bằng chứng đủ 6 executor vẫn là D14's grid harness.
4. **Kế thừa nguyên các giới hạn của D15** (`aie1-day15-real-batch.md` §Giới hạn) không đổi ở D16:
   `StaticKbSearch` không phải Postgres/RLS thật; `_GoldenAwareLLM` là double trích lại, không phải
   gateway LLM thật; `section_roles` đến từ recipe (`_build_recipe`), không từ session — nợ mở
   `docs/backlog.yaml` **FENCE-SEAM-1** vẫn còn nguyên, D16 không đóng nợ đó, chỉ tái xác nhận nó
   còn mở trên quy mô N=30 thay vì N=1.
5. **Golden-set là input READ-only** — `callisto-handbook-30-draft.yaml` (PR #18 kb đổi tên thành
   `callisto-golden-30-v1.yaml`, chưa merge tại thời điểm đo, nội dung 30 case không đổi) — script
   đọc path qua 1 hằng số `_GOLDEN_SET_PATH` duy nhất, dễ đổi nếu tên file đổi trước khi merge.
