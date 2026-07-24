"""简历原始文件校验与存储服务。

本模块供后续上传 Service 调用，负责把已上传的字节校验为 UTF-8 TXT 并原子保存到
`data/resumes/`；不创建数据库记录、不调用 Chroma，也不处理 HTTP 请求。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MAX_RESUME_FILE_SIZE_BYTES = 2 * 1024 * 1024


class ResumeFileValidationError(ValueError):
    """表示上传文件不符合简历库的冻结格式规则。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedResumeFile:
    """已通过格式校验的原始简历内容。"""

    file_name: str
    content: bytes
    text: str

    @property
    def file_size(self) -> int:
        """返回原始上传字节数，供持久化和 2 MB 规则复用。"""

        return len(self.content)


class ResumeStorage:
    """`data/resumes/` 原始 TXT 文件存储。"""

    def __init__(self, directory: str | Path = "data/resumes") -> None:
        self._directory = Path(directory)

    def validate(self, *, file_name: str, content: bytes) -> ValidatedResumeFile:
        """校验上传字节并生成可安全索引的 UTF-8 文本。

        参数：
            file_name: 用户上传时携带的原始文件名。
            content: 未解码的原始文件字节。
        返回：
            包含原始字节与 UTF-8 文本的不可变校验结果。
        异常：
            ResumeFileValidationError: 文件为空、类型不支持、超过限制、编码非法或文本为空。
        """

        normalized_name = Path(file_name).name
        if not normalized_name.casefold().endswith(".txt"):
            raise ResumeFileValidationError("RESUME_FILE_TYPE_UNSUPPORTED", "Only .txt resume files are supported")
        if not content:
            raise ResumeFileValidationError("RESUME_FILE_EMPTY", "Resume file must not be empty")
        if len(content) > MAX_RESUME_FILE_SIZE_BYTES:
            raise ResumeFileValidationError("RESUME_FILE_TOO_LARGE", "Resume file exceeds the 2 MB limit")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ResumeFileValidationError(
                "RESUME_FILE_ENCODING_INVALID", "Resume file must use UTF-8 encoding"
            ) from error
        if not text.strip():
            raise ResumeFileValidationError("RESUME_TEXT_EMPTY", "Resume text must not be blank")
        return ValidatedResumeFile(file_name=normalized_name, content=content, text=text)

    def save(self, *, resume_id: str, content: bytes) -> str:
        """将已校验内容原子保存为简历资源的原始 TXT 文件。

        参数：
            resume_id: 简历资源 UUIDv4，用于稳定且不可由文件名注入的存储名。
            content: 已通过 `validate()` 的原始文件字节。
        返回：
            相对于项目根目录的 POSIX 路径，供数据库持久化。

        使用临时文件再替换，避免进程中断留下被当成完整简历的半写入文件。
        """

        self._directory.mkdir(parents=True, exist_ok=True)
        final_path = self._directory / f"{resume_id}.txt"
        temporary_path = self._directory / f".{resume_id}.uploading"
        try:
            temporary_path.write_bytes(content)
            temporary_path.replace(final_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return final_path.as_posix()

    def read_text(self, storage_path: str) -> str:
        """读取已保存的原始 UTF-8 文本，供索引任务重建 chunk。"""

        try:
            return Path(storage_path).read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise ResumeFileValidationError("RESUME_SOURCE_FILE_MISSING", "Stored resume file is missing") from error
        except UnicodeDecodeError as error:
            raise ResumeFileValidationError(
                "RESUME_FILE_ENCODING_INVALID", "Stored resume file is not valid UTF-8"
            ) from error

    def delete(self, storage_path: str) -> None:
        """删除本次上传未被持久化引用的原始文件。

        参数：
            storage_path: 由 `save()` 返回的相对或项目内路径。
        返回：
            无返回值；文件已不存在时按幂等删除处理。
        """

        path = Path(storage_path)
        if path.exists():
            path.unlink()