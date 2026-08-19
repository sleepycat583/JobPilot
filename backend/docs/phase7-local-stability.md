# 阶段 7：本地稳定性与恢复

本阶段针对个人电脑单用户运行，不引入 PostgreSQL、S3/MinIO、远程 Chroma 或多实例部署。
默认数据拓扑保持为：SQLite 业务库、SqliteSaver checkpoint、本地上传目录和本地 Chroma。

## 已实现

- 启动时校验后端选择所需的关键环境变量；配置错误会在 lifespan 初始化前失败，不会接收请求。
- 本地 SQLite/本地文件/本地 Chroma 模式下，进程重启会把上一次留下的 `running` 任务重新置为
  `queued`，不必等待跨实例租约超时，然后继续处理简历和 JD 任务。
- 失败的简历模型、向量索引和 JD 模型任务可通过 `POST /api/jobs/{job_id}/retry` 重试。
  接口要求 `Idempotency-Key`，同一 key 重试返回同一响应；不可重试的错误会明确拒绝。
- SSE 回放继续使用 `Last-Event-ID`，回放数量和数据库检查间隔由
  `SSE_REPLAY_BATCH_SIZE`、`SSE_POLL_INTERVAL_SECONDS` 控制，默认不进行高频状态轮询。
- 健康检查继续区分进程存活和依赖就绪：数据库、checkpoint、上传存储或 Chroma 不可用时，
  `/api/health/ready` 返回可重试的 503。
- 提供本地数据备份和恢复命令，SQLite 使用在线 backup API 生成一致快照，上传目录和 Chroma
  一并归档；恢复默认拒绝覆盖，`--force` 会先把旧目标移动到 `.pre-restore-*` 文件或目录。
- 提供本地历史维护命令：默认只预览超过保留期的终态任务、SSE 事件和幂等记录；只有传入
  `--apply` 才会删除。它不会删除简历、JD、对话、上传文件、向量或 checkpoint。

## 备份与恢复

从 `backend` 目录执行：

```powershell
uv run python scripts/manage_local_data.py backup --output ..\career-agent-backup.zip
uv run python scripts/manage_local_data.py restore --input ..\career-agent-backup.zip --force
```

备份包含业务 SQLite、LangGraph checkpoint、上传文件和本地 Chroma。命令只输出条目数量，
不会输出连接串、模型 payload 或任何密钥。执行恢复前应停止后端进程，恢复后再运行：

```powershell
uv run alembic upgrade head
uv run pytest
```

备份命令只支持本地存储拓扑；如果配置为 S3、PostgreSQL checkpoint 或 HTTP Chroma，会直接拒绝，
避免生成看似完整但实际缺少共享数据的备份。

清理可再生历史记录，默认保留 30 天，可在 `.env` 中设置 `LOCAL_HISTORY_RETENTION_DAYS`：

```powershell
uv run python scripts/maintain_local_data.py
uv run python scripts/maintain_local_data.py --apply --retention-days 30
```

## 验收证据

- 后端回归覆盖启动配置、租约恢复、重试幂等、SSE `Last-Event-ID` 回放和备份恢复。
- 真实业务 E2E 仍作为阶段回归门禁，确保稳定性改动没有破坏简历、JD、匹配和面试主流程。
