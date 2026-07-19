# vince-skill

Vince 的个人 Codex Skill 仓库，用于集中维护、复用和分发可独立安装的技能。

## 目录结构

```text
vince-skill/
├── README.md
└── <category>/
    └── <skill-name>/
        ├── SKILL.md           # 技能入口，必需
        ├── agents/
        │   └── openai.yaml    # 展示名称、简介和默认提示词，推荐
        ├── scripts/           # 可重复执行的脚本，按需创建
        ├── references/        # 按需载入的参考资料，按需创建
        └── assets/            # 模板、图标等输出资源，按需创建
```

仓库按领域分类，每个 `<skill-name>` 目录都是一个可独立复制和安装的 Skill。目录名与 `SKILL.md` 中的 `name` 应保持一致，并使用小写字母、数字和连字符。

## 已提供的 AI 趋势 Skill

AI 趋势渠道已拆分为三个可独立安装、独立预检和独立运行的 Skill：

| Skill | 数据渠道 | 运行依赖 |
| --- | --- | --- |
| [`collect-x-ai-trends`](ai/collect-x-ai-trends) | X 重点账号与主题查询 | `opencli` |
| [`collect-reddit-ai-trends`](ai/collect-reddit-ai-trends) | Reddit AI 社区 | `opencli` |
| [`collect-github-ai-trends`](ai/collect-github-ai-trends) | GitHub 仓库搜索趋势代理 | `gh` |

三个目录都自带采集和中文编辑终稿校验，终稿正文直接返回，不依赖 Obsidian、其他发布系统或 [`collect-ai-trends`](ai/collect-ai-trends) 的文件。原聚合 Skill 继续保留，用于确实需要一次运行合并多渠道信号的场景。

## 创建 Skill

1. 复制 [`category-name/example-skill`](category-name/example-skill) 作为起点。
2. 修改目录名、`SKILL.md` 和 `agents/openai.yaml`。
3. 只保留实际需要的 `scripts/`、`references/` 或 `assets/`。
4. 运行校验并检查版本差异。

`SKILL.md` 的 YAML 前置元数据只保留 `name` 和 `description`：

```yaml
---
name: example-skill
description: Describe what the skill does and the requests that should trigger it.
---
```

编写时遵循以下原则：

- 在 `description` 中同时说明技能能力和触发场景。
- 正文使用明确的祈使句，优先描述可执行工作流、判断条件和验证方式。
- 保持 `SKILL.md` 简洁；详细资料放入 `references/`，重复且要求稳定的操作放入 `scripts/`。
- 避免在 `SKILL.md` 与参考文件中重复同一份内容。
- 不提交密钥、令牌、个人配置或生成产物。

## 校验

使用 Codex 自带的 `skill-creator` 校验脚本检查单个 Skill：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" \
  category-name/example-skill
```

提交前还应确认：

- `SKILL.md` 的 `name` 与技能目录名一致。
- `description` 能覆盖用户真实会说出的触发请求。
- `agents/openai.yaml` 中的默认提示词显式包含 `$skill-name`。
- 新增脚本已经实际运行，并验证了成功路径和关键失败路径。

## 安装

将需要的技能目录复制到 Codex Skill 目录：

```bash
cp -R category-name/example-skill "${CODEX_HOME:-$HOME/.codex}/skills/"
```

安装后可在提示词中使用 `$example-skill` 显式调用。更新 Skill 后，建议重新启动相关 Codex 会话以加载最新内容。

## 示例

[`category-name/example-skill`](category-name/example-skill) 展示了最小可用结构。新增正式技能时，请将 `category-name` 替换为清晰的领域名称，例如 `github`、`documents` 或 `frontend`。

## License

MIT
