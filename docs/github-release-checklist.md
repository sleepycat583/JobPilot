# JobPilot GitHub 发布检查清单

这份清单用于首次公开 JobPilot 仓库。完成全部检查并得到项目所有者明确确认之前，不得配置远程仓库或执行 push。

## 当前身份边界

- Git 提交作者和提交者必须是项目所有者本人。
- 不添加 `Co-authored-by: Claude`、`Co-authored-by: Codex` 或其他 AI/协作者 trailer。
- 发布前检查 `git log --all --format='%an <%ae>' | Sort-Object -Unique`，只允许项目所有者的身份。
- GitHub 仓库创建后，Settings -> Collaborators 中只保留项目所有者，不邀请 Claude、Codex 或其他账号。
- 本地使用 AI 工具不等于 GitHub Contributor；Contributor 由 Git commit 作者身份决定。

## 安全检查

- [ ] `backend/.env`、系统环境变量和 OAuth 凭据未进入 Git。
- [ ] 使用 `gitleaks` 或同等工具扫描当前文件和完整历史。
- [ ] 从历史中移除 `output/` 截图及其他个人业务数据。
- [ ] `backend/data/`、Chroma、SQLite、上传文件、日志和备份均未被跟踪。
- [ ] README、日志和提交信息不包含真实密钥。

## 发布前验证

```powershell
git status --short --branch
git diff --check
git ls-files | Select-String -Pattern '(^|/)(\.env|data|output|node_modules|\.venv)(/|$)'
git log --all --format='%an <%ae>' | Sort-Object -Unique
git log --all --full-history --name-status -- '*env*' '*secret*' '*credential*' '*key*'
gitleaks git -v --redact
```

## 首次公开顺序

1. 备份本地仓库。
2. 用 `git filter-repo` 清理 `output/` 等敏感历史：

   ```powershell
   git clone --mirror . ..\JobPilot-pre-filter-backup.git
   git filter-repo --path output/ --invert-paths --force
   git log --all -- output/
   git fsck --full --no-reflogs
   ```

   `git log --all -- output/` 应无输出。备份镜像只用于恢复，不得上传。
3. 重新执行安全扫描、测试和构建。
4. 在 GitHub 创建空仓库 `JobPilot`，不自动添加 README、License 或 `.gitignore`。
5. 确认仓库权限中只有项目所有者。
6. 配置 `origin`，检查 `git remote -v`。
7. 项目所有者明确确认后，才执行首次 push。
