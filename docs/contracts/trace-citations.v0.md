---
id: studio.contract.trace-citations.v0
type: contract-clause
owner: AIE-1 — Trần Bá Đạt (@TranBaDat2607)
status: freeze-ready
freeze: FREEZE-READY   # chờ 4/4 chữ ký — workshop #84, D11
freeze_target: D11
scope: điều kiện SẢN XUẤT `TraceEvent.citations` từ `studio_engine.interpreter.run()`
answers: evalhub `scorecard.v1.md` §6 (⏳ chờ AIE-1) · contracts#2 (SWE)
---

# 🖊️ `TraceEvent.citations` — CLAUSE SẢN XUẤT (C-1)

> **Đây không phải hợp đồng thứ 5.** `TraceEvent` là hợp đồng của **DE** (`trace-event.v0.md`), field
> `citations` khai ở `studio_contracts.trace:38`. File này chỉ ghi **một điều mà bên sản xuất phải bảo
> đảm** và bên tiêu thụ đang dựa vào nhưng chưa có ai viết ra. Bút của điều đó là AIE-1, vì `interpreter`
> là nơi duy nhất điền field này.
>
> Sinh ra để trả đúng hai câu hỏi đang mở: **evalhub `scorecard.v1.md` §6** (*"carrier `citations` chỉ
> trên `llm-step` — chờ AIE-1, hành vi đã đúng, clause chưa có"*) và **contracts#2** (SWE viết một câu
> cùng nội dung vào docstring `trace.py`, nhưng lúc viết thì code chưa thi hành nó — xem §4).

## 1. Clause

> **C-1 — chỉ `llm-step` mang `citations`.** `TraceEvent.citations` chỉ được điền cho event của node
> `node_type == NodeType.LLM_STEP`. Event của **mọi** node khác (`kb-retrieve`, `condition`,
> `tool-call`, `hitl-pause`, `end`) PHẢI mang `citations = None`, **bất kể** output của node đó có key
> `"citations"` hay không.
>
> **C-1a — chặn, không xoá.** Nếu một node không-phải-`llm-step` trả về key `"citations"`, giá trị đó
> vẫn được giữ nguyên trong `TraceEvent.outputs`; nó chỉ **không** được nâng lên field `citations`.
> Không mất dữ liệu, không ném lỗi, và vẫn truy được khi cần điều tra.
>
> **C-1b — nghĩa của field.** `citations` là **những chunk câu trả lời thật sự dựa vào**
> (*grounded*), KHÔNG phải toàn bộ chunk đã truy xuất (*retrieved*). Phần đã truy xuất sống ở
> `outputs["chunks"]` của event `kb-retrieve`.

## 2. Cơ chế — vì sao C-1 là CẤU TRÚC, không phải thói quen

`interpreter.py:304` cổng theo `node_type`:

```python
raw_citations = raw_outputs.get("citations") if node_type is NodeType.LLM_STEP else None
```

Khoá bằng `tests/test_trace_event_emission.py::test_c1_chi_llm_step_duoc_mang_citations`: giả một
`ToolDispatch` trả `{"citations": [...]}`, chạy recipe 4 node, assert event `tool-call` có
`citations is None` **và** `outputs["citations"]` còn nguyên. Gỡ cổng ⇒ bài đỏ (đã đo).

**Trước D11 điều này KHÔNG được bảo đảm.** Chỗ đó rẽ theo **hình dạng output** (`isinstance(output,
list)`), không theo loại node: mọi node trả `dict` đều được nhấc `citations`. "Chỉ `llm-step`" khi đó
là **hành vi tình cờ đúng** vì hôm nay chỉ `LlmStepExecutor` đặt key ấy.

Hai bằng chứng cho thấy nó không phải lo xa:

1. **Lỗ có thật, ở một seam ngoài.** `ToolCallExecutor.execute` trả **thẳng** dict của
   `ToolDispatch.dispatch()` (`executors.py:576`). `ToolDispatch` là Protocol — tool do bên khác viết.
   Một tool đặt key `"citations"` là chuyện bình thường, không cần ác ý, và trước C-1 nó đi thẳng vào
   trace như trích dẫn thật.
2. **Không lớp nào bên tiêu thụ bắt được.** Mutation **M1** của @dholmes0207
   (`kit:docs/mutations/s2/dholmes0207-into-engine.md`) sửa interpreter cho `kb-retrieve` phát
   citations ⇒ **evalhub 42 passed, xanh**. Tức bảo đảm này bắt buộc phải sống ở **bên sản xuất**;
   đặt lưới ở bên chấm thì không có gì để bắt.

## 3. Vỡ C-1 thì hỏng thế nào

Hỏng **im lặng**, không exception, không log:

| Ai | Hỏng ra sao |
|---|---|
| `citation_accuracy` (evalhub) | Marker không có căn cứ được tính là trích dẫn thật ⇒ **điểm giả cao hơn thực tế**. Trên một bài kiểm hàng rào, xanh-giả nguy hiểm hơn đỏ-giả. |
| `refused = has_kb_upstream and not citations` (`executors.py`, engine#37) | Đọc ngược: một câu từ chối kèm output của tool có key `citations` thành `refused=False`. |
| Reader trace (DE) | `trace-event.v0.md` §4.2a bảo reader so theo `node_type`; C-1 là thứ làm cho phép so đó có nghĩa. |

## 4. Quan hệ với `contracts#2` (SWE)

`contracts#2` thêm vào docstring `studio_contracts.trace.TraceEvent.citations` câu
*"set by the `llm-step` node's event only; `kb-retrieve`'s own event must always leave this `None`"*.

**Nội dung đúng, thời điểm sai.** Lúc PR đó viết, code **chưa** thi hành điều nó khẳng định — nó mô tả
một hành vi tình cờ như thể là bảo đảm, đúng loại lỗi mà `_WALK_ORDER` gây ra cho `trace-event.v0.md`.
Từ khi C-1 land thì câu đó **thành đúng**, và AIE-1 ký được.

Hai chỉnh nhỏ đề nghị cho `contracts#2` trước khi merge:

- *"`kb-retrieve`'s own event"* → **mọi** node không-phải-`llm-step`, không chỉ `kb-retrieve`. Lỗ thật
  nằm ở `tool-call` (seam ngoài), không ở `kb-retrieve` (executor nội bộ, trả `list`).
- Thêm một câu C-1a: giá trị bị chặn vẫn còn trong `outputs` — nếu không nói, người sau sẽ "dọn" bằng
  cách xoá hẳn key và làm mất dữ liệu điều tra.

## 5. Cho evalhub `scorecard.v1.md` §6 — câu đề nghị dán thẳng

> **§6 — Carrier của `citations`. ✅ ĐÃ CÓ CLAUSE (AIE-1, `engine:docs/contracts/trace-citations.v0.md` C-1).**
> Bên sản xuất bảo đảm `TraceEvent.citations` chỉ được điền cho `node_type == LLM_STEP`; mọi node khác
> luôn `None`, kể cả khi output của chúng có key `"citations"` (giá trị đó ở lại trong `outputs`). Bảo
> đảm là **cấu trúc** — cổng `interpreter.py:304` + bài khoá
> `test_c1_chi_llm_step_duoc_mang_citations`, đã đo là đỏ khi gỡ cổng.
> ⇒ `citations_from_trace` (`harness.py:85-89`) gom node-agnostic **không còn là rủi ro**: nguồn đã
> được siết ở đầu phát. Lưới đỡ phía evalhub (lọc theo `node_type`) trở thành **tuỳ chọn**, không còn
> bắt buộc.

## 6. Đổi C-1 sau freeze

Cần mini-RFC + 4/4 chữ ký + decision-log (umbrella §3 · D-12 · INV-5). Lý do siết chặt: đây là clause
mà **cả ba** quadrant còn lại đang dựa vào — evalhub chấm điểm, kb đọc trace, workbench hiển thị.

## 7. Chữ ký

| Vai | Người | Trạng thái |
|---|---|---|
| AIE-1 (bút) | @TranBaDat2607 | tác giả |
| DE (bút `trace-event`) | @DongAnh2704 | ⬜ |
| SWE (tác giả `contracts#2`) | @Dozyboy | ⬜ |
| AIE-2 (bên tiêu thụ, người xin clause) | @dholmes0207 | ⬜ |

Chữ ký thật = **Approve trên engine#15** (ADR-D11-01 lớp 1, đang PROPOSED).

## 8. Changelog

| Bản | Ngày | Đổi gì |
|---|---|---|
| **freeze-ready** | 2026-08-03 (D11, #81) | Bản đầu. Biến "chỉ `llm-step` mang citations" từ **hành vi tình cờ** thành **bảo đảm có cổng + có bài khoá**. Trả lời `scorecard.v1.md` §6 và làm cho câu chữ của `contracts#2` thành đúng. Không đổi field/kiểu ở `studio_contracts` ⇒ theo ADR-D11-01 nhánh (a), không cần PR vào `contracts`. |
