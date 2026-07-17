---
name: collect-ai-trends
description: 通过 opencli 和 gh CLI 采集 Reddit、GitHub 与知名 X 账号的最新 AI 话题，由 Codex 基于证据撰写最多 10 条中文 X 长帖与推荐理由，校验后通过 Obsidian CLI 归档。适用于 AI 趋势监控、每日或每周选题、中文 X 内容策划、跨来源热点筛选和 Obsidian 情报归档。
---

# AI 趋势采集与 X 成稿

执行“采集 → 编辑 → 终稿 → Obsidian 发布”四个阶段。不要跳过编辑或终稿校验，也不要把采集器的机器诊断直接复制给用户。

将本文件所在目录解析为 `<skill-dir>`，始终使用脚本绝对路径。

## 1. 采集证据

首次运行或认证变化后先预检：

```bash
python3 <skill-dir>/scripts/collect_ai_trends.py --preflight
```

运行采集：

```bash
python3 <skill-dir>/scripts/collect_ai_trends.py \
  --output-dir /absolute/path/to/ai-trend-output
```

读取标准输出中的 `editorial_input`。采集器只生成原始审计数据、`report.json`、`editorial-input.json` 和配置快照，不会生成可发布内容。

## 2. 编写 editorial.json

读取 `<run-dir>/editorial-input.json`，按给定顺序处理全部 `topics`。数量必须等于 `required_topic_count`；不足 10 个时不得补造话题。

在 `<run-dir>/editorial.json` 写入：

```json
{
  "schema_version": 1,
  "run_id": "与 editorial-input.json 一致",
  "language": "zh-CN",
  "items": [
    {
      "rank": 1,
      "topic_id": "按输入原样保留",
      "title_zh": "8 至 28 字中文标题",
      "recommendation_reason": "20 至 50 字推荐理由",
      "x_post": "120 至 180 字具体中文长帖正文\n\nhttps://主来源",
      "primary_url": "必须来自该话题 evidence"
    }
  ]
}
```

撰写规则：

- 用具体事实说明发生了什么、影响谁或改变了什么工作流，再给出简短判断。
- 推荐理由解释为什么现在值得发布，依据时效性、跨来源印证、采用信号或受众价值。
- 每条只保留一个主来源 URL，并放在最后一行。主来源优先使用项目、论文或官方仓库，其次官方 X，最后 Reddit。
- 最多使用一个 hashtag 和一个 emoji；不要使用 Markdown。
- 禁止“检测到来自”“共同信号”“建议先核对原始信息”等空泛模板。
- 不得把英文原始标题直接当作中文标题或正文，不得编造 evidence 中不存在的事实。

## 3. 生成终稿

```bash
python3 <skill-dir>/scripts/finalize_ai_trends.py \
  --run-dir /absolute/path/to/<run-dir>
```

终稿脚本验证数量、顺序、中文比例、长度、URL、重复内容和泛化模板，然后生成：

- `report.md`、`x-drafts.md`：精简的人类阅读版本；
- `drafts.json`：结构化成稿；
- `obsidian-note.md`、`obsidian-publish.json`：待发布笔记和确定性计划。

如果 `obsidian-publish-result.json` 已是 `published`，不得重新终稿化该运行。

## 4. 发布到 Obsidian

```bash
python3 <skill-dir>/scripts/publish_obsidian.py \
  /absolute/path/to/<run-dir>/obsidian-publish.json
```

只有发布脚本返回 0 且结果状态为 `published` 才算成功。Codex 沙箱阻止 Obsidian CLI 或出现退出 134 时，在获得批准后以相同命令在沙箱外重试；禁止直接读写 Vault 文件。

默认使用 `wiki` Vault 和既有 `raw/` 目录，不创建新目录。发布器根据 `run_id`、wikilink 和日志标记幂等补全趋势笔记、`index.md` 与 `log.md`。

## 用户返回格式

完整返回全部 N 个话题，不重复机器诊断：

````markdown
## 01｜中文话题标题

**推荐理由：** 为什么值得现在发布。

**X 成稿：**

```text
可复制发布的中文长帖。

https://主来源
```
````

结尾仅附 Obsidian wikilink、健康状态和“本次共 N 个可靠话题”。正常运行不显示来源表、请求记录或局限说明；`partial` 或 `failed` 只显示一条数据不完整警告。

## 可靠性

- `complete`、`partial`、`failed` 继续由 `report.json` 决定；即使采集命令因 `--strict` 非零，只要生成了运行目录，仍完成编辑、终稿和 Obsidian 诊断归档。
- 所有原始链接、指标、来源状态和请求诊断保留在本地 JSON，不进入精简正文。
- 该 Skill 只生成成稿，不会在 X 自动发布、点赞、转发或回复。
- 配置默认固定为最多 10 个话题、X 长帖模式、正文 120–180 字、推荐理由 20–50 字。

## 验证

```bash
PYTHONPYCACHEPREFIX=/tmp/collect-ai-trends-pycache \
python3 -m unittest discover -s <skill-dir>/scripts/tests -v
```

修改后还要运行 Skill 校验和真实 Obsidian 只读预检。只有 opencli、gh 和 Obsidian CLI 全部通过预检后，才执行真实数据发布。
