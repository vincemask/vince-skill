# 输出契约

## 采集产物

```text
<run-dir>/
├── manifest.json
├── report.json
├── editorial-input.json
├── run-config.json
└── raw/github.json
```

`editorial-input.json` 只包含排名前 10 的 URL-backed GitHub 项目信号。`required_topic_count` 是 `editorial.json` 必须提供的精确数量；不足 10 时不得补足。

## 终稿与直接输出

Codex 写入 `editorial.json` 后，`finalize_ai_trends.py` 生成 `drafts.json`、`report.md`、`x-drafts.md` 和 `finalized.json`。终稿命令标准输出中的 `content` 必须与 `report.md` 完全一致，调用方直接把该字段返回给用户。

每条 `editorial.json` item 必须按排名包含 `rank`、`topic_id`、8–28 字中文标题、20–50 字推荐理由、120–180 字中文 `x_post` 和 evidence 中的唯一 `primary_url`。URL 必须位于正文最后一行。

正文结构固定为中文标题、推荐理由和可复制 X 成稿。正常状态不显示请求诊断或局限说明；`partial`、`failed` 或不足 10 个时只生成一组 warning。详细审计数据继续保留在 JSON。

该流程到正文输出即结束，不修改仓库、不创建 issue/PR，也不调用外部发布系统。
