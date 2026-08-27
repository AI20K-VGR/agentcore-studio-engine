"""Agent CÓ `kb_search` phải được bảo là **tra trước rồi mới nói không có**.

## Sự cố đã đo

Trên hệ thật, một lượt chấm 20 case ghi được **62 event `llm-step` và đúng 1 event `kb-retrieve`**:
model trả lời "Không có thông tin." ngay ở lượt đầu mà chưa từng tra cứu. Bảng điểm ra
`success_rate=0.00`, và nhìn từ giao diện thì giống hệt "KB chưa có tài liệu".

## Vì sao prompt cũ đẩy model tới đúng chỗ đó

`_GROUNDING_CONVENTION` dạy model **cách dùng** đoạn trích:

    Nếu các đoạn trích không chứa câu trả lời: nói rõ là không có thông tin

Ở lượt 1 chưa có đoạn trích nào, nên mệnh đề đó **đúng theo nghĩa đen** — và model tuân đúng nghĩa
đen. Không câu nào trong prompt bảo nó **đi lấy** đoạn trích trước.

`system_prompt` cũng không cứu được: từ `web#51` nó luôn rỗng, nên prompt của engine là mặt phẳng
duy nhất còn lại để khai hành vi này.
"""

from __future__ import annotations

import re

from studio_engine.agent_protocol import KB_SEARCH_TOOL, build_agent_prompt


def _prompt(tool_names: list[str]) -> str:
    return build_agent_prompt(
        system_prompt="",
        question="Nhân viên được bao nhiêu ngày phép năm?",
        tool_names=tool_names,
        observations=[],
    )


def test_kb_agent_is_told_to_search_before_answering() -> None:
    """Prompt phải gọi đích danh `kb_search` như bước BẮT BUỘC trước khi trả lời.

    Nêu đích danh tên tool chứ không nói chung chung "hãy dùng tool": catalog có nhiều tool, và một
    lời khuyên chung không nói được rằng câu hỏi về tài liệu thì phải tra KB chứ không phải gọi
    `calculator`."""
    text = _prompt([KB_SEARCH_TOOL, "calculator"])
    assert KB_SEARCH_TOOL in text
    assert re.search(rf"(phải|hãy|luôn)[^\n]*{KB_SEARCH_TOOL}", text, re.IGNORECASE), (
        f"prompt không có chỉ thị bắt buộc gọi {KB_SEARCH_TOOL} trước khi trả lời:\n{text}"
    )


def test_the_refusal_clause_is_conditioned_on_having_searched() -> None:
    """**Bài trung tâm.** Mệnh đề "nói không có thông tin" phải đi kèm điều kiện *đã tra rồi*.

    Đây là chỗ phân biệt bản vá này với việc chỉ thêm một câu khuyên dùng tool: nếu mệnh đề từ chối
    vẫn đứng vô điều kiện ở đâu đó trong prompt, model vẫn có đường tuân nó ngay lượt 1 — đúng hành
    vi đã đo được."""
    text = _prompt([KB_SEARCH_TOOL])
    refusal_lines = [line for line in text.splitlines() if "không có thông tin" in line.lower()]
    assert refusal_lines, "prompt agent có KB phải vẫn giữ mệnh đề từ chối khi tra không ra"
    assert all(re.search(r"sau khi|đã (gọi|tra)", line, re.IGNORECASE) for line in refusal_lines), (
        f"mệnh đề từ chối còn đứng vô điều kiện, model tuân được ngay lượt 1:\n{refusal_lines}"
    )


def test_a_non_kb_agent_is_not_told_to_search() -> None:
    """Vế bất đối xứng: agent không có `kb_search` mà bị bảo "hãy gọi `kb_search`" sẽ in ra một
    TOOL_CALL tới một tool không tồn tại, và lượt chạy hỏng theo một cách khó truy."""
    text = _prompt(["calculator"])
    assert KB_SEARCH_TOOL not in text


def test_citation_is_stated_as_mandatory_not_optional() -> None:
    """Trích dẫn phải được khai là **bắt buộc**, không phải một gợi ý.

    Không chỉ vì `citation_accuracy`. Cờ `refused` của engine suy từ
    `used_kb_search and (not citations) and (not used_non_kb_tool)` (A5), nên một agent tra KB xong
    trả lời ĐÚNG mà quên trích dẫn sẽ bị đọc thành **đã từ chối** — và case đó ra `fail_refused`
    trong khi câu trả lời hoàn toàn đúng. Đo được 3/15 case như vậy trong một lượt chấm thật.

    Câu "Khi trả lời dựa trên đoạn trích: trích dẫn chunk_id" cũ đọc như mô tả một thói quen. Bài
    này ghim nó thành mệnh lệnh."""
    text = _prompt([KB_SEARCH_TOOL])
    citation_lines = [line for line in text.splitlines() if "chunk_id" in line]
    assert citation_lines, "prompt agent có KB phải nói về trích dẫn chunk_id"
    assert any(re.search(r"BẮT BUỘC|PHẢI", line) for line in citation_lines), (
        f"trích dẫn đang được khai như gợi ý, không phải bắt buộc:\n{citation_lines}"
    )


def test_the_convention_forbids_repeating_a_tool_call_already_answered() -> None:
    """Quy ước phải nói thẳng: đã có `[Kết quả <tool>]` cho đúng tham số đó thì KHÔNG gọi lại.

    Đo được trên một lượt chạy thật, câu hỏi `15 * 15`:

        1  LLM   → calculator {"expression":"15 * 15"}
        2  TOOL  ← result: 225
        3  LLM   → calculator {"expression":"15 * 15"}   ← y hệt
        4  TOOL  ← result: 225
        5  LLM   → calculator {"expression":"15 * 15"}   ← y hệt
        6  TOOL  ← result: 225
        7  LLM   → "225"

    Kết quả CÓ được nối lại vào prompt (`observations` → `build_agent_prompt`), nên model không hề
    mù. Nó lặp vì quy ước chỉ nói *"khi đã đủ thông tin thì trả lời thẳng"* mà không nói **đừng gọi
    lại thứ đã có kết quả** — ba lượt thừa là ba lần trả tiền LLM cho cùng một phép tính, và với câu
    hỏi KB thì nó thành chuỗi 13 lượt.

    Bài kiểm nội dung prompt chứ không kiểm hành vi model: đây là lựa chọn "rẻ nhất" — dặn model,
    không chặn ở vòng lặp. Model là thứ không hứa hẹn gì, nên bài này chỉ khẳng định **lời dặn có
    mặt**, không khẳng định nó được nghe."""
    text = _prompt([KB_SEARCH_TOOL, "calculator"])
    repeat_lines = [line for line in text.splitlines() if "Kết quả" in line and "gọi lại" in line.lower()]
    assert repeat_lines, f"quy ước không có lời dặn chống gọi lặp:\n{text}"


def test_the_catalog_names_the_modes_current_datetime_accepts() -> None:
    """Catalog phải LIỆT KÊ giá trị `mode` hợp lệ, không chỉ khai kiểu `str`.

    `params: {"mode"?: str}` nói với model rằng đây là một chuỗi tuỳ ý, nên nó đoán — đo được trên
    hệ thật: model gửi `mode="date"` rồi `mode="today"`, cả hai đều không tồn tại, và người dùng
    nhận `ValueError: current_datetime: mode không hỗ trợ` ngay trên ô Phản hồi.

    Không sửa bằng cách cho `mode` lạ rơi về `"now"`: một model gõ nhầm `"yesterday"` sẽ nhận giờ
    hiện tại như thể đó là đáp án đúng (`test_current_datetime_still_rejects_a_mode_it_does_not_know`
    ghim điều đó). Chỗ hỏng là hợp đồng nói thiếu, nên vá đúng ở hợp đồng."""
    text = _prompt(["current_datetime"])
    catalog_line = next(line for line in text.splitlines() if "current_datetime" in line)
    assert "now" in catalog_line
    assert "days_between" in catalog_line
