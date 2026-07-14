# Job Assistant

基于 LangGraph Supervisor-Worker 架构的求职辅助系统。已完成 JD 结构化解析、基于 Chroma 证据的简历匹配、确定性评分、低分审核 Gate 和 Graph 原生有序任务队列，并通过 FastAPI HTTP 边界提供可测试的调用入口。

## 当前状态

当前已实现：

- Pydantic Schema、配置校验与 OpenAI-compatible Chat/固定 `BAAI/bge-m3` Provider 边界。
- Supervisor 意图路由、有序 `task_queue` 消费、JD Parser、公司背景搜索适配器与结构化输出重试/降级。
- TXT 简历的语义切分、Chroma 持久化索引、按 `resume_version` 隔离检索与证据引用校验。
- 确定性五维匹配评分；`total_score < 60.0` 时进入 `review_status="in_review"` 的低分 Gate。
- `POST /v1/job-analysis`：只传 `jd_text` 解析 JD；额外传 `resume_version` 时由一次 Graph 调用顺序执行 JD 解析和简历匹配。响应包含 `jd_parsed`、`match_result`、审核状态、执行轨迹和 `error_log`。

后续范围或当前占位：

- 模拟面试、rolling summary、结构化日志装饰器；最终产物仅在人工最终核可后由 `finalize_node` 格式化，不生成额外 LLM 报告文本。
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

- 低分 Gate 当前支持 `interrupt()`、`continue`/`revise_inputs`/`cancel` 和 SQLite checkpoint 恢复；`revise_inputs` 可更换简历版本或修正 JD 后重新评分，若结果仍低分则再次要求确认。
- `build_graph` 未注入 `resume_store` 时会启用仅供测试的 100 分 matcher 占位，真实匹配必须注入 Chroma store。
- Chroma 当前只校验 collection metadata 中的 embedding 模型名和维度，未对运行时向量长度做额外校验。
- 简历切分是规则式文本处理：仅支持有限的中英文标题别名；experience/project 没有显式标签或日期范围时会保守合并，避免误切分。
- 索引目录入口只读取顶层 UTF-8 `.txt` 文件，不支持 PDF/DOCX、递归目录或其他文本编码。
- 面试、SSE、React、SQLAlchemy 和 Docker 尚未实现，不应作为可用功能对外承诺。