# Docker 部署指南

## 单实例部署

适合本地开发或单用户使用，使用 SQLite + 本地文件存储。

### 前置条件

- Docker Desktop（包含 Docker Compose）
- 至少 2GB 可用内存

### 启动步骤

**1. 配置环境变量**

```powershell
Copy-Item backend\.env.example backend\.env
```

编辑 `backend\.env`，至少设置：

```bash
LLM_MODE=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

**2. 验证配置**

```powershell
docker compose config --quiet
```

**3. 启动服务**

```powershell
docker compose up --build
```

首次启动会：
- 构建前后端镜像（约 3-5 分钟）
- 执行数据库迁移
- 创建 `backend_data` 卷（持久化数据）

**4. 访问应用**

访问 `http://127.0.0.1:8080/`

后端健康检查通过后 Nginx 才会启动前端服务。

### 日常操作

**查看日志**：

```powershell
docker compose logs -f backend
docker compose logs -f frontend
```

**停止服务**：

```powershell
docker compose down
```

**清理数据**（⚠️ 会删除所有数据）：

```powershell
docker compose down -v
```

### 架构说明

- **Nginx** (`127.0.0.1:8080`)：前端静态文件 + `/api/` 反向代理
- **Backend** (内部 `8000`)：FastAPI + LangGraph
- **Frontend** (构建后静态文件)：React 生产构建
- **数据卷** `backend_data`：SQLite、checkpoint、上传文件、Chroma 索引

Nginx 对 `/api/` 关闭代理缓冲并保持长连接，确保 SSE 不被聚合。

---

## 多实例部署

适合生产环境或多用户场景，使用 PostgreSQL + S3 + HTTP Chroma。

### 前置条件

- PostgreSQL 12+
- S3 兼容存储（AWS S3 / MinIO）
- Chroma HTTP 服务（可选，推荐）

### 架构变化

| 组件 | 单实例 | 多实例 |
|---|---|---|
| 业务数据库 | SQLite | PostgreSQL |
| Checkpoint | SqliteSaver | PostgreSQL |
| 文件存储 | 本地目录 | S3/MinIO |
| 向量库 | 本地 Chroma | HTTP Chroma |
| 后端实例数 | 1 | 2+ |

### 配置步骤

**1. 准备外部服务**

PostgreSQL 创建两个数据库：

```sql
CREATE DATABASE jobpilot;
CREATE DATABASE jobpilot_checkpoint;
```

**2. 配置环境变量**

编辑 `backend/.env`：

```bash
# 业务数据库
DATABASE_URL=postgresql+psycopg://user:pass@postgres:5432/jobpilot

# Checkpoint
CHECKPOINT_BACKEND=postgres
GRAPH_CHECKPOINT_DATABASE_URL=postgresql+psycopg://user:pass@postgres:5432/jobpilot_checkpoint
AUTO_CREATE_CHECKPOINT_SCHEMA=true

# S3 存储
UPLOAD_STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://s3.amazonaws.com
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=jobpilot-uploads

# HTTP Chroma
CHROMA_BACKEND=http
CHROMA_HOST=chroma-server
CHROMA_PORT=8000

# 迁移控制
RUN_MIGRATIONS=false  # 多实例必须关闭
```

**3. 执行迁移**

单独运行迁移任务：

```powershell
docker compose -f docker-compose.multi-instance.yml --profile ops run --rm migrate
```

**4. 启动服务**

```powershell
docker compose -f docker-compose.multi-instance.yml up --build
```

默认启动 2 个后端实例 + Nginx 负载均衡。

### 验证

**检查 PostgreSQL 连接**：

```powershell
cd backend
uv run python scripts/check_postgres_database.py
```

**检查 Checkpoint Schema**：

```powershell
uv run python scripts/check_postgres_checkpoint.py --setup
```

### 扩缩容

修改 `docker-compose.multi-instance.yml` 中的 `replicas`：

```yaml
services:
  backend:
    deploy:
      replicas: 3  # 调整实例数
```

---

## 故障排查

### 后端健康检查失败

```powershell
# 查看详细日志
docker compose logs backend | tail -50

# 手动测试健康检查
curl http://127.0.0.1:8000/api/health/ready
```

常见原因：
- 数据库连接失败（检查 `DATABASE_URL`）
- Checkpoint 建表失败（检查 `AUTO_CREATE_CHECKPOINT_SCHEMA`）
- 文件存储不可写（检查目录权限或 S3 配置）

### Nginx 502 Bad Gateway

后端未就绪。等待后端健康检查通过后 Nginx 会自动恢复。

### SSE 事件中断

检查 Nginx 配置是否包含：

```nginx
proxy_buffering off;
proxy_read_timeout 3600s;
```

---

## 生产环境建议

- 使用 PostgreSQL 而非 SQLite
- 启用 S3 存储（备份、共享）
- 配置 Nginx 访问日志和错误日志
- 定期备份 PostgreSQL 数据库
- 监控 Chroma 索引大小

详细的生产存储方案见 `backend/docs/production-storage.md`。
