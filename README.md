# 求职工作台

多智能体 AI 求职助手。当前版本已经接入 LangGraph Supervisor-Worker、SqliteSaver checkpoint、HTTP/SSE 状态同步和 HITL 流程。简历/JD 索引、匹配评分和面试内容仍保留确定性实现，后续逐个替换为真实 Worker 能力。

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

默认 `LLM_MODE=stub` 会运行完整 LangGraph 拓扑和 checkpoint，但不会假装做语义判断。配置环境变量 `LLM_MODE=openai`、`OPENAI_MODEL` 和 `OPENAI_API_KEY` 后启用真实 LLM 路由。LangSmith Trace 优先在本机通过 OAuth 完成认证，任何真实密钥都不得提交。

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
