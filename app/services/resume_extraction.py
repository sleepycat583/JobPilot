"""简历文本提取服务。

由 ResumeIndexService 调用，将已保存的 TXT/PDF 转换为可切块文本；文本型 PDF
优先本地提取，只有扫描件才调用注入的视觉模型，避免该基础设施能力进入 Agent 边界。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import fitz
from langchain_core.messages import HumanMessage
from pypdf import PdfReader

from app.services.resume_storage import ResumeFileValidationError

MAX_PDF_PAGES = 20
MIN_TEXT_CHARACTERS = 40


class ResumeTextExtractor:
    """提取 TXT、文本型 PDF 或扫描 PDF 的可索引文本。"""

    def __init__(self, *, vision_model: Any | None = None) -> None:
        self._vision_model = vision_model

    def extract(self, storage_path: str) -> str:
        """从原始简历文件提取文本。

        参数：storage_path 是已持久化的 TXT/PDF 路径。
        返回：保留 PDF 页码分隔的非空文本。
        异常：ResumeFileValidationError 表示用户可修正或可重试的提取错误。
        """

        path = Path(storage_path)
        if not path.exists():
            raise ResumeFileValidationError("RESUME_SOURCE_FILE_MISSING", "Stored resume file is missing")
        if path.suffix.casefold() == ".txt":
            return self._read_txt(path)
        if path.suffix.casefold() != ".pdf":
            raise ResumeFileValidationError("RESUME_FILE_TYPE_UNSUPPORTED", "Stored resume type is unsupported")
        return self._extract_pdf(path)

    def _read_txt(self, path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ResumeFileValidationError("RESUME_FILE_ENCODING_INVALID", "Stored resume file is not valid UTF-8") from error
        if not text.strip():
            raise ResumeFileValidationError("RESUME_TEXT_EMPTY", "Resume text must not be blank")
        return text

    def _extract_pdf(self, path: Path) -> str:
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                raise ResumeFileValidationError("RESUME_PDF_ENCRYPTED", "Password-protected resume PDFs are not supported")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ResumeFileValidationError("RESUME_PDF_TOO_MANY_PAGES", f"Resume PDF exceeds the {MAX_PDF_PAGES}-page limit")
            page_texts = [page.extract_text() or "" for page in reader.pages]
        except ResumeFileValidationError:
            raise
        except Exception as error:
            raise ResumeFileValidationError("RESUME_PDF_PARSE_FAILED", "Resume PDF could not be parsed") from error

        text = self._join_pages(page_texts)
        if len(text.strip()) >= MIN_TEXT_CHARACTERS:
            return text
        return self._extract_scanned_pdf(path, len(page_texts))

    def _extract_scanned_pdf(self, path: Path, page_count: int) -> str:
        # 为什么这样做：没有足够文本层的 PDF 才调用视觉模型，避免普通 PDF 产生 OCR 成本。
        if self._vision_model is None:
            raise ResumeFileValidationError(
                "RESUME_PDF_OCR_UNAVAILABLE", "Scanned resume PDF requires configured vision OCR support"
            )
        try:
            document = fitz.open(path)
            page_texts = [self._ocr_page(page, index + 1) for index, page in enumerate(document)]
        except ResumeFileValidationError:
            raise
        except Exception as error:
            raise ResumeFileValidationError("RESUME_PDF_OCR_FAILED", "Resume PDF OCR failed") from error
        finally:
            if "document" in locals():
                document.close()
        if len(page_texts) != page_count:
            raise ResumeFileValidationError("RESUME_PDF_OCR_FAILED", "Resume PDF page count changed during OCR")
        text = self._join_pages(page_texts)
        if not text.strip():
            raise ResumeFileValidationError("RESUME_TEXT_EMPTY", "Resume PDF OCR produced no text")
        return text

    def _ocr_page(self, page: Any, page_number: int) -> str:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        image_data = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
        response = self._vision_model.invoke(HumanMessage(content=[
            {"type": "text", "text": "提取此简历页面的全部可见文字。保留标题、项目、经历和表格的阅读顺序；只返回纯文本，不要解释。"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
        ]))
        content = getattr(response, "content", "")
        if not isinstance(content, str) or not content.strip():
            raise ResumeFileValidationError("RESUME_PDF_OCR_FAILED", f"OCR returned no text for page {page_number}")
        return content.strip()

    @staticmethod
    def _join_pages(page_texts: list[str]) -> str:
        return "\n\n".join(
            f"[第 {index} 页]\n{text.strip()}" for index, text in enumerate(page_texts, start=1) if text.strip()
        )