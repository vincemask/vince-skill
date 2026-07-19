---
name: collect-x-ai-trends
description: 通过 opencli 独立采集 X 平台重点账号和主题查询中的最新 AI、coding agent 与 AI coding 信号，生成并直接输出最多 10 条中文 X 长帖与推荐理由。适用于 X 平台 AI 趋势监控、账号观察、每日或每周选题和中文 X 内容策划；不采集 Reddit 或 GitHub，不依赖 Obsidian。
---

# X AI 趋势采集与正文输出

执行“X 采集 → 编辑 → 终稿输出”。将本文件所在目录解析为 `<skill-dir>`，始终使用脚本绝对路径。

## 1. 采集 X 证据

首次运行或认证变化后预检：

```bash
python3 <skill-dir>/scripts/collect_x_ai_trends.py --preflight
```

运行采集：

```bash
python3 <skill-dir>/scripts/collect_x_ai_trends.py \
  --output-dir /absolute/path/to/x-ai-trend-output
```

读取标准输出中的 `editorial_input`。默认账号名单与独立主题查询见 `references/default-config.json`；调整账号、关键词或请求量前先读 `references/source-and-ranking.md`，复制配置后通过 `--config` 传入。不要启用 Reddit 或 GitHub。

## 2. 编写 editorial.json

读取 `<run-dir>/editorial-input.json`，按顺序处理全部 `topics`，数量严格等于 `required_topic_count`。不足 10 个时不得补造。

写入 `<run-dir>/editorial.json`：

```json
{
  "schema_version": 1,
  "run_id": "与输入一致",
  "language": "zh-CN",
  "items": [{
    "rank": 1,
    "topic_id": "按输入保留",
    "title_zh": "8 至 28 字中文标题",
    "recommendation_reason": "20 至 50 字推荐理由",
    "x_post": "120 至 180 字具体中文正文\n\nhttps://主来源",
    "primary_url": "必须来自该话题 evidence"
  }]
}
```

只陈述 X evidence 支持的事实。解释事件、受影响对象和发布价值；每条只保留一个主来源 URL，并放在最后一行。最多使用一个 hashtag 和一个 emoji，不使用 Markdown，不把单渠道信号称为跨来源印证。

## 3. 终稿与直接输出

```bash
python3 <skill-dir>/scripts/finalize_ai_trends.py \
  --run-dir /absolute/path/to/<run-dir>
```

终稿器验证数量、顺序、中文比例、长度、URL 和重复内容。读取命令标准输出中的 `content` 字段并直接返回，禁止调用外部发布或归档系统。需要核对完整文件契约时读取 `references/output-contract.md`。

## 用户返回

直接返回全部 N 个话题正文，每条包含中文标题、推荐理由和可复制的 X 成稿；结尾仅附健康状态和“本次共 N 个可靠话题”。`partial` 或 `failed` 只显示一条数据不完整警告。

不要自动发布、点赞、转发或回复 X 内容。

## 验证

```bash
PYTHONPYCACHEPREFIX=/tmp/collect-x-ai-trends-pycache \
python3 -m unittest discover -s <skill-dir>/scripts/tests -v
```

修改后同时运行 Skill 结构校验；真实采集前只需完成 opencli 预检。
