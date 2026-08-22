# 本地快速启动

这份说明面向 Windows 个人电脑单实例使用。项目默认使用 SQLite、SqliteSaver、本地文件上传目录和本地 ChromaDB，不需要 PostgreSQL、MinIO、Docker 或第三方 API Key 就可以用 Stub 模式启动。

## 前置条件

- Windows 10/11。
- Python 3.11 或 3.12。
- `uv`（用于读取 `backend/uv.lock` 并管理 Python 环境）。
- Node.js 20 或更高版本（包含 npm）。
- PowerShell 5.1 或更高版本。

首次安装 `uv` 可参考官方安装说明：<https://docs.astral.sh/uv/getting-started/installation/>。安装 Python 和 Node.js 后，请重新打开 PowerShell，让 PATH 生效。

## 首次启动

在仓库根目录执行：

```powershell
.\scripts\local.ps1 -Action start
```

启动器会依次完成：检查依赖、从 `backend/.env.example` 创建（但不覆盖）`backend/.env`、按锁定文件安装依赖、执行 Alembic 迁移、启动 FastAPI 和 Vite，并等待三个健康地址就绪。

启动成功后访问：

- 工作台：<http://127.0.0.1:5173/>
- OpenAPI：<http://127.0.0.1:8000/docs>
- 后端存活：<http://127.0.0.1:8000/api/health/live>
- 后端就绪：<http://127.0.0.1:8000/api/health/ready>

如果 PowerShell 阻止本地脚本，可以只对当前窗口放行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

日常操作：

```powershell
.\scripts\local.ps1 -Action check
.\scripts\local.ps1 -Action stop
```

已经安装过依赖时可以跳过安装：

```powershell
.\scripts\local.ps1 -Action start -SkipInstall
```

自定义端口时，脚本只为本次启动设置进程环境，不会修改 `.env`：

```powershell
.\scripts\local.ps1 -Action start -BackendPort 8001 -FrontendPort 5174
```

此时访问 `http://127.0.0.1:5174/`，OpenAPI 地址为 `http://127.0.0.1:8001/docs`。

## 模型配置

首次启动默认是 `LLM_MODE=stub`，使用确定性离线结果，不需要任何密钥。需要真实模型时，编辑本机未纳入 Git 的 `backend/.env`：

```dotenv
LLM_MODE=openai
OPENAI_MODEL=你的模型名称
OPENAI_BASE_URL=兼容服务地址（如需要）
OPENAI_API_KEY=本机环境中的真实密钥
```

真实密钥不得写入 `backend/.env.example`、README、日志、截图或 commit。LangSmith 优先用本机 OAuth；如果使用环境变量，也只在本机配置 `LANGSMITH_API_KEY`。

个人电脑单机不要切换以下设置：

- `CHECKPOINT_BACKEND=postgres`
- `UPLOAD_STORAGE_BACKEND=s3`
- `CHROMA_BACKEND=http`

它们只用于未来的共享部署拓扑。

## 本地数据

停止后端后，以下目录和文件构成本地工作区数据：

```text
backend/data/app.db          业务数据库
backend/data/langgraph.db    LangGraph checkpoint
backend/data/uploads/        上传的原始文件
backend/data/chroma/         简历向量索引
```

启动日志和脚本 PID 位于 `output/local/`：

```text
backend.log / backend.err
frontend.log / frontend.err
backend.pid / frontend.pid
```

`-Action stop` 只停止启动器记录的进程，不删除 `backend/data`。如果端口被占用，启动器会报告端口和 PID，不会结束无关进程；请使用其他端口或手动处理占用进程。

## 备份、恢复和清理

先停止服务，再创建备份：

```powershell
cd backend
uv run python scripts/manage_local_data.py backup --output ..\career-agent-backup.zip
```

恢复前必须关闭后端。`--force` 会先把现有目标保留为 `.pre-restore-*`，再恢复备份：

```powershell
uv run python scripts/manage_local_data.py restore --input ..\career-agent-backup.zip --force
```

历史清理默认只预览，确认后才使用 `--apply`：

```powershell
uv run python scripts/maintain_local_data.py
uv run python scripts/maintain_local_data.py --apply --retention-days 30
```

清理只涉及终态任务、幂等记录和 SSE 事件等可再生元数据，不删除简历、JD、上传文件、面试复盘或向量索引。

## 故障排查

### 启动器提示缺少依赖

安装对应版本后重新打开 PowerShell，再执行 `-Action start`。脚本不会替你安装无版本约束的全局依赖。

### 数据库迁移失败

查看 `output/local/backend.err` 和 `backend.log`。确认没有另一个后端正在使用同一份 SQLite 数据；先执行 `-Action stop`，再重试。不要在服务运行时手动恢复备份。

### `LLM_MODE=openai` 启动失败

确认 `backend/.env` 中存在非空 `OPENAI_API_KEY`；如果服务使用兼容接口，同时确认 `OPENAI_BASE_URL` 和模型名称正确。日志不会打印密钥值。

### 简历索引或模型任务失败

先在工作台查看任务错误和可重试状态，再检查 `backend.log`。确认 `backend/data/uploads/` 和 `backend/data/chroma/` 可读写；Stub 模式可用于区分本地流程问题和第三方模型问题。

### SSE 断开

执行 `-Action check` 确认后端 ready，再刷新工作台。SSE 支持断线后的事件续传；若后端被关闭，先查看后端日志再重新执行 `start`。

## Docker 单实例（可选）

如果已安装 Docker Desktop，可以使用现有的单实例 Compose。首次执行：

```powershell
Copy-Item backend\.env.example backend\.env
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

访问 <http://127.0.0.1:8080/>。后端容器的 readiness 通过后，前端容器才会正常提供页面。查看日志：

```powershell
docker compose logs -f backend
docker compose logs -f frontend
```

停止服务：

```powershell
docker compose down
```

`backend_data` 卷保存 SQLite、checkpoint、上传目录和 Chroma 数据。Docker 单实例适合希望隔离运行环境的用户；PowerShell 启动适合本地开发和直接查看日志。多实例模板、PostgreSQL、S3/MinIO 和 HTTP Chroma 不属于本阶段的本地验收范围。

## 开发者验证

```powershell
cd backend
uv run pytest
uv run alembic check

cd ..\frontend
npm run typecheck
npm run build
```
