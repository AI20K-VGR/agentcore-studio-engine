# Bằng chứng D9 — phần AIE-1 (engine)

> **Mục đích:** phần của AIE-1 trong evidence-pack chung (`day-09.md:31`, DoD `:52-54`). Viết theo
> chuẩn *"đủ để chấm **không cần hỏi**"* — mọi con số dưới đây tái lập được bằng lệnh trong §1, không
> phải tin lời khai.
>
> Người gom pack: lấy §1 (lệnh), §2 (số + bằng chứng có-răng), §3 (link PR), §4 (lỗ đã biết).

---

## 1. Chạy lại — khối lệnh dán-là-chạy

Từ **thư mục gốc của kit** (`agentcore-studio-kit/`), không cần Postgres (quadrant này không chạm
DB):

```bash
uv run pytest packages/engine -q                                              # 84 passed
uv run pytest packages/engine/tests/test_fixture_missing_fails_loud.py \
               packages/engine/tests/test_interpreter_determinism.py -v       # 15 passed
uv run ruff check packages/engine                                             # sạch
uv run mypy packages/engine                                                   # sạch, 20 file
```

Không dùng `uv` trong máy đo (không có sẵn) — chạy tương đương bằng venv đã dựng sẵn của kit
(`./.venv/Scripts/pytest.exe packages/engine -q`), cùng kết quả.

---

## 2. Số đo

### 2.1 Suite

| | |
|---|---|
| `pytest packages/engine -q` | **84 passed**, 0 failed, 0 skipped |
| 2 file Day 9 riêng (`test_fixture_missing_fails_loud.py` + `test_interpreter_determinism.py`) | **15 passed** (11 + 4) |
| `ruff check packages/engine` | sạch |
| `mypy packages/engine` | sạch, 20 source file |
| chống flaky (`test_interpreter_determinism.py`, 10 lần liên tiếp) | **4 passed cả 10 lần**, không có lần nào đỏ |

### 2.2 Test negative có răng — chứng minh bằng đột biến, không tin lời khai

Đề bài đòi *"negative: fixture thiếu → fail **rõ**, không nuốt lỗi"* và ràng buộc *"test negative
phải bắt lỗi thật (không phải test giả)"* (`day-09.md:38,45`). Đo bằng 2 đột biến thật, không đọc
code đoán:

**Đột biến 1 — phục hồi hành vi TRƯỚC Day 9** (`git checkout f229e22^ -- demo_stubs.py`, tức bản
chưa có `FixtureError`):

```
ImportError: cannot import name 'FixtureError' from 'studio_engine.demo_stubs'
1 error during collection
```

Toàn bộ file test không collect nổi — nghĩa là 11 test negative **phụ thuộc thật** vào việc
`FixtureError` tồn tại, không phải test rỗng ăn theo hành vi cũ.

**Đột biến 2 — raise `FixtureError` vô điều kiện** (mô phỏng một impl lười: "cứ thấy gọi là báo lỗi,
không cần đọc fixture thật") — đây là phép thử "test giả" kinh điển: một stub **luôn luôn raise** sẽ
lọt qua mọi test *chỉ* kiểm "có raise FixtureError không":

```
10 failed, 5 passed in 0.54s
```

10/15 đỏ, gồm cả 2 bài "răng dương" (`test_real_case_id_still_replays`,
`test_stub_embedding_still_replays_the_real_fixture`) và cả 4 bài determinism — chứng minh bộ test
**phân biệt được** "báo lỗi đúng lúc" với "báo lỗi lúc nào cũng được". Restore lại
(`git checkout HEAD -- demo_stubs.py`) → về nguyên **84 passed**.

### 2.3 Bảng đối chiếu 4 dạng fixture hỏng (từ `README.md`, đã verify từng dòng qua test thật)

| Dạng hỏng | Trước Day 9 | Từ Day 9 | Test pin |
|---|---|---|---|
| File không tồn tại | `FileNotFoundError`, absolute path máy local | `FixtureError`, nêu `case_id` + path repo-relative | `test_missing_llm_fixture_names_case_id_and_relative_path`, `test_missing_fixture_message_does_not_leak_absolute_host_path` |
| JSON không parse được | `json.JSONDecodeError` không kèm tên file | `FixtureError` nêu file + lý do parse | `test_unparseable_fixture_json_fails_loud` |
| Thiếu key `response` | `KeyError: 'response'` trần | `FixtureError` nêu file + key thiếu | `test_fixture_missing_response_key_fails_loud` |
| `response` sai kiểu (LLM) | **nuốt lỗi**: `str()` coerce dict → `"{'a': 1}"`, chạy tiếp | `FixtureError` nêu kiểu nhận được | `test_llm_response_of_wrong_type_fails_instead_of_str_coercion` |
| `response: []` (embedding) | `ZeroDivisionError`, không liên hệ gì tới fixture | `FixtureError` nêu lý do | `test_empty_embedding_response_fails_loud_not_zero_division` |
| Interpreter có nuốt lỗi không? | — | Không — `run()` không try/except; trace dừng đúng ở node cuối chạy xong (không có event `end`) | `test_interpreter_does_not_swallow_a_missing_fixture` |

### 2.4 Determinism — phát biểu chính xác, không phải slogan "chạy lại ra số giống nhau"

2 lần chạy cùng recipe phải **giống** ở mọi field mang nội dung (output từng node, `inputs_hash`,
`citations`, `tokens`, `cost`) và phải **khác** ở 3 field định danh/thời điểm sinh mới theo thiết kế
(`run_id`, `event_id`, `ts`). Cả hai chiều đều được assert (`test_run_identity_fields_are_fresh_per_run`)
— nếu chỉ assert chiều "giống" thì một bug cache-toàn-bộ-run (2 lần gọi trả cùng object) sẽ pass sai
lý do.

Cơ chế khiến số ổn định: `inputs_hash = sha256(json.dumps(node.params, sort_keys=True, default=str))`
(`interpreter.py:297`) — `sort_keys=True` + hash mật mã, không phụ thuộc thứ tự dict runtime hay
`PYTHONHASHSEED`. Đây là lý do bảng điểm evalhub (đọc `citations` từ trace) chạy lại ra cùng số
(DoD AIE-2, `:53-54`), vì trace nó đọc bắt nguồn từ đúng cơ chế này.

---

## 3. Link PR

| repo | PR | nội dung |
|---|---|---|
| `agentcore-studio-engine` | [#13](https://github.com/AI20K-VGR/agentcore-studio-engine/pull/13) | `day9/interpreter-fixture-determinism` — `FixtureError`, 15 test Day 9, doc hợp đồng lỗi — **merged**, HEAD `c9249d3` |

Nhánh nguồn 3 commit: `f229e22` (fix: gộp `FixtureError`), `9c52585` (test: pin determinism),
`84a071f` (docs: hợp đồng lỗi trong `README.md`).

---

## 4. Lỗ đã biết trong phần AIE-1 — khai ra, không giấu

- **Chỉ có 2 đột biến tay, không có mutation sweep tự động.** DE (`packages/kb`) có
  `mutation_check.py` (8 mutant khai trước) + `mutation_sweep.py` (93 mutant AST, quét mù). Quadrant
  AIE-1 chưa có script tương đương — 2 đột biến ở §2.2 chứng minh răng cho đúng 2 bug lịch sử
  (revert-fix và raise-vô-điều-kiện), không phải quét toàn diện `demo_stubs.py`/`interpreter.py`.
- **Determinism chỉ đo trong-process, chưa đo xuyên-process.** 2 lần gọi `_run()` trong cùng
  `pytest` process, cùng interpreter, cùng import. evalhub (AIE-2) ghi nhận đã đo "determinism xuyên
  process" cho phần của họ; engine chưa có test tương đương chạy CLI demo 2 lần ở 2 tiến trình riêng
  rồi so trace. Rủi ro thấp vì cơ chế `sort_keys=True` + sha256 không phụ thuộc process/hash-seed
  (§2.4), nhưng đây là suy luận từ đọc code, chưa phải số đo trực tiếp.
- **`FixtureError` kế thừa `RuntimeError` trần, không có subclass theo dạng hỏng.** Caller bắt được
  "fixture hỏng nói chung" bằng 1 `except FixtureError`, nhưng không phân biệt được lập trình "thiếu
  file" với "sai kiểu" bằng type — phải parse message. Đủ cho D9 (đề bài chỉ đòi "fail rõ, không nuốt
  lỗi"), nhưng nếu S2 cần caller xử lý khác nhau theo dạng hỏng thì đây là chỗ sẽ phải tách.
- **Case tenant-mismatch / INV-1 không nằm trong 2 file D9 này.** Nằm ở `test_session_context_tenant_wall.py`
  (Day 8, seam DE/SWE) — D9 của AIE-1 chỉ harden lớp fixture-replay, không đụng lại INV-1.
