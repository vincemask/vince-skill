# 输出契约

## 采集产物

采集阶段创建不可变运行目录：

```text
<run-dir>/
├── manifest.json
├── report.json
├── editorial-input.json
├── run-config.json
└── raw/{reddit,x,github}.json
```

`editorial-input.json` 只包含排名前 10 的 URL-backed 话题。`required_topic_count` 是 `editorial.json` 必须提供的精确数量；不足 10 时不得补足。

## 终稿产物

Codex 写入 `editorial.json` 后，运行 `finalize_ai_trends.py` 生成：

```text
<run-dir>/
├── editorial.json
├── drafts.json
├── report.md
├── x-drafts.md
└── finalized.json
```

终稿命令标准输出是 JSON，其中 `content` 必须与 `report.md` 完全一致。调用方直接把 `content` 返回给用户，不再执行发布或归档阶段。

## editorial.json

顶层字段为 `schema_version: 1`、`run_id`、`language: zh-CN` 和 `items`。每个 item 必须按排名顺序包含：

- `rank`、`topic_id`；
- 8–28 字中文 `title_zh`；
- 20–50 字、无 URL 的 `recommendation_reason`；
- 120–180 个非空白字符的中文 `x_post`；
- evidence 中存在的 `primary_url`。

`x_post` 只包含一个 URL，最后一个非空行必须是该 URL。正文不得使用 Markdown、多个 hashtag、堆叠 emoji 或已禁止的泛化模板。

## 直接返回正文

`content`、`report.md` 与 `x-drafts.md` 使用相同结构：

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

正常状态不渲染来源表、完整指标、请求诊断或局限说明。`partial`、`failed` 或不足 10 个时只生成一组 warning，完整审计数据继续保留在 JSON。

## drafts.json

每条 draft 包含 `id`、`rank`、`topic_id`、`title_zh`、`recommendation_reason`、`text`、`primary_url`、字符计数和单一 `sources` 对象。人工修改后必须运行 `validate_x_drafts.py`。
