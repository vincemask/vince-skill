# Reddit 渠道与排序

- 使用 `opencli reddit subreddit` 采集配置中 AI 社区的热门帖子。
- 默认社区包含 `ChatGPTCoding` 与 `AI_Agents`，用于补强 coding agent 和 AI coding 主题覆盖。
- 保留 subreddit、发布时间、得分、评论数和原始帖子链接。
- 在 Reddit 渠道内部按互动百分位与时间衰减排序，并用标题词法相似度去重聚类。
- 定期检查社区活跃度与主题相关性，避免把长期低信号社区固化为权威来源。

该 skill 不使用 X 或 GitHub 数据，不得把单渠道结果描述为跨来源印证。
