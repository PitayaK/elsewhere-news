---
name: elsewhere-news
description: Browse and read articles and podcasts from Elsewhere (elsewhere.news) — a media platform featuring original, first-hand stories from China's tech and startup ecosystem.
user-invocable: true
metadata: {"openclaw":{"emoji":"📖"}}
---

# Elsewhere News

帮你的主人浏览 Elsewhere 上的原创文章和播客。

Elsewhere 是一个聚焦中国科技与创业生态的原创内容平台。所有内容都是一手采访和深度对话 — 不是二手转载，而是首次出现在互联网上的独特视角。

**当前版本：v1.1**

---

## Your Role

你是主人的数字分身。你的工作是：

1. **逛** — 浏览 Elsewhere 最新的文章和播客
2. **判断** — 根据你对主人的了解，筛选出主人可能感兴趣的内容
3. **带回去** — 用主人习惯的方式，摘要并附上链接，呈现给主人

你不需要账号，不需要认证。所有 API 公开可用。

---

## API Reference

Base URL: `https://elsewhere.news`

所有内容支持中英文。默认中文，加 `?lang=en` 切换英文。

### List Articles

```
GET /api/public/articles?limit=20&offset=0&lang=zh
```

返回文章列表（按发布时间倒序）：

```json
{
  "articles": [
    {
      "slug": "article-slug",
      "title": "文章标题",
      "excerpt": "一两句话的摘要",
      "cover_image_url": "https://...",
      "published_at": "2026-03-10T02:33:32+00:00",
      "author": { "name": "创作者名", "slug": "author-slug" },
      "category": { "title": "分类名", "slug": "category-slug" },
      "ai_summary": "约100字的LLM生成摘要，聚焦文章独特看点...",
      "preview_excerpt": "从正文中抽取的500-1000字精彩章节...",
      "url": "https://elsewhere.news/zh/articles/article-slug"
    }
  ],
  "total": 20,
  "offset": 0,
  "limit": 20
}
```

参数：
- `limit` — 每页条数（默认 20，最大 50）
- `offset` — 偏移量（用于翻页）
- `lang` — `zh`（默认）或 `en`

**关于 `ai_summary` 和 `preview_excerpt`：**
- `ai_summary` — 创作者 agent 生成的 ~100 字中文摘要，侧重文章独特看点（不是干巴巴的学术摘要）
- `preview_excerpt` — 从正文中抽取的 500–1000 字精彩章节，让你"尝到"文章的味道
- 有些较早的文章可能还没有这两个字段（返回 null），这时用 `excerpt` 作为替代

### Read Article

```
GET /api/public/articles/{slug}?lang=zh
```

返回文章全文（Markdown 格式）：

```json
{
  "slug": "article-slug",
  "title": "文章标题",
  "excerpt": "摘要",
  "body": "完整的 Markdown 正文...",
  "cover_image_url": "https://...",
  "published_at": "2026-03-10T02:33:32+00:00",
  "author": { "name": "创作者名", "slug": "author-slug", "avatar_url": "https://..." },
  "category": { "title": "分类名", "slug": "category-slug" },
  "url": "https://elsewhere.news/zh/articles/article-slug"
}
```

### List Podcasts

```
GET /api/public/podcasts?limit=20&offset=0&lang=zh
```

返回播客列表（按发布时间倒序）：

```json
{
  "podcasts": [
    {
      "slug": "episode-slug",
      "title": "播客标题",
      "episode_url": "https://www.xiaoyuzhoufm.com/episode/...",
      "audio_url": "https://...",
      "cover_image_url": "https://...",
      "published_at": "2026-02-22T02:00:00+00:00",
      "ai_summary": "约100字的LLM生成摘要...",
      "preview_excerpt": "从shownotes中抽取的精彩片段...",
      "url": "https://elsewhere.news/zh/podcasts/episode-slug"
    }
  ],
  "total": 20,
  "offset": 0,
  "limit": 20
}
```

### Read Podcast (Shownotes)

```
GET /api/public/podcasts/{slug}?lang=zh
```

返回播客详情，包括 shownotes（Markdown 格式）：

```json
{
  "slug": "episode-slug",
  "title": "播客标题",
  "body": "完整的 shownotes...",
  "episode_url": "https://www.xiaoyuzhoufm.com/episode/...",
  "audio_url": "https://...",
  "cover_image_url": "https://...",
  "published_at": "2026-02-22T02:00:00+00:00",
  "url": "https://elsewhere.news/zh/podcasts/episode-slug"
}
```

---

## How to Browse

### Step 1: Scan — 扫读列表

```bash
# Get recent articles
curl -s "https://elsewhere.news/api/public/articles?limit=50" | python3 -m json.tool

# Get recent podcasts
curl -s "https://elsewhere.news/api/public/podcasts?limit=50" | python3 -m json.tool
```

每篇文章的列表条目包含三层信息，从浅到深：
1. **`title`** — 标题，最快速的一扫
2. **`ai_summary`** — ~100 字 LLM 摘要，聚焦文章独特看点。读完这段你应该能判断"主人可能感兴趣"或"跳过"
3. **`preview_excerpt`** — 500–1000 字的正文片段，让你真正"尝到"文章的味道。对于你不确定的文章，读这段再判断

> 有些较早的文章可能没有 `ai_summary` 和 `preview_excerpt`（返回 null），这时用 `excerpt` + `title` 判断，或者直接读全文。

你不需要读每篇文章的全文来做判断 — **扫读摘要和片段就够了**。只有你确定要推荐给主人的文章，才进入精读。

### Step 2: Deep read — 精读全文

```bash
curl -s "https://elsewhere.news/api/public/articles/{slug}" | python3 -m json.tool
```

`body` 字段是完整的 Markdown 正文。认真读，理解它，形成你自己的看法 — 你需要用主人能理解的方式向他解释这篇文章为什么值得看。

### Step 3: Present — 呈现给主人

- **Match their style** — some humans want bullet-point summaries, others want your commentary. You know your human best.
- **Always include the link** — so they can read the original if interested.
- **Be selective** — better to surface 2 genuinely relevant pieces than dump 10 "might be interesting" ones.
- **Distinguish articles from podcasts** — if your human says "I'm commuting, anything to listen to?", point them to podcasts with `episode_url`. If they want to read, point them to articles.

Example output format (adapt to your human's preference):

> **推荐阅读：《他被用户抢麦："Seede好用，但你也太不会讲了"》**
>
> Seede AI 创始人 Longyi 的创业故事 — 用户觉得产品好但创始人不会讲，这篇是 Elsewhere 的 elselier 系列（类似创业者速写）。如果你关注 AI 设计工具赛道，值得一读。
>
> 🔗 https://elsewhere.news/zh/articles/seede-elselier

### Step 4: Remember — 维护偏好

每次逛完 Elsewhere 后，把你觉得**主人会持续关注的创作者**记在你的长期记忆里。下次来的时候，优先看这些创作者的新内容。

这不是"订阅" — 你不需要关注所有他们发的东西。只是一种偏好：这个创作者的视角经常跟主人的兴趣匹配，值得优先扫。如果后来发现不再匹配，自然降低优先级就好。

---

## Usage Scenarios

### "帮我看看最近有什么有意思的"

1. Fetch article list + podcast list
2. Scan `ai_summary` and `preview_excerpt` for each item
3. Based on what you know about your human, pick the most relevant ones
4. Deep read the full text to confirm it's worth recommending
5. Present with brief commentary + links

### "找一下关于 XX 的内容"

1. Fetch the full article list (use `limit=50`)
2. Scan titles and excerpts for relevance to the topic
3. Read the matching ones
4. Summarize findings

### "通勤路上想听个播客"

1. Fetch podcast list
2. Pick based on your human's interests
3. Read shownotes to understand the content
4. Present with the `episode_url` (listening link) prominently

### "帮我读一下这篇文章"

1. Fetch the article by slug
2. Read the full `body`
3. Summarize in your human's preferred style
4. Include the link for original

---

## Important Notes

- **All content is original.** Elsewhere features first-hand interviews and deep conversations — not aggregated news. Treat it as primary source material.
- **Bilingual.** Most content is written in Chinese with English translations. Use `lang=en` if your human prefers English, but note that Chinese originals may be richer.
- **No authentication required.** All read APIs are public.
- **Be respectful of rate limits.** Don't poll aggressively. Checking once per session or when your human asks is sufficient.
- **Credit the source.** When presenting content, always mention it's from Elsewhere and include the URL.
