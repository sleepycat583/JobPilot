# Phase 9: Local Privacy and Data Management

第九阶段只服务于个人电脑单实例模式。在线功能不会替换业务 SQLite、LangGraph checkpoint、上传文件或 Chroma 索引。

## 交付内容

- `GET /api/local-data/summary`：返回本地资源数量与各类存储体积，不暴露物理路径、`.env` 内容或密钥。
- `POST /api/local-data/backup`：创建包含业务数据库、checkpoint、上传文件和向量索引的一致性 ZIP 备份；备份包不包含 `.env`。
- `GET /api/local-data/cleanup-preview`：预览过期的终态任务、幂等记录和 SSE 事件。
- `POST /api/local-data/cleanup`：需要 `Idempotency-Key` 与固定确认值 `DELETE_LOCAL_HISTORY`；不会删除简历、JD、上传文件、面试复盘或向量索引。
- 工作台“更多设置”入口新增“隐私与数据”页，显示本地存储规模、模型处理边界、备份下载、保留期预览和确认清理。

## 恢复边界

恢复继续使用 `scripts/manage_local_data.py restore`。恢复会替换多个相互关联的本地持久化目录，必须在后端已停止时执行；因此不提供会在运行中的应用内覆盖数据的 HTTP API。

## 验收

```powershell
cd backend
uv run pytest
uv run alembic check

cd ..\frontend
npm run build
```

浏览器证据位于 `output/playwright/`：

- `phase9-local-data-desktop.png`：桌面端本地数据概览与备份控制。
- `phase9-local-data-mobile.png`：移动端数据管理布局。
- `phase9-cleanup-confirmation-desktop.png`：清理确认值未输入时，破坏性操作保持禁用。
