"""简历向量索引写入。

本文件负责把 chunking 产出的语义 chunk 经过 embedding 编码后写入 Chroma，
并提供测试夹具目录批量入库入口。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.rag.chunking import ResumeChunk, chunk_resume
from app.rag.chroma_store import ChromaResumeStore


def index_resume_chunks(
    chunks: list[ResumeChunk],
    embedding_model: Any,
    store: ChromaResumeStore,
) -> list[ResumeChunk]:
    """对 chunk 批量编码并写入 Chroma。

    参数：
        chunks: 待入库的语义 chunk 列表。
        embedding_model: 需支持 `encode(list[str])` 的 embedding provider。
        store: Chroma 存储适配层。

    返回：
        原始 chunks，便于调用方继续复用。
    """

    if not chunks:
        return []

    embeddings = embedding_model.encode([chunk["source_text"] for chunk in chunks])
    store.upsert_chunks(chunks, embeddings)
    return chunks


def index_resume_fixture_directory(
    directory: str | Path,
    embedding_model: Any,
    store: ChromaResumeStore,
) -> list[ResumeChunk]:
    """批量读取测试夹具目录中的 TXT 简历并入库。

    参数：
        directory: 夹具目录路径，仅处理 `.txt` 文件。
        embedding_model: 需支持 `encode(list[str])` 的 embedding provider。
        store: Chroma 存储适配层。

    返回：
        本次入库的全部 chunk 列表。
    """

    fixture_dir = Path(directory)
    all_chunks: list[ResumeChunk] = []

    for path in sorted(fixture_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        chunks = chunk_resume(text, resume_version=path.stem, source_id=path.name)
        index_resume_chunks(chunks, embedding_model, store)
        all_chunks.extend(chunks)

    return all_chunks