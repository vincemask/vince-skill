---
name: collect-ai-trends
description: 通过 opencli 和 gh CLI 稳定采集 Reddit、GitHub 与知名 X 账号的最新 AI 话题，生成带来源的中文趋势报告和中文 X 草稿，并通过 Obsidian CLI 强制发布到指定 Vault。适用于 AI 趋势监控、每日或每周 AI 情报报告、跨来源热点聚合、X 选题草拟以及将采集产物归档到 Obsidian。
---

# AI 趋势采集与 Obsidian 发布

使用随 Skill 提供的脚本执行完整工作流，不要临时拼接采集管道。所有面向用户的报告、说明和 X 草稿使用简体中文；原始 URL、产品名、账号名和原始标题可以保留原文。

将包含本文件的安装目录解析为 `<skill-dir>`。始终用绝对路径调用脚本，使工作流不依赖当前工作目录。

## 强制工作流

1. 首次运行、认证变化或采集失败后，先检查 opencli 和 gh：

   ```bash
   python3 <skill-dir>/scripts/collect_ai_trends.py --preflight
   ```

2. 运行采集器。默认配置固定使用 Obsidian Vault `wiki` 和既有目录 `raw/`：

   ```bash
   python3 <skill-dir>/scripts/collect_ai_trends.py \
     --output-dir /absolute/path/to/ai-trend-output
   ```

3. 从采集器标准输出读取 `obsidian_publish_plan`，或读取 `<run-dir>/obsidian-publish.json`。只要发布计划已经生成，即使采集器因 `partial`、`failed` 或 `--strict` 返回非零状态，也要继续执行 Obsidian 发布，以保留诊断记录。

4. 运行确定性发布脚本：

   ```bash
   python3 <skill-dir>/scripts/publish_obsidian.py \
     /absolute/path/to/<run-dir>/obsidian-publish.json
   ```

5. 回读 `<run-dir>/obsidian-publish-result.json`。只有当 `status` 为 `published` 且发布脚本返回 0 时，才可以把本次工作流报告为成功。向用户交付 Obsidian wikilink、健康状态、热点数、草稿数和本地运行目录。

Obsidian 发布是强制步骤。不得只生成本地报告后宣告完成，也不得在发布失败时静默降级。

## Obsidian 规则

- 默认 Vault：`wiki`。
- 默认目标目录：`raw`。该目录必须已存在；禁止创建任何 Vault 目录。
- 每次运行只发布一篇合并笔记，路径为 `raw/trend-YYYY-MM-DD-HHMMSS.md`，时间使用 `Asia/Shanghai`。
- JSON、缓存和原始响应只保留在本地运行目录，不放入 Vault。
- 对趋势笔记、`index.md`、`log.md` 的检查、创建、覆盖、追加和回读必须全部通过 Obsidian CLI。严禁用 Python、Shell 或文件系统 API 直接读写 Vault。
- 发布脚本先验证目标目录、`index.md` 和 `log.md`，再创建或复用同一 `run_id` 的笔记；随后补全索引与日志，最后回读三处内容。
- 不做自动回滚。中途失败时保留已完成步骤；使用同一份 `obsidian-publish.json` 重试，脚本会依据 `run_id`、wikilink 和日志标记继续补全，不重复创建或计数。
- 如果目标路径已经属于其他 `run_id`，立即失败，不覆盖冲突笔记。

在 Codex 沙箱内，如果 Obsidian CLI 返回 134 或被系统限制，发布脚本必须在获得用户批准后，以完全相同的脚本路径和发布计划在沙箱外重新执行。不要改用直接 Vault 文件操作绕过限制。

只读检查可以单独运行：

```bash
python3 <skill-dir>/scripts/publish_obsidian.py \
  /absolute/path/to/<run-dir>/obsidian-publish.json \
  --preflight
```

## 配置

默认配置位于 `references/default-config.json`。需要修改时复制到 Skill 目录之外，凭据不得写入配置。

- `reddit.subreddits`：Reddit 社区列表。
- `x.accounts`：重点 X 账号列表。
- `github.queries`：GitHub Trending 代理查询。
- `document_language` 与 `drafts.language` 必须为 `zh-CN`。
- `obsidian.enabled` 和 `obsidian.strict` 必须为 `true`。
- `obsidian.vault`、`obsidian.target_directory`、`obsidian.index_path`、`obsidian.log_path` 只能使用 Vault 名称或 Vault 内相对路径，拒绝绝对路径和 `..`。

可在单次运行中覆盖 Vault 和目录：

```bash
python3 <skill-dir>/scripts/collect_ai_trends.py \
  --config /absolute/path/to/config.json \
  --output-dir /absolute/path/to/ai-trend-output \
  --obsidian-vault wiki \
  --obsidian-dir raw
```

覆盖值仍必须通过发布脚本的真实 Vault 与目录预检。不存在的 Vault 或目录会导致发布失败。

## 健康状态与严格模式

- `complete`：每个配置请求均返回最新数据。
- `partial`：至少一个请求使用缓存或失败，但仍有可用信号。可以发布，但笔记必须显示警告。
- `failed`：没有可用信号。仍发布包含诊断的笔记，但不得生成无来源事实或草稿。
- 采集器的 `--strict` 控制数据不完整是否令采集命令失败；它不取消 Obsidian 发布。
- `obsidian.strict` 控制发布契约，固定为 `true`；任何发布或最终验证失败都令整个工作流失败。

不要把缓存数据描述为最新数据，不要从标题单独推断事实，不要删除草稿中的来源链接。该 Skill 只生成 X 草稿，不会发布、点赞、转发或回复。

## 失败处理

- opencli 守护进程或浏览器桥接失败：运行 `opencli doctor`，恢复连接后用同一配置重试，不要静默替换为网页搜索。
- `gh auth status` 失败：让用户通过 `gh auth login` 恢复认证，不索取或回显令牌。
- Obsidian 预检失败：保留本地运行目录与 `obsidian-publish-result.json`，修复 CLI、Vault、目录或索引后使用同一发布计划重试。
- 索引或日志失败：不要删除已创建的趋势笔记；重试会补全缺失步骤。
- 回读摘要不一致：停止并报告冲突，不要用 `overwrite` 强行替换趋势笔记。

## 确定性验证

修改脚本、配置、排序、模板或发布逻辑后，运行完整测试：

```bash
PYTHONPYCACHEPREFIX=/tmp/collect-ai-trends-pycache \
python3 -m unittest discover -s <skill-dir>/scripts/tests -v
```

测试必须使用伪 Obsidian CLI，不能向真实 Vault 写入测试数据。真实环境只先做 `--preflight`；只有 opencli、gh 和 Obsidian CLI 全部通过后，才执行真实数据采集与发布。
