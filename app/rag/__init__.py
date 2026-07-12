"""简历 RAG 模块导出。

本包负责简历切分、向量索引与 Chroma 检索适配，
供后续简历匹配 Worker 复用，不包含第⑩步评分逻辑。
"""

from app.rag.chunking import ResumeChunk, chunk_resume
from app.rag.chroma_store import ChromaQueryResult, ChromaResumeStore
from app.rag.indexing import index_resume_chunks, index_resume_fixture_directory

__all__ = [
    "ChromaQueryResult",
    "ChromaResumeStore",
    "ResumeChunk",
    "chunk_resume",
    "index_resume_chunks",
    "index_resume_fixture_directory",
]
