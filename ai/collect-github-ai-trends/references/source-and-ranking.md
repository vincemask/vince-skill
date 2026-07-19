# GitHub 渠道与排序

- 使用 `gh api search/repositories` 采集新建或近期活跃的 AI 仓库。
- 默认查询覆盖 LLM、生成式 AI、机器学习、coding agents、AI coding agent 与 AI coding assistant topic。
- GitHub 没有公开的官方 Trending API，因此输出必须称为“仓库搜索趋势代理”，不能冒充 GitHub Trending 官方榜单。
- 保留仓库、简介、创建或推送时间、stars、forks、语言和原始仓库链接。
- 在 GitHub 渠道内部按 stars/forks 互动百分位与时间衰减排序，再用项目标题词法相似度去重聚类。

该 skill 不使用 X 或 Reddit 数据，不得把单渠道结果描述为跨来源印证。
