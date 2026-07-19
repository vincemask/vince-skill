# 输出契约

## 两阶段本地产物

采集阶段创建暂存运行目录：

```text
<run-dir>/
├── manifest.json
├── report.json
├── editorial-input.json
├── run-config.json
└── raw/github.json
```

`editorial-input.json` 只包含排名前 10 的 URL-backed GitHub 项目信号，每个话题最多保留 3 条同渠道证据。`required_topic_count` 是 `editorial.json` 必须提供的精确数量；不足 10 时不得补足。

Codex 写入 `editorial.json` 后，`finalize_ai_trends.py` 增加：

```text
<run-dir>/
├── editorial.json
├── drafts.json
├── report.md
├── x-drafts.md
├── obsidian-note.md
├── obsidian-publish.json
└── finalized.json
```

终稿成功后才更新 `latest.json` 和 `latest-*` 文件。采集阶段只更新 `latest-collection.json`。

## editorial.json

顶层字段为 `schema_version: 1`、`run_id`、`language: zh-CN` 和 `items`。每个 item 必须按排名顺序包含：

- `rank`、`topic_id`；
- 8–28 字的 `title_zh`；
- 20–50 字、无 URL 的 `recommendation_reason`；
- 120–180 个非空白字符的中文 `x_post` 正文；
- evidence 中存在的 `primary_url`。

`x_post` 只包含一个 URL，最后一个非空行必须是该 URL。正文不得使用 Markdown、多个 hashtag、堆叠 emoji 或已禁止的泛化模板。

## 精简 Markdown

`report.md`、`x-drafts.md` 与 Obsidian 正文共享以下结构：

````markdown
# AI 热点与 X 成稿

> 本次共 N 个可靠话题 · 数据状态：完整

## 01｜中文话题标题

**推荐理由：** 一句话推荐理由。

**X 成稿：**

```text
具体中文长帖。

https://主来源
```
````

正常状态不渲染来源表、完整指标、请求诊断或局限说明。`partial`/`failed` 与不足 10 个的话题提示合并为同一条 warning callout，避免重复告警。完整审计数据继续保留在 `report.json` 和 `raw/`。

## drafts.json

每条 draft 包含 `id`、`rank`、`topic_id`、`title_zh`、`recommendation_reason`、`text`、`primary_url`、字符计数和单一 `sources` 对象。顶层固定记录：

- `language: zh-CN`；
- `mode: long`；
- 正文与推荐理由长度边界；
- `max_hashtags: 1`。

人工修改后必须运行 `validate_x_drafts.py`。

## Obsidian 发布

目标仍为 `wiki:raw/trend-YYYY-MM-DD-HHMMSS.md`。Frontmatter 保留运行、健康和数量字段，但 `sources` 只列最终主来源 URL。

`index.md` 条目格式：

```markdown
- [[raw/trend-YYYY-MM-DD-HHMMSS]] — AI 趋势采集：N 个推荐话题与 N 条 X 成稿。
```

所有 Vault 操作仍只允许通过 Obsidian CLI。发布计划必须在终稿校验通过后生成；发布成功的运行禁止重新终稿化。
