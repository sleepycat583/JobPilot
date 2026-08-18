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

健康检查：

- `GET /api/health/live`：进程存活检查，不访问外部服务。
- `GET /api/health/ready`：应用已完成 lifespan 初始化，并可读业务数据库、LangGraph checkpoint、文件存储和（启用时）Chroma。

默认 `LLM_MODE=stub` 会运行完整 LangGraph 拓扑和 checkpoint，但不会假装做语义判断。配置 `LLM_MODE=openai`、`OPENAI_MODEL` 和 `OPENAI_API_KEY` 后启用真实 LLM 路由与业务任务；第三方兼容服务通过 `OPENAI_BASE_URL` 接入。LangSmith Trace 优先在本机通过 OAuth 完成认证，任何真实密钥都不得提交。

上传的 PDF、DOCX 和 TXT 会在后台提取文本、清理联系方式、生成结构化简历，并通过阿里云原生 Embedding 接口写入 ChromaDB。结构化结果落库后的中间状态为 `parsed`，向量索引成功后为 `indexed`；匹配分析只使用该简历检索出的证据片段。ChromaDB 精确锁定为 `1.5.9`，匿名遥测已关闭。单实例默认使用本地文件；多实例可将 `UPLOAD_STORAGE_BACKEND` 切换为 `s3`，使用 S3/MinIO 共享对象存储。

对话工作台已经接入真实业务动作：用户可以直接粘贴新 JD、要求执行当前简历与 JD 的匹配分析，或在聊天中开始模拟面试、提交回答、查看逐题反馈并继续下一题。Supervisor 仍然只负责语义路由；是否执行动作由被选中的 Worker 通过结构化输出决定，API 层不做关键词判断。

## 启动前端

需要 Node.js 20+。

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

前端：`http://127.0.0.1:5173/`

Vite 开发代理默认指向 `http://127.0.0.1:8000`。如后端使用其他端口，启动前设置 `API_PROXY_TARGET`，例如：

```powershell
$env:API_PROXY_TARGET = "http://127.0.0.1:8001"
npm run dev -- --host 127.0.0.1 --port 5174
```

## Docker Compose 部署

需要 Docker Desktop（包含 Compose）。首次启动前创建本地配置：

```powershell
Copy-Item backend\.env.example backend\.env
```

编辑 `backend\.env`：生产环境至少需要设置 `LLM_MODE=openai`、兼容模型的 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`；LangSmith 优先使用本机 OAuth 注入的环境变量。不要把真实密钥写入 `docker-compose.yml`、镜像层或 Git。

启动：

```powershell
docker compose up --build
```

访问 `http://127.0.0.1:8080/`，后端健康检查通过后 Nginx 才会启动前端服务。Nginx 对 `/api/` 关闭代理缓冲并保持长连接，确保 SSE 不被聚合；数据、SQLite checkpoint、上传文件和 Chroma 索引保存在 `backend_data` 卷中。

停止：

```powershell
docker compose down
```

当前 Compose 配置使用单个后端 worker 和 SQLite，以保证 SqliteSaver、后台任务和本地 Chroma 的进程内一致性。需要多实例或高并发部署时，应先把业务数据库迁移到 Postgres，并将 checkpoint、上传文件和向量库切换到共享持久化方案，再增加 worker 数量。

多实例模板位于 `docker-compose.multi-instance.yml`，依赖外部 PostgreSQL、S3/MinIO 和 Chroma。
先在单个受控迁移任务中执行 `docker compose -f docker-compose.multi-instance.yml --profile ops run --rm migrate`，
再执行 `docker compose -f docker-compose.multi-instance.yml up --build` 启动两个后端实例和前端负载均衡。
默认单实例 Compose 仍设置 `RUN_MIGRATIONS=true`；多实例 API 容器固定为 `false`，避免并发迁移。

Checkpoint 也支持显式切换到 PostgreSQL：设置 `CHECKPOINT_BACKEND=postgres` 和
`GRAPH_CHECKPOINT_DATABASE_URL`。配置错误不会回退到 SQLite；首次建表由
`AUTO_CREATE_CHECKPOINT_SCHEMA` 控制。详细迁移边界见 `backend/docs/production-storage.md`。

业务库的 PostgreSQL URL 会统一使用 Psycopg 3 驱动；迁移完成后可运行
`backend/scripts/check_postgres_database.py` 验证连接和核心表。

Chroma 单实例默认使用本地 `PersistentClient`；多实例可设置 `CHROMA_BACKEND=http`、
`CHROMA_HOST`、`CHROMA_PORT` 和 `CHROMA_SSL`，让所有 Worker 连接同一个 Chroma 服务。

PostgreSQL 环境准备好后，可运行 `backend/scripts/check_postgres_checkpoint.py --setup`
验证 checkpoint schema、写入和读取；命令不会输出连接串。

## 验证

```powershell
cd backend
uv run pytest
uv run alembic check

cd ..\frontend
npm run typecheck
npm run build
```

离线质量契约和真实路由评测：

```powershell
cd backend
uv run python scripts/evaluate_quality.py
uv run python scripts/evaluate_supervisor_routes.py  # 需要 LLM_MODE=openai 和本地认证
```

Supervisor 只做语义路由，Worker 生成业务结果，Output Sanitizer 是唯一用户输出出口。详细状态所有权和恢复流程见 `backend/docs/langgraph-architecture.md`。API 层不包含关键词或正则意图路由。
