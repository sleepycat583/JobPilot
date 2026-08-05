# JobPilot

基于 LangGraph Supervisor-Worker 架构的求职多智能体辅助系统。核心能力：JD 结构化解析、Chroma 证据驱动的简历匹配、确定性五维评分、低分人工审核 Gate、模拟面试 + 复盘报告、Graph 原生有序任务队列。通过 FastAPI HTTP 接口、异步任务和 SSE 实时事件流对外提供服务，前端使用 React + Vite + TypeScript。

> 🧠 Supervisor 负责意图路由和任务编排，Worker 代理分别处理 JD 解析、简历匹配、面试模拟；中断点（Interrupt）支持人工核可（HITL），Checkpoint 保证状态持久与恢复。

## 技术栈

| 层 | 技术 |
|---|------|
| Agent 编排 | LangGraph (Supervisor-Worker)，SQLite Checkpoint |
| LLM | OpenAI-compatible API（默认 DeepSeek） |
| 向量检索 | ChromaDB + `BAAI/bge-m3` Embedding |
| 后端 | Python 3.11+ / FastAPI / SQLAlchemy + Alembic |
| 前端 | React 19 / TypeScript 6 / Vite 8 |
| 测试 | pytest / Vitest |

## 当前状态

已完成：

- Pydantic Schema、配置校验与 OpenAI-compatible Chat/固定 `BAAI/bge-m3` Provider 边界。
- Supervisor 意图路由、有序 `task_queue` 消费、JD Parser、公司背景搜索适配器与结构化输出重试/降级。
- 简历库支持 TXT、PDF 上传与文本提取；扫描版 PDF 可在配置视觉模型后进行 OCR 回退。简历经语义切分后持久化到 Chroma，并按 `resume_id` 隔离检索及校验证据引用。
- 确定性五维匹配评分；`total_score < 60.0` 时进入 `review_status="in_review"` 的低分 Gate。
- 提供同步 `POST /v1/job-analysis` 与异步 `POST /api/tasks` 接口；请求使用 `jd_text`、可选 `resume_id` 和可选 `task_queue`，支持按已选择的任务顺序执行 JD 解析、简历匹配和模拟面试。
- 简历库接口支持上传、列表查询和异步索引状态跟踪；异步任务通过 SSE 推送节点级进度、审核请求和完成事件。

进行中 / 计划中：

- 模拟面试主流程已实现：面试计划、问题生成、回答评价、rolling summary、复盘报告生成、降级处理和最终人工核可均已接入 Graph；React 前端已接入面试回答、低分审核和评价不可用时的处理表单。
- 已实现基础 SSE 节点级实时进度展示和 React/Vite 交互页面；SSE 断线补发/`Last-Event-ID` 尚未实现。
- 使用 SQLAlchemy + Alembic 管理业务 SQLite Schema，建立可追踪的 migration 版本链，并实际验证空数据库初始化、已有数据库 upgrade 和失败恢复不破坏现有业务数据。
- Docker Compose、生产交付和前端端到端自动化测试仍未完成。

## 本地运行

要求：Python 3.11 或 3.12。

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

复制 `.env.example` 为 `.env`，并填写模型、Chroma 目录和 Embedding device 配置。若需对扫描版 PDF 启用视觉 OCR，还需配置视觉模型的相关环境变量。

### 后端

```bash
uvicorn app.api:app --reload
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

Vite 开发服务器默认运行在 `http://localhost:5173`，API 请求自动代理到后端 `http://127.0.0.1:8000`。

请求示例：

```bash
curl -X POST http://127.0.0.1:8000/v1/job-analysis ^
  -H "Content-Type: application/json" ^
  -H "X-Session-ID: 123e4567-e89b-42d3-a456-426614174000" ^
  -d "{\"jd_text\":\"后端工程师，要求熟悉 Python、FastAPI，具备三年以上接口设计经验。\",\"task_queue\":[\"jd_parse\"]}"
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
- OCR 仅作为扫描版 PDF 的回退机制，需要可用的视觉模型；图片型简历和其他格式不在当前上传范围内。
- 面试表单已接入；页面刷新后的状态恢复仍未完成。简历上传已通过幂等记录避免重复提交产生重复版本。
- SSE 当前仅提供基础事件流和节点级实时进度展示，尚未完成 `Last-Event-ID` 断线补发及异常网络测试。
- Alembic 迁移链已验证空库初始化、已有数据库 upgrade、重复 upgrade、重复采样键失败回滚和 Checkpoint 隔离；历史归档导入按 `architecture` 重编号并保留 source id 映射，源归档文件保持只读。
- 当前真实实验归档仅覆盖 `case1_simple_python_backend_jd`；Case2（跨语言简历匹配）和 Case3（复杂多轮模拟面试）尚无真实 LLM 实验记录。