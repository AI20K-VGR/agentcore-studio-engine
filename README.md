# agentcore-studio-engine

> Interpreter + 6 node executors + fence-EXECUTOR. Stateless (không schema DB).

**Owner:** AIE-1 — Trần Bá Đạt · **Loại:** uv workspace member (Python 3.14) · **Repo cha:** [agentcore-studio-kit](https://github.com/hieubui2409/agentcore-studio-kit)

## Repo này là gì
Submodule `packages/engine` của workspace `agentcore-studio-kit`. Owner: **AIE-1 — Trần Bá Đạt**. Chứa interpreter + node executors + closed-set node type. Stateless — không giữ schema DB.

## ⚠️ Không build/test độc lập được
`agentcore-studio-engine` phụ thuộc `agentcore-studio-contracts` + uv.lock của repo cha. Stateless nên **không cần DB**, nhưng vẫn cần workspace để resolve dependency. Vì vậy:
- **Làm việc qua repo cha:** `git clone --recursive git@github.com:hieubui2409/agentcore-studio-kit.git`, rồi `cd packages/engine` để sửa / commit / push chính repo này.
- **Test đầy đủ:** đẩy PR → CI tự **dựng lại full workspace** rồi chạy `pytest packages/engine/tests` (Phương án B).

## CI
`.github/workflows/ci.yml` chỉ là **stub** gọi reusable workflow chung ở repo cha:
`hieubui2409/agentcore-studio-kit/.github/workflows/reusable-domain-ci.yml@main`.
Muốn đổi quy trình CI thì sửa ở repo cha (1 chỗ).

## Quy tắc
- Chỉ đụng file trong `packages/engine/**` (fence-lane của bạn) — không sửa surface domain khác.
- Node type là closed-set — thêm loại node phải qua contract (mentor-approval).
- Đổi contract → sang repo `agentcore-studio-contracts` (mentor-approval).
- Không commit tài liệu mentor/rubric/answer-key (pre-commit `nda-denylist` chặn).

📖 Phân quyền + luồng thao tác đầy đủ: [GITFLOWS.md](https://github.com/hieubui2409/agentcore-studio-kit/blob/main/GITFLOWS.md)
