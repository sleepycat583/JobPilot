# 求职工作台

多智能体 AI 求职助手。当前版本已经接入 LangGraph Supervisor-Worker、SqliteSaver checkpoint、HTTP/SSE 状态同步和 HITL 流程。OpenAI-compatible 模式下，简历与 JD 解析、匹配评分、面试出题、回答评估和复盘均使用经过 Pydantic 校验的真实结构化模型输出；Stub 模式保留确定性结果用于离线回归。

## 目录

```text
career-agent-workbench/
├─ frontend/   React + Vite + React Router + TanStack Query
└─ backend/    FastAPI + LangGraph + SQLAlchemy + Alembic + SQLite
```

## 启动后端

需要 Python 3.11 和 uv。

```powershell
cd backend
Copy-Item .env.example .env
uv sync --dev
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

OpenAPI：`http://127.0.0.1:8000/docs`

默认 `LLM_MODE=stub` 会运行完整 LangGraph 拓扑和 checkpoint，但不会假装做语义判断。配置 `LLM_MODE=openai`、`OPENAI_MODEL` 和 `OPENAI_API_KEY` 后启用真实 LLM 路由与业务任务；第三方兼容服务通过 `OPENAI_BASE_URL` 接入。LangSmith Trace 优先在本机通过 OAuth 完成认证，任何真实密钥都不得提交。

上传的 PDF、DOCX 和 TXT 会在后台提取文本、清理联系方式、生成结构化简历，并通过阿里云原生 Embedding 接口写入本地 ChromaDB。结构化结果落库后的中间状态为 `parsed`，向量索引成功后为 `indexed`；匹配分析只使用该简历检索出的证据片段。ChromaDB 精确锁定为 `1.5.9`，匿名遥测已关闭。

对话工作台已经接入真实业务动作：用户可以直接粘贴新 JD、要求执行当前简历与 JD 的匹配分析，或在聊天中开始模拟面试、提交回答、查看逐题反馈并继续下一题。Supervisor 仍然只负责语义路由；是否执行动作由被选中的 Worker 通过结构化输出决定，API 层不做关键词判断。

## 启动前端

需要 Node.js 20+。

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

前端：`http://127.0.0.1:5173/`

## 验证

```powershell
cd backend
uv run pytest
uv run alembic check

cd ..\frontend
npm run typecheck
npm run build
```

Supervisor 只做语义路由，Worker 生成业务结果，Output Sanitizer 是唯一用户输出出口。详细状态所有权和恢复流程见 `backend/docs/langgraph-architecture.md`。API 层不包含关键词或正则意图路由。
