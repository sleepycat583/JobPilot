"""TXT、文本型 PDF 与扫描 PDF 的简历文本提取测试。"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.services.resume_extraction import ResumeTextExtractor
from app.services.resume_storage import ResumeFileValidationError


class FakeVisionModel:
    """记录视觉调用并返回每页固定 OCR 文本。"""

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, _message):
        self.calls += 1
        return type("VisionResponse", (), {"content": f"扫描页 {self.calls} 的简历内容"})()


def _write_pdf(path: Path, pages: list[str | None]) -> None:
    """生成含文本层或纯白页的最小 PDF 测试夹具。"""

    document = fitz.open()
    for text in pages:
        page = document.new_page()
        if text is not None:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_extracts_text_layer_pdf_without_calling_vision_model(tmp_path: Path) -> None:
    pdf_path = tmp_path / "text-resume.pdf"
    _write_pdf(pdf_path, ["Name: Ada\nPython engineer", "Experience: five years"])
    vision_model = FakeVisionModel()

    text = ResumeTextExtractor(vision_model=vision_model).extract(str(pdf_path))

    assert "[第 1 页]" in text
    assert "Python engineer" in text
    assert "[第 2 页]" in text
    assert vision_model.calls == 0


def test_rejects_scanned_pdf_when_vision_ocr_is_not_configured(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned-resume.pdf"
    _write_pdf(pdf_path, [None])

    with pytest.raises(ResumeFileValidationError) as error:
        ResumeTextExtractor().extract(str(pdf_path))

    assert error.value.code == "RESUME_PDF_OCR_UNAVAILABLE"


def test_uses_vision_model_for_scanned_pdf_and_preserves_page_order(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned-resume.pdf"
    _write_pdf(pdf_path, [None, None])
    vision_model = FakeVisionModel()

    text = ResumeTextExtractor(vision_model=vision_model).extract(str(pdf_path))

    assert text == "[第 1 页]\n扫描页 1 的简历内容\n\n[第 2 页]\n扫描页 2 的简历内容"
    assert vision_model.calls == 2