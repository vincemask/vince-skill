# X 渠道与排序

- 使用 `opencli twitter search` 分批采集配置中的重点账号，并独立运行 `topic_queries`。
- 默认主题查询显式覆盖 `coding agent`、`agentic coding`、`AI coding` 与近义词。
- 保留作者、发布时间、互动指标和原始 X 链接；回复与转发默认排除。
- 在 X 渠道内部按互动百分位与时间衰减排序，并用标题词法相似度去重聚类。
- 账号名单只是可维护的起点；定期替换已停用、改名或低信号账号。

该 skill 不使用 Reddit 或 GitHub 数据，不得把单渠道结果描述为跨来源印证。
