# 生产存储边界

当前默认配置是单实例部署：业务数据使用 SQLite，LangGraph checkpoint 使用
`SqliteSaver`，上传文件和 Chroma 使用本地持久化目录。Compose 只运行一个后端 worker，
这是经过验证的默认路径。

## Checkpoint 后端

通过环境变量显式选择：

```dotenv
CHECKPOINT_BACKEND=sqlite
GRAPH_CHECKPOINT_PATH=./data/langgraph.db
AUTO_CREATE_CHECKPOINT_SCHEMA=false
```

生产多实例可以切换到 PostgreSQL：

```dotenv
CHECKPOINT_BACKEND=postgres
GRAPH_CHECKPOINT_DATABASE_URL=postgresql://user:password@db:5432/career
AUTO_CREATE_CHECKPOINT_SCHEMA=false
```

PostgreSQL 使用 `langgraph-checkpoint-postgres==2.0.21`，与当前锁定的
`langgraph-checkpoint==2.1.2` 兼容。应用不会在配置错误时自动回退到 SQLite；缺少
`GRAPH_CHECKPOINT_DATABASE_URL` 会直接导致启动失败，避免多个实例各自写入本地 checkpoint。

首次部署需要在受控迁移步骤中创建 checkpoint 表。开发环境可以临时设置
`AUTO_CREATE_CHECKPOINT_SCHEMA=true`，生产环境建议使用一次性管理命令或受控发布步骤，
并保持该开关为 `false`。业务表仍由 `alembic upgrade head` 管理，二者是两套独立的 schema 生命周期。
`/api/health/ready` 会执行一次只读 checkpoint 查询；如果表不存在或数据库不可用，服务会返回
`CHECKPOINT_NOT_READY`，不会把未完成初始化的实例交给前端流量。

## 多实例迁移边界

切换 checkpoint 后端并不自动解决其他本地状态：

1. `DATABASE_URL` 需要迁移到 Postgres，并在所有实例使用同一数据库。
2. `UPLOAD_DIR` 需要对象存储或共享文件系统，不能依赖容器本地磁盘。
3. `CHROMA_PERSIST_DIRECTORY` 需要共享 Chroma 服务或集中式向量库；多个进程不能各自使用独立本地 collection。
4. 后台任务与 SSE 事件需要共享任务/事件存储，并重新验证 worker 并发和恢复语义。
5. 完成上述迁移并通过回归后，才可以把 Uvicorn worker 数量从 1 调高。

## 当前验证范围

已验证 `PostgresSaver.from_conn_string()` 和 `.setup()` 在当前 Python 3.11、LangGraph
checkpoint 依赖组合下可导入。当前工作区没有运行中的 PostgreSQL，因此尚未声称已经完成
真实数据库连接、迁移和多实例压测；这些属于下一阶段的部署验证任务。
