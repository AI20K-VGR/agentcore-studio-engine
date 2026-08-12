# D19 — failure-mode `kb-retrieve` ở tầng engine (đã có, ghi lại; + 2 honest-TODO)

AIE-1, D19 #121 (`plans/260812-1133-d18-d20-aie1-engine-daily-prs/phases/
phase-2-d19-tokens-idempotent-retrieval-failure-modes.md`). Việc con thứ 3 của
issue kit#121: liệt kê cách `KbRetrieveExecutor` có thể hỏng ở tầng engine —
docs-only, không đổi code ở đây. 2 mục đầu là hành vi ĐÃ implement (ghi lại,
không phải case mới); 2 mục sau là gap thật, ghi rõ "chưa xử lý"/"chưa phân
biệt được" chứ không ngụy trang thành đã đóng.

## 1. `tenant_id` không phải `UUID` thật → fail-closed `PermissionError`

`packages/engine/src/studio_engine/executors.py:170-178`. `KbRetrieveExecutor.execute()`
raise `PermissionError` khi `node.params["tenant_id"]` (sau khi
`interpreter.run()` đã ghi đè bằng `session_context.tenant_id`, INV-1) vẫn
không phải một `UUID` thật — thiếu hẳn, hoặc một slug string kiểu `"ankor"`.
Đây là defense-in-depth: dispatch bình thường qua `interpreter.run()` không
bao giờ chạm raise này; nó chỉ nổ nếu executor bị construct và gọi trực tiếp,
bỏ qua session fence. Error message chỉ báo `type(raw_tenant_id).__name__`,
không bao giờ echo giá trị thật (dữ liệu client-declared chưa validate).
Test khoá: `packages/engine/tests/test_executors_behavior.py::
test_kb_retrieve_raises_when_tenant_id_absent` +
`::test_kb_retrieve_raises_when_tenant_id_is_slug_not_uuid`.

## 2. `section_roles` rỗng/malformed → deny-all `[]`, không phải wildcard

`packages/engine/src/studio_engine/executors.py:134-141` (docstring) và
`:181` (`section_roles = [str(role) for role in raw_roles] if isinstance(raw_roles, list) else []`).
Khác với `tenant_id`, không có nhánh raise cho `section_roles` — một
`section_roles` thiếu/malformed (không phải `list`) đã tự nhiên đọc thành
"không role nào khớp" ở tầng retrieval thật
(`static_search.py`: `allowed = set(section_roles)`, tập rỗng không khớp gì
— tham chiếu tại `executors.py:140`), nên `[]` deny-all chính là giá trị
fail-closed đúng, không cần raise. Test khoá: `packages/engine/tests/
test_section_roles_server_resolve.py::test_missing_section_roles_param_fails_closed_to_empty_list`
(line 144) + `::test_non_list_section_roles_at_executor_boundary_is_not_coerced`
(line 162) — cả hai đều pin trực tiếp nhánh executor-boundary này.

## 3. `KbSearch.search()` raise — CHƯA được bắt ở tầng dispatch (honest gap)

`packages/engine/src/studio_engine/interpreter.py:375`:
`output = await executors[node_type].execute(node)` — KHÔNG có `try/except`
bao quanh lời gọi này. Nếu `KbSearch.search(...)` (fence-DATA, DE-owned; seam
chính thức `KbSearchService.search()` — `packages/kb/src/studio_kb/search.py`
— đã un-ratchet ở D17, `kb#19`: không còn raise `NotImplementedError`, nay uỷ
quyền một dòng sang `PgKbSearch.search` (`postgres.py`), impl fenced retrieval
thật đã nằm trên spine từ D13, không phải từ D17) raise bất kỳ exception nào
(network lỗi, DB lỗi, timeout, v.v.), exception đó thoát
thẳng ra khỏi `interpreter.run()`'s walk loop, giết cả run giữa chừng — kể cả
sau khi các node trước đó đã ghi `TraceEvent` thật (chính cái
"truncated-run hazard" mà `ConditionExecutor`'s docstring đã cảnh báo ở
`executors.py:462-471` cho một node khác, nhưng CHƯA có cơ chế tương tự cho
`kb-retrieve`). Nói thẳng: đây là **chưa xử lý**, không phải "đã xử lý theo
thiết kế" — không có test nào trong `packages/engine/tests/` khoá hành vi
fail-closed/retry/surface-lỗi cho case này hôm nay. Việc quyết định retry vs.
fail-closed vs. surface-partial-run là một quyết định thiết kế còn mở, ngoài
scope D19.

## 4. `[]` hợp lệ (fence khớp đúng, không có gì trong phạm vi) vs. `[]` do bug — engine không phân biệt được (honest-TODO)

Theo hợp đồng `packages/kb/docs/contracts/kb-search.v0.md` §6.1
(`kb-search.v0.md:210-238`): `[]` là **kết quả hợp lệ**, không phải lỗi — node
`kb-retrieve` nhận `[]` thì đi tiếp sang `llm-step` bình thường, không raise,
không dừng chuỗi. Nhưng `KbRetrieveExecutor.execute()` (`executors.py:130-183`)
trả `[]` y hệt nhau cho CẢ HAI trường hợp:

- fence khớp đúng và thật sự không có chunk nào trong phạm vi tenant/role đó
  (case hợp lệ, §6.1), và
- một bug ở tầng `KbSearch` thật (query builder sai, index rỗng do lỗi
  seed/migration, v.v.) tình cờ cũng trả `[]`.

Tầng engine hôm nay KHÔNG có tín hiệu nào để tách hai case này — `[]` là
`[]`, executor pass-through nguyên văn (fence-EXECUTOR duty, không được
filter/widen post-hoc). Đây là honest-TODO, không phải case đã đóng: một
cơ chế phân biệt (ví dụ DE's `KbSearch` trả kèm metadata "0 match vs.
lỗi-nội-bộ", hoặc một tín hiệu riêng ở tầng contract) là việc thiết kế còn
mở, ngoài scope D19 và ngoài quyền sửa của AIE-1 hôm nay (`packages/kb` là
DE's write scope, không phải `packages/engine`).
