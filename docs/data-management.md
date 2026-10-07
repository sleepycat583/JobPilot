# 数据管理

## 数据存储位置

JobPilot 单实例使用本地文件存储，所有数据位于 `backend/` 目录下：

| 数据类型 | 位置 | 说明 |
|---|---|---|
| 业务数据库 | `career_agent.db` | SQLite，包含简历、JD、对话、任务记录 |
| LangGraph Checkpoint | `checkpoints.sqlite` | SqliteSaver，保存 Agent 执行状态 |
| 上传文件 | `uploads/` | PDF/DOCX/TXT 原始文件 |
| 向量索引 | `.chroma_data/` | ChromaDB 持久化目录 |

多实例部署使用 PostgreSQL、S3 和 HTTP Chroma，无本地数据。

---

## 备份与恢复

### 创建备份

停止后端后执行：

```powershell
cd backend
uv run python scripts/manage_local_data.py backup --output ..\career-agent-backup.zip
```

备份内容：
- `career_agent.db` — 业务数据
- `checkpoints.sqlite` — LangGraph 状态
- `uploads/` — 上传文件
- `.chroma_data/` — 向量索引

### 恢复备份

```powershell
cd backend
uv run python scripts/manage_local_data.py restore --input ..\career-agent-backup.zip --force
```

`--force` 会先保留旧数据为 `.pre-restore-*` 目录，不会静默删除。

**⚠️ 注意**：
- 恢复前必须停止后端
- 恢复会覆盖当前所有数据
- 建议先备份当前数据再恢复

---

## 在线数据管理

前端提供"隐私与数据"页面（工作台底部"更多设置"进入）：

### 功能

1. **数据规模查看**：
   - 简历、JD、对话、面试数量
   - 上传文件大小
   - 向量索引条目数
   - 模型处理模式（stub/openai）

2. **一致性备份**：
   - 下载包含所有数据的 ZIP 文件
   - 保证数据一致性（不包含运行中任务）

3. **历史清理**：
   - 预览待清理内容（默认不删除）
   - 清理过期的终态任务（completed/failed/cancelled）
   - 清理幂等记录和 SSE 事件

**不会删除的内容**：
- 简历和 JD
- 上传文件
- 面试复盘
- 向量索引

---

## 历史清理

### 命令行清理

预览待清理内容：

```powershell
cd backend
uv run python scripts/maintain_local_data.py
```

执行清理（保留 30 天内数据）：

```powershell
uv run python scripts/maintain_local_data.py --apply --retention-days 30
```

### 清理规则

| 数据类型 | 清理条件 | 是否可配置 |
|---|---|---|
| 终态任务 | 状态为 completed/failed/cancelled 且超过保留期 | ✅ |
| SSE 事件 | 关联任务已终态且超过保留期 | ✅ |
| 幂等记录 | 超过保留期 | ✅ |
| 简历/JD | 永不清理 | ❌ |
| 上传文件 | 永不清理 | ❌ |
| 对话历史 | 永不清理 | ❌ |
| 向量索引 | 永不清理 | ❌ |

---

## 任务重试

任务失败时，前端根据 `retryable` 字段显示重试按钮。

**API 接口**：

```http
POST /api/jobs/{job_id}/retry
Headers:
  Idempotency-Key: {原始幂等键}
```

重试使用原始幂等键，保证幂等性。

---

## 数据迁移

### SQLite → PostgreSQL

详见 `backend/docs/production-storage.md`。

关键步骤：
1. 导出 SQLite 数据
2. 创建 PostgreSQL 数据库
3. 配置 `DATABASE_URL`
4. 运行 Alembic 迁移
5. 导入数据

### 本地 Chroma → HTTP Chroma

1. 启动 HTTP Chroma 服务
2. 配置环境变量：
   ```bash
   CHROMA_BACKEND=http
   CHROMA_HOST=chroma-server
   CHROMA_PORT=8000
   ```
3. 重建索引（简历重新上传触发索引）

---

## 数据安全

### 隐私保护

- 简历自动清理联系方式（邮箱、手机号）
- 向量索引只保存文本 Embedding，不保存原文
- 对话历史不包含真实姓名和敏感信息

### 备份建议

- 每周备份一次（`manage_local_data.py backup`）
- 备份文件保存在仓库外（如 `D:\Backups\`）
- 测试恢复流程（至少一次）

### 密钥安全

- API Key 只存在于 `backend/.env`
- 不要把 `.env` 提交到 Git
- 备份文件不包含 `.env`

---

## 故障恢复

### 数据库损坏

1. 停止后端
2. 重命名 `career_agent.db` 为 `.corrupted`
3. 从备份恢复或重新初始化：
   ```powershell
   uv run python -m alembic upgrade head
   ```

### Checkpoint 不一致

删除 `checkpoints.sqlite`，LangGraph 会自动重建：

```powershell
Remove-Item backend\checkpoints.sqlite
```

运行中任务会丢失，但历史对话和简历不受影响。

### 向量索引损坏

删除 `.chroma_data/`，重新上传简历触发索引重建：

```powershell
Remove-Item -Recurse backend\.chroma_data
```

---

## 多实例数据管理

多实例使用云端存储，无本地备份脚本。

**PostgreSQL 备份**：

```bash
pg_dump jobpilot > backup.sql
```

**S3 备份**：

使用云服务商的快照或版本控制功能。

**Chroma 备份**：

HTTP Chroma 自身不提供备份接口，建议定期导出 PostgreSQL 中的 `wardrobe_items` 表（包含 Embedding）。
