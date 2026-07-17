# Sources and ranking

## Source strategy

- Reddit: collect hot posts from the configured AI communities with `opencli reddit subreddit`. Keep subreddit, score, comments, publication time, and the original post URL.
- X: group the configured accounts into small batches and query their recent posts with `opencli twitter search`. Small batches reduce browser work while preserving per-batch cache fallback.
- GitHub: query `search/repositories` through `gh api`. GitHub has no public Trending API, so label this section as a proxy built from newly created repositories by stars and established repositories with recent pushes.

The default X watchlist is a starter set, not a permanent authority list. Review handles periodically and replace inactive, renamed, or low-signal accounts. Prefer official organization accounts and researchers who regularly link to primary work.

## Ranking

For each source, calculate a raw engagement value:

- Reddit: `log1p(score) + 0.7 * log1p(comments)`
- X: `log1p(likes) + 1.5 * log1p(retweets) + 1.2 * log1p(replies) + 0.2 * log1p(views)`
- GitHub: `log1p(stars) + 1.5 * log1p(forks)`

Convert raw engagement to a percentile within that source, combine it with an exponential recency score, and apply the configured source weight. Add a bounded bonus when a topic cluster contains independent signals from more than one source.

Keep at most the top 10 URL-backed clusters for editorial work. If fewer than 10 clusters have usable evidence, preserve the smaller count instead of padding the list with weak signals.

Do not compare raw likes, Reddit scores, and GitHub stars directly. Their scales and collection semantics differ.

## Deduplication and clustering

1. Deduplicate exact canonical URLs first.
2. Deduplicate repeated source IDs next.
3. Cluster normalized titles only when token similarity meets the configured threshold.
4. Keep every original item and URL inside the topic cluster for auditability.

Clustering is lexical and deterministic. Treat it as editorial assistance, not proof that two sources describe the same event.

## Authentication and privacy

- Use the user's existing opencli browser session for Reddit and X.
- Use `gh auth login` for GitHub authentication.
- Never store cookies, tokens, command traces, or full browser state in the report directory.
- Keep configuration free of secrets.
