# 输出契约

## 中文交付规则

- `report.md`、`x-drafts.md`、`obsidian-note.md` 和面向用户的最终回复必须使用简体中文。
- URL、产品名、账号名和标记为“原始标题”的证据引用可以保留原文。
- 不得把英文原始标题直接当作中文摘要或 X 草稿正文。
- JSON 字段名以及 `complete`、`partial`、`failed` 等枚举值保持英文，避免破坏机器接口。

## 本地稳定路径

每次调用创建不可变运行目录：

```text
<output-dir>/<run-id>/
├── manifest.json
├── report.json
├── report.md
├── drafts.json
├── x-drafts.md
├── obsidian-note.md
├── obsidian-publish.json
├── obsidian-publish-result.json    # 发布脚本运行后生成
└── raw/
    ├── github.json
    ├── reddit.json
    └── x.json
```

输出根目录还会原子替换以下便捷文件：

- `latest.json`：运行指针、健康状态和 Obsidian 目标。
- `latest-report.json`、`latest-report.md`：最新结构化报告和中文报告。
- `latest-drafts.json`、`latest-x-drafts.md`：最新结构化草稿和中文草稿。
- `latest-obsidian-note.md`：等待发布的合并笔记。
- `latest-obsidian-publish.json`：最新发布计划。

## Obsidian 发布计划

`obsidian-publish.json` 是确定性发布器的唯一输入，包含：

- `run_id`、Vault 名称、目标目录、趋势笔记路径和 wikilink；
- 本地 `obsidian-note.md` 路径与 SHA-256；
- `index.md` 与 `log.md` 路径；
- 索引条目、日志条目、日志幂等标记和必要笔记标记；
- 信号数、热点数、草稿数和上海时区日期。

发布器只读取本地计划和暂存笔记。所有 Vault 检查与变更都调用 `obsidian` 命令，不直接读取或写入 Vault 文件系统。

成功后，`obsidian-publish-result.json` 的 `status` 为 `published`，并记录已执行或复用的步骤。失败时 `status` 为 `failed`，同时记录失败阶段和经过脱敏、截断的错误；发布器返回 4。

## Obsidian 笔记契约

目标路径为 `raw/trend-YYYY-MM-DD-HHMMSS.md`，时间使用 `Asia/Shanghai`。Frontmatter 包含：

- `title`、`created`、`updated`、`type: summary`；
- `tags: [ai, monitoring, news, x, github]`；
- 去重后的 `sources` URL；
- `run_id`、`health`、`window_start`、`window_end`；
- `signal_count`、`topic_count`、`draft_count`。

正文顺序固定为：

1. `[[concepts/news-monitoring-and-growth]]`；
2. 采集概况与状态 callout；
3. 热点话题与跨来源证据；
4. 中文 X 草稿；
5. 采集诊断与局限说明。

`index.md` 的 `Raw` 区域使用：

```markdown
- [[raw/trend-YYYY-MM-DD-HHMMSS]] — AI 趋势采集：N 个热点，包含 Reddit、GitHub、X，并附 X 草稿。
```

`log.md` 记录运行编号、wikilink、信号数、热点数、草稿数、发布状态和隐藏幂等标记。

## 健康状态

- `fresh`：请求在本次运行中成功。
- `cached`：请求失败，使用了不超过 `cache_max_age_hours` 的缓存。
- `failed`：请求失败且没有可用缓存。
- `complete`：所有配置请求均为最新。
- `partial`：至少一个请求使用缓存或失败，同时存在至少一个标准化信号。
- `failed`：没有标准化信号。

## 结构化报告与草稿

`report.json` 的顶层字段保持稳定：

```json
{
  "schema_version": "1.0",
  "run": {},
  "health": {},
  "source_runs": [],
  "topics": [],
  "items": {"reddit": [], "x": [], "github": []},
  "limitations": []
}
```

消费者必须容忍新增字段，但应拒绝不支持的主版本。

每个 `drafts.json` 条目包含 `id`、`topic_id`、`text`、`character_count` 和 `sources`。有效草稿必须：

- 不超过配置的 Unicode 字符数；
- 正文包含 HTTP 来源 URL；
- 至少列出一个带 `url` 与 `source` 的来源对象；
- 不与其他草稿的规范化正文重复；
- 除 URL、产品名和账号外，自动生成文案使用简体中文。

人工修改草稿后，运行 `scripts/validate_x_drafts.py` 校验。
