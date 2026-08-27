"""Chỉ thị prompt RIÊNG cho agent không gắn KB — `kit#256`, lối (D).

## Vì sao cần

`engine#51` gỡ chỉ thị grounding cho agent không có `kb_search`, và gỡ đúng: chỉ thị đó điều kiện
theo *"đoạn trích rỗng"*, mà agent không-KB thì đoạn trích LUÔN rỗng, nên nó từ chối cả câu chào
hỏi thường.

Nhưng gỡ trắng để lại một khoảng trống: sau `web#51` (bỏ `system_prompt` khỏi giao diện, luôn `""`)
và `engine#49/#50` (whitelist phản ánh đúng agent có KB hay không), **không còn chỗ nào nói cho
model biết agent này không có tài liệu công ty**. Nó sẽ trả lời từ kiến thức nền — tức bịa một
chính sách cho một công ty cụ thể — và cổng Publish (`evalhub.no_kb_golden`) bắt được điều đó
thành FAIL, chặn lại đúng loại agent vừa được mở đường.

## Điều kiện theo LOẠI CÂU HỎI, không theo "đoạn trích rỗng"

Đó là toàn bộ khác biệt giữa chỉ thị mới và chỉ thị `engine#51` vừa gỡ, và là lý do bug "xin chào"
không quay lại: câu chào hỏi không nằm trong phạm vi *"được hỏi về nội dung tài liệu/chính sách"*.
Bài `test_khong_dieu_kien_theo_doan_trich_rong` ghim đúng chỗ đó.
"""

from __future__ import annotations

from studio_engine.agent_protocol import KB_SEARCH_TOOL, build_agent_prompt


def _prompt(tool_names: list[str], question: str = "Chính sách nghỉ phép công ty thế nào?") -> str:
    return build_agent_prompt(system_prompt="", question=question, tool_names=tool_names, observations=[])


def test_agent_khong_kb_duoc_bao_la_khong_co_tai_lieu() -> None:
    """Không có `kb_search` ⇒ prompt phải NÓI RA rằng agent không có tài liệu công ty.

    Thiếu câu này, model tự do bịa chính sách của một công ty cụ thể — và không mặt phẳng nào còn
    lại để chỉnh (`system_prompt` luôn rỗng từ `web#51`)."""
    text = _prompt([])
    assert "KHÔNG có tài liệu nội bộ" in text
    assert "không có thông tin" in text


def test_agent_co_kb_khong_nhan_chi_thi_do() -> None:
    """Vế bất đối xứng: agent CÓ `kb_search` thật sự có tài liệu — bảo nó "bạn không có tài liệu"
    là nói dối model, và nó sẽ từ chối cả câu trả được."""
    text = _prompt([KB_SEARCH_TOOL, "calculator"])
    assert "KHÔNG có tài liệu nội bộ" not in text


def test_agent_co_kb_van_giu_chi_thi_grounding() -> None:
    """Không được đánh đổi: nhánh có KB phải giữ luật trích dẫn VÀ luật từ chối-khi-tra-không-ra.

    Neo vào **bất biến**, không vào mặt chữ: bản trước assert nguyên văn *"Nếu các đoạn trích không
    chứa câu trả lời"*, nên khi mệnh đề đó được viết lại kèm điều kiện *"chỉ SAU KHI đã gọi
    kb_search"* (`test_search_before_refusing`) thì bài đỏ dù luật không hề mất. Một bài neo mặt chữ
    biến mọi lần sửa cách diễn đạt thành một lần đỏ giả, và người sửa sẽ học cách nới nó thay vì đọc
    nó."""
    text = _prompt([KB_SEARCH_TOOL])
    assert "chunk_id" in text
    assert "không có thông tin" in text
    assert "đoạn trích" in text


def test_agent_khong_kb_khong_nhan_lai_chi_thi_cu() -> None:
    """Bug `engine#51` vá KHÔNG được quay lại: chỉ thị cũ điều kiện theo *đoạn trích rỗng*, mà agent
    không-KB thì đoạn trích luôn rỗng ⇒ nó từ chối cả "xin chào"."""
    text = _prompt([])
    # Cùng lý do bài trên: kiểm KHÁI NIỆM ("đoạn trích") chứ không kiểm nguyên văn một câu đã đổi.
    # Assert vắng mặt của một chuỗi không còn tồn tại ở đâu cả là bài luôn xanh, không chứng minh gì.
    assert "đoạn trích" not in text
    assert "chunk_id" not in text


def test_khong_dieu_kien_theo_doan_trich_rong() -> None:
    """Bài quan trọng nhất — ghim đúng thứ phân biệt chỉ thị mới với chỉ thị đã gỡ.

    Mới: điều kiện theo **loại câu hỏi** (*"được hỏi về nội dung tài liệu…"*) — "xin chào" không
    rơi vào phạm vi.
    Cũ:  điều kiện theo **trạng thái dữ liệu** (*"đoạn trích không chứa câu trả lời"*) — luôn đúng
    với agent không-KB, nên phủ cả câu chào hỏi.

    Nếu ai đó viết lại chỉ thị mới theo kiểu cũ, bài này đỏ trước khi bug quay lại trên production."""
    text = _prompt([])
    assert "đoạn trích" not in text, "chỉ thị không-KB không được nhắc tới đoạn trích — đó là điều kiện SAI"
    assert "nội dung tài liệu" in text, "phải điều kiện theo loại câu hỏi"


def test_cau_chao_hoi_va_cau_hoi_tai_lieu_nhan_CUNG_mot_prompt_convention() -> None:
    """Chỉ thị là TĨNH theo agent, không theo câu hỏi — `build_agent_prompt` không đọc `question`
    để quyết định. Phân biệt loại câu hỏi là việc của model, và đó là chủ đích: một hàm render
    đoán ý câu hỏi bằng từ khoá sẽ sai theo cách không ai gỡ nổi."""
    chao = _prompt([], question="xin chào")
    tai_lieu = _prompt([], question="Chính sách nghỉ phép công ty thế nào?")
    assert chao.split("Câu hỏi:")[0] == tai_lieu.split("Câu hỏi:")[0]
