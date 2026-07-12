"""项目冻结常量。

本文件只保存架构设计文档中已经冻结的常量，不从环境变量读取，避免运行时被随意改写。
这些值直接对应文档第 3.3 节、8.2 节和 12.7 节，用于后续 RAG、输入校验和结构化输出重试。
"""

# 文档 §3.3：Embedding 固定为唯一模型，不支持运行时切换。
EMBEDDING_MODEL = "BAAI/bge-m3"

# 文档 §3.3：collection metadata 需固定记录 bge-m3 向量维度。
EMBEDDING_DIMENSION = 1024

# 文档 §3.3：Chroma collection 固定为 resume_chunks。
CHROMA_COLLECTION_NAME = "resume_chunks"

# 文档 §3.3：每个 JD 技能/职责查询固定取 top_k=5。
RAG_TOP_K = 5

# 文档 §3.3：relevance_score < 0.35 的片段不作为正向证据。
RAG_RELEVANCE_THRESHOLD = 0.35

# 文档 §3.3 / §12.7：低分阈值固定为 60.0。
LOW_SCORE_THRESHOLD = 60.0

# 文档 §3.1 / §12.7：单次输入最大长度固定为 30000 字符。
MAX_INPUT_LENGTH = 30000

# 文档 §8.2：最大重试次数固定为 2 次，即首次调用 + 最多 2 次重试 = 最多 3 次模型调用。
MAX_FORMAT_RETRIES = 2
