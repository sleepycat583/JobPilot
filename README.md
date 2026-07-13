# Job Assistant

基于 LangGraph Supervisor-Worker 架构的求职辅助系统。Week1 已完成 JD 结构化解析、基于 Chroma 证据的简历匹配、确定性评分和低分审核 Gate，并通过最小 FastAPI HTTP 边界提供可测试的调用入口。

## 当前状态

Week1 已实现：

- Pydantic Schema、配置校验与 OpenAI-compatible Chat/固定 `BAAI/bge-m3` Provider 边界。
- Supervisor 意图路由、JD Parser、公司背景搜索适配器与结构化输出重试/降级。
- TXT 简历的语义切分、Chroma 持久化索引、按 `resume_version` 隔离检索与证据引用校验。
- 确定性五维匹配评分；`total_score < 60.0` 时进入 `review_status="in_review"` 的低分 Gate。
- `POST /v1/job-analysis`：只传 `jd_text` 解析 JD；额外传 `resume_version` 时串联简历匹配。响应包含 `jd_parsed`、`match_result`、审核状态、执行轨迹和 `error_log`。

Week2+ 范围或当前占位：

- 真实 HITL `interrupt()`/`Command()`、checkpoint 恢复、审核命令与完整 `ReviewStatus` 生命周期。
- 模拟面试、rolling summary、结构化日志装饰器、最终产物生成；当前 `finalize_node` 不写 `final_output`。
- SQLAlchemy 业务存储、SSE、React UI、Docker Compose 与生产交付。

## 本地运行

要求：Python 3.11 或 3.12。

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

复制 `.env.example` 为 `.env`，并填写模型、Chroma 目录和 Embedding device 配置。随后启动：

```bash
uvicorn app.api:app --reload
```

请求示例：

```bash
curl -X POST http://127.0.0.1:8000/v1/job-analysis ^
  -H "Content-Type: application/json" ^
  -d "{\"jd_text\":\"后端工程师，要求熟悉 Python、FastAPI，具备三年以上接口设计经验。\",\"resume_version\":\"2026-07-v1\"}"
```

运行全部测试：

```bash
pytest -q
```

## 已知限制

- **组合请求是 Week1 临时方案**：Graph 目前不消费 `task_queue`。当请求提供 `resume_version` 时，API 层显式两次调用 Graph，先解析 JD 再匹配简历。Week2+ 必须在 Graph 内实现真正的队列消费逻辑，并删除 API 中的临时编排。
- 低分 Gate 只写入待审核状态后结束执行；没有真实 interrupt、continue/revise/cancel 命令或幂等恢复。
- `build_graph` 未注入 `resume_store` 时会启用仅供测试的 100 分 matcher 占位，真实匹配必须注入 Chroma store。
- Chroma 当前只校验 collection metadata 中的 embedding 模型名和维度，未对运行时向量长度做额外校验。
- 简历切分是规则式文本处理：仅支持有限的中英文标题别名；experience/project 没有显式标签或日期范围时会保守合并，避免误切分。
- 索引目录入口只读取顶层 UTF-8 `.txt` 文件，不支持 PDF/DOCX、递归目录或其他文本编码。
- 面试、最终产物、SSE、React、SQLAlchemy、Docker 和 Checkpoint 恢复尚未实现，不应作为可用功能对外承诺。