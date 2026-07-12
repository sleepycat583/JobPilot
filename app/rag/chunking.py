"""简历语义切分。

本文件负责把原始简历文本切分为 profile/experience/project/skill/education 五类语义 chunk，
供 indexing.py 写入向量库；不负责评分或召回。
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict


ChunkType = Literal["profile", "experience", "project", "skill", "education"]


class ResumeChunk(TypedDict):
    """简历语义 chunk。

    字段：
        chunk_id: 当前 chunk 的稳定标识，格式为 `type-序号`。
        chunk_type: 五类语义单元之一，供后续检索与评分使用。
        section_type: 与 chunk_type 保持一致，兼容架构文档中的 metadata 示例。
        resume_version: 简历版本号。
        source_id: 数据来源标识，例如文件名或上传记录 ID。
        source_text: 当前 chunk 的原始文本，用于检索引用。
        start_line/end_line: chunk 在原始文本中的行号范围，便于后续追溯。
    """

    chunk_id: str
    chunk_type: ChunkType
    section_type: ChunkType
    resume_version: str
    source_id: str
    source_text: str
    start_line: int
    end_line: int


SECTION_ALIASES: dict[ChunkType, tuple[str, ...]] = {
    "profile": ("个人信息", "基本信息", "个人简介", "个人概述", "求职意向", "简介", "profile", "summary"),
    "experience": ("工作经历", "实习经历", "工作经验", "职业经历", "experience"),
    "project": ("项目经历", "项目经验", "项目", "projects", "project"),
    "skill": ("专业技能", "技能", "技能清单", "核心技能", "skills", "skill"),
    "education": ("教育经历", "教育背景", "学历", "education"),
}

STRONG_ENTRY_LABELS: dict[ChunkType, tuple[str, ...]] = {
    # 只保留真正能开启新条目的标志；岗位/时间/职责等字段归入同一条经历或项目。
    "experience": ("公司", "单位", "雇主"),
    "project": ("项目名称", "项目"),
}

DATE_CONNECTOR_PATTERN = r"(?:-|–|—|~|至|到|/|\\)"
# 业务规则：只要一行里出现可读的日期范围，就允许把它当成经历/项目条目起点；
# 因此这里同时兼容 YYYY-MM、YYYY/MM、YYYY年M月、单独年份与“至今/现在”等表述。
DATE_FRAGMENT_PATTERN = r"(?:\d{4}(?:[./-]\d{1,2}|年\d{1,2}月?|[./]\d{1,2})?|\d{1,2}月?)"
DATE_RANGE_RE = re.compile(
    rf"{DATE_FRAGMENT_PATTERN}\s*{DATE_CONNECTOR_PATTERN}\s*(?:{DATE_FRAGMENT_PATTERN}|至今|现在|present|current)",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^\s*[一二三四五六七八九十0-9]+[、.．)]\s*(.+?)\s*$")


def chunk_resume(text: str, resume_version: str, source_id: str = "resume-main") -> list[ResumeChunk]:
    """按冻结规则切分简历文本。

    参数：
        text: 原始简历全文。
        resume_version: 当前简历版本标识。
        source_id: 当前文本来源标识，默认 `resume-main`。

    返回：
        按语义单元切分后的 chunk 列表；空文本返回空列表。
    """

    lines = text.splitlines()
    sections = _group_lines_by_section(lines)
    counters: dict[ChunkType, int] = {key: 0 for key in SECTION_ALIASES}
    chunks: list[ResumeChunk] = []

    for section_type, section_lines in sections:
        if section_type in {"experience", "project"}:
            entries = _split_entry_sections(section_type, section_lines)
        else:
            entries = [section_lines]

        for entry_lines in entries:
            normalized_lines = [line[1].rstrip() for line in entry_lines if line[1].strip()]
            if not normalized_lines:
                continue

            counters[section_type] += 1
            start_line = entry_lines[0][0]
            end_line = entry_lines[-1][0]
            source_text = "\n".join(normalized_lines)
            chunks.append(
                ResumeChunk(
                    chunk_id=f"{section_type}-{counters[section_type]:03d}",
                    chunk_type=section_type,
                    section_type=section_type,
                    resume_version=resume_version,
                    source_id=source_id,
                    source_text=source_text,
                    start_line=start_line,
                    end_line=end_line,
                )
            )

    return chunks


def _group_lines_by_section(lines: list[str]) -> list[tuple[ChunkType, list[tuple[int, str]]]]:
    sections: list[tuple[ChunkType, list[tuple[int, str]]]] = []
    current_type: ChunkType = "profile"
    current_lines: list[tuple[int, str]] = []

    for line_number, raw_line in enumerate(lines, start=1):
        detected = _detect_section_heading(raw_line)
        if detected is not None:
            if current_lines:
                sections.append((current_type, current_lines))
            current_type = detected
            current_lines = []
            continue

        current_lines.append((line_number, raw_line))

    if current_lines:
        sections.append((current_type, current_lines))

    return [(section_type, content) for section_type, content in sections if any(line.strip() for _, line in content)]


def _detect_section_heading(line: str) -> ChunkType | None:
    normalized = _normalize_heading_text(line)
    if not normalized:
        return None

    for chunk_type, aliases in SECTION_ALIASES.items():
        if normalized in aliases:
            return chunk_type
    return None


def _normalize_heading_text(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""

    match = HEADING_RE.match(stripped)
    if match:
        stripped = match.group(1).strip()

    return stripped.casefold().replace("：", "").replace(":", "")


def _split_entry_sections(
    section_type: Literal["experience", "project"],
    section_lines: list[tuple[int, str]],
) -> list[list[tuple[int, str]]]:
    entries: list[list[tuple[int, str]]] = []
    current_entry: list[tuple[int, str]] = []

    for line_number, raw_line in section_lines:
        if _is_new_entry_line(section_type, raw_line, current_entry):
            if current_entry:
                entries.append(current_entry)
            current_entry = [(line_number, raw_line)]
            continue

        current_entry.append((line_number, raw_line))

    if current_entry:
        entries.append(current_entry)

    return [entry for entry in entries if any(line.strip() for _, line in entry)]


def _is_new_entry_line(
    section_type: Literal["experience", "project"],
    raw_line: str,
    current_entry: list[tuple[int, str]],
) -> bool:
    stripped = raw_line.strip()
    if not stripped:
        return False

    for label in STRONG_ENTRY_LABELS[section_type]:
        if stripped.startswith(f"{label}：") or stripped.startswith(f"{label}:"):
            return True

    # 业务规则：当没有显式标签时，允许使用“短标题 + 日期范围”识别新条目；
    # 若既无标签又无日期范围，则保守并入当前条目，避免把自由文本误切成新记录。
    has_field_label = "：" in stripped or ":" in stripped
    if current_entry and not has_field_label and _looks_like_short_title(stripped) and DATE_RANGE_RE.search(stripped):
        return True

    return not current_entry


def _looks_like_short_title(line: str) -> bool:
    if line.startswith(("-", "*", "•", "1.", "2.", "3.")):
        return False
    return len(line) <= 80