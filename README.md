# agentcore-studio-engine

> Interpreter + 6 node executors + fence-EXECUTOR. Stateless (không schema DB).

**Owner:** AIE-1 — Trần Bá Đạt · **Loại:** uv workspace member (Python 3.14) · **Repo cha:** [agentcore-studio-kit](https://github.com/AI20K-VGR/agentcore-studio-kit)

## Repo này là gì
Submodule `packages/engine` của workspace `agentcore-studio-kit`. Owner: **AIE-1 — Trần Bá Đạt**. Chứa interpreter + node executors + closed-set node type. Stateless — không giữ schema DB.

## ⚠️ Không build/test độc lập được
`agentcore-studio-engine` phụ thuộc `agentcore-studio-contracts` + uv.lock của repo cha. Stateless nên **không cần DB**, nhưng vẫn cần workspace để resolve dependency. Vì vậy:
- **Làm việc qua repo cha:** `git clone --recursive git@github.com:AI20K-VGR/agentcore-studio-kit.git`, rồi `cd packages/engine` để sửa / commit / push chính repo này.
- **Test đầy đủ:** đẩy PR → CI tự **dựng lại full workspace** rồi chạy `pytest packages/engine/tests` (Phương án B).

## CI
`.github/workflows/ci.yml` chỉ là **stub** gọi reusable workflow chung ở repo cha:
`AI20K-VGR/agentcore-studio-kit/.github/workflows/reusable-domain-ci.yml@main`.
Muốn đổi quy trình CI thì sửa ở repo cha (1 chỗ).

## Fixture format — VCR-style cho `llm-step` (Day 2, R-SPEC A2)

Quyết định hôm nay: **chỉ chốt hình dạng file**, chưa build engine ghi/phát (đúng scope Day 2 —
xem `tom-tat-de-bai.md` mục 4 ở repo cha). Engine ghi/phát thật là việc của ngày sau.

> **Cập nhật Day 3**: nhánh **phát** (replay) đã có — `studio_engine.demo_stubs.FixtureLLM` đọc
> đúng file theo format dưới đây, trả `response` cho `LLM.complete` (dùng trong
> `LlmStepExecutor`/CLI demo, xem mục dưới). Nhánh **ghi** (record fixture từ 1 lời gọi thật) vẫn
> chưa build.

**Vị trí:** `tests/fixtures/llm_step/<case_id>.json` — 1 file = 1 lời gọi `LLM.complete` trong 1
test-case (pipeline hardcode Day 3 chỉ có đúng 1 node `llm-step`/case nên 1-file-1-case là đủ; đã
có sẵn field `node_id` để phân biệt nếu sau này 1 case cần nhiều lời gọi LLM).

**Hình dạng** (xem ví dụ `tests/fixtures/llm_step/smoke-01.json`):

```json
{
  "case_id": "smoke-01",
  "node_id": "n2",
  "request": { "prompt": "<toàn bộ prompt gửi cho LLM.complete>", "kwargs": {} },
  "response": "<chuỗi hoàn chỉnh LLM.complete trả về>"
}
```

`request`/`response` map thẳng 1:1 vào `LLM.complete(prompt: str, **kwargs: object) -> str`
(`studio_contracts.protocols.LLM`) — không field thừa, không cần lớp dịch khi build fixture-backed
`LLM` impl thật. `response` là chuỗi thô (không tách `citations` riêng) vì việc trích `chunk_id` từ
text ra `TraceEvent.citations` là việc của executor (`executors.py::LlmStepExecutor`), không phải
của fixture — tách citation thành field riêng sẽ ghi sai cái model thật trả về.

**100% synthetic** (luật NDA) — dùng tenant giả `ankor` (đã dùng xuyên suốt umbrella-contract),
không tên người/công ty thật.

## CLI demo (Day 3)

`python -m studio_engine` chạy 1 recipe synthetic 4-node (`kb-retrieve → llm-step → tool-call →
end`, `studio_engine.__main__.build_demo_recipe()`) qua `interpreter.run()`, wire với 4 collaborator
demo-only trong `studio_engine.demo_stubs` (`EmptyKbSearch`/`FixtureLLM`/`EmptyEmbedding`/
`WhitelistToolDispatch`), in `final_state` ra JSON. Đây là walking-skeleton demo — không phải
composition thật (`apps/studio` thay bằng collaborator production sau, xem docstring
`demo_stubs.py`). 4/6 node-executor (`KbRetrieveExecutor`/`LlmStepExecutor`/`ToolCallExecutor`/
`EndExecutor`) đã điền thân; `ConditionExecutor`/`HitlPauseExecutor` vẫn `NotImplementedError`
(ngoài scope Day 3).

## Quy tắc
- Chỉ đụng file trong `packages/engine/**` (fence-lane của bạn) — không sửa surface domain khác.
- Node type là closed-set — thêm loại node phải qua contract (mentor-approval).
- Đổi contract → sang repo `agentcore-studio-contracts` (mentor-approval).
- Không commit tài liệu mentor/rubric/answer-key (pre-commit `nda-denylist` chặn).

📖 Phân quyền + luồng thao tác đầy đủ: [GITFLOWS.md](https://github.com/AI20K-VGR/agentcore-studio-kit/blob/main/GITFLOWS.md)
