# 配置说明

## 环境变量

JobPilot 通过环境变量控制运行模式和外部服务集成。

### 必需配置（Stub 模式）

首次本地启动默认使用 `LLM_MODE=stub`，不需要第三方 API Key：

```bash
# backend/.env
LLM_MODE=stub
DATABASE_URL=sqlite:///./career_agent.db
```

### OpenAI-Compatible 模式

启用真实 LLM 需要以下配置：

```bash
LLM_MODE=openai
OPENAI_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1  # 可选，默认 OpenAI 官方
```

第三方兼容服务（如 Azure、通义千问、Deepseek）通过 `OPENAI_BASE_URL` 接入。

### 可选配置

**LangSmith 追踪**（Agent 调用链监控）：

```bash
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=jobpilot
```

认证优先使用本机 OAuth，无需配置 `LANGSMITH_API_KEY`。

**企查查 API**（雇主背调）：

```bash
QCC_API_KEY=your_qcc_key
QCC_API_SECRET=your_qcc_secret
```

未配置时雇主背调功能不可用。

### 多实例部署配置

**PostgreSQL 业务数据库**：

```bash
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname
```

**PostgreSQL Checkpoint**：

```bash
CHECKPOINT_BACKEND=postgres
GRAPH_CHECKPOINT_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/checkpoint_db
AUTO_CREATE_CHECKPOINT_SCHEMA=true  # 首次建表
```

**S3/MinIO 对象存储**：

```bash
UPLOAD_STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://s3.amazonaws.com
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=jobpilot-uploads
S3_REGION=us-east-1
```

**HTTP Chroma**：

```bash
CHROMA_BACKEND=http
CHROMA_HOST=chroma-server
CHROMA_PORT=8000
CHROMA_SSL=false
```

### 安全提示

- 所有真实密钥只允许存在于本机 `backend/.env` 或系统环境变量中
- 不要把密钥写入 `docker-compose.yml`、镜像层或 Git
- `.env` 文件已在 `.gitignore` 中排除

### 配置验证

检查配置是否生效：

```powershell
cd backend
uv run python -c "from app.core.config import settings; print(f'LLM_MODE: {settings.LLM_MODE}')"
```

健康检查接口：

- `GET /api/health/live` — 进程存活
- `GET /api/health/ready` — 数据库、checkpoint、文件存储就绪
