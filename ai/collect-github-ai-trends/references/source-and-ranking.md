# GitHub 渠道与排序

- 使用 `gh api search/repositories` 建立两个候选池：半年内仍有 push 的成熟热门项目，以及一年内获得明显采用的上升项目。不要只抓最近几天新建的仓库。
- 默认查询覆盖 LLM、生成式 AI、机器学习、AI agents、coding agents、AI coding agent 与 AI coding assistant topic，并设置最低 stars 门槛，避免“刚创建”被误判为“正在变热”。
- GitHub 没有公开的官方 Trending API，因此输出必须称为“仓库搜索趋势代理”，不能冒充 GitHub Trending 官方榜单。
- 保留仓库、简介、创建时间、最近 push 时间、stars、forks、语言和原始仓库链接。
- 排序默认由三部分组成：40% 总热度（stars/forks）、50% 增长动量代理（按项目年龄折算的 stars/forks 速度）、10% 当下活跃度（最近 push 的时间衰减），再用项目标题词法相似度去重聚类。
- 创建日期的新鲜度不能单独加分。老项目只要总热度高、增长代理强且仍在活跃，就应排在低采用的新项目之前。
- 增长动量是基于当前仓库元数据的代理，不是 GitHub 官方近期增星数；文案可说“上涨趋势代理较强”，不得声称“近期新增了 N 个 stars”。

该 skill 不使用 X 或 Reddit 数据，不得把单渠道结果描述为跨来源印证。
