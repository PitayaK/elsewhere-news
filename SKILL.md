---
name: elsewhere-news
version: 2.5.1
description: Read and reason over Elsewhere (elsewhere.news): first-hand reporting on China's tech and venture ecosystem. Browse public content anonymously, or connect an els_live key for search, graph, full text, personas, and personal context.
user-invocable: true
---

# Elsewhere

Elsewhere (elsewhere.news) is first-hand reporting on China's tech and venture ecosystem: interviews, profiles, and analysis, not aggregated news. Treat it as a primary-source corpus. Elsewhere returns facts; you reason, rank, synthesize, and explain.

Reply in the user's language. Prefer `lang=zh` unless the user asks otherwise; Chinese originals are often richer.

Before use, check for a newer canonical skill:

```bash
curl -s https://elsewhere.news/skill.md | head -4
```

If the online `version` differs, read the full online file.

## Modes

Anonymous mode: no key. Use public discovery only. Good for browsing latest items and linking users to Elsewhere. Do not scrape article HTML for full text.

Connected mode: an `els_live_...` key unlocks `/api/v1`: semantic search, knowledge graph, full text, personas, the user's own context, sessions, topics, and what's-new feed.

## Key

Resolve connection status the same way every session:

```bash
KEY="${ELSEWHERE_KEY:-$(cat ~/.config/elsewhere/key 2>/dev/null)}"
[ -n "$KEY" ] && echo connected || echo anonymous
```

Keys come from `https://elsewhere.news/me/connections`.

If the user gives a key, immediately persist it to the canonical path:

```bash
mkdir -p ~/.config/elsewhere
printf '%s' 'els_live_...' > ~/.config/elsewhere/key
chmod 600 ~/.config/elsewhere/key
```

Do not store the key only in chat, a project `.env`, or an unread secret store. If the key was pasted into chat, suggest rotating it afterward.

## Connected Setup

Before fetching from Elsewhere, check any available local memory about the user (`MEMORY.md`, `USER.md`, `SOUL.md`, or equivalent). Then refresh the user's Elsewhere context:

```bash
curl -s -H "Authorization: Bearer $KEY" \
  "https://elsewhere.news/api/v1/me/context?lang=zh" > elsewhere.md
```

Refresh `elsewhere.md` if it is older than about 30 minutes. Treat it as behavior: what the user actually did on Elsewhere. Treat your own memory as inference. If they conflict, behavior wins.

## API

Base: `https://elsewhere.news/api/v1`

Connected requests require:

```text
Authorization: Bearer $KEY
```

Before first use of an endpoint, read schemas and examples:

```bash
curl -s https://elsewhere.news/api/v1/reference.md
```

Use exact field names from the reference.

Common endpoints:

```text
GET /search/chunks?q=&k=&author=&published_after=&recency=prefer|filter
GET /entities/find?name=
GET /entities/search?q=&k=
GET /entities/{id}/card?relation_limit=&mention_limit=
GET /entities/{id}/edges?dir=in|out&key=&limit=
GET /content/{type}/{id}?lang=
GET /relation-keys?category=
GET /topics?q=&limit=
GET /topics/{id}
GET /personas?q=&lang=
GET /personas/{slug}
GET /me/context?lang=
GET /me/sessions?limit=
GET /me/sessions/{id}
GET /me/topics?limit=&lang=
GET /me/whats-new?since=<ISO>&lang=
```

Frequent shapes:

```text
search/chunks -> chunks[]:
{ content_id, content_type, title_zh, title_en, slug, author_slug,
  author_name_zh, heading, content, cosine_similarity, published_at }

card edge:
{ subject_id, object_id, subject_name, object_name, canonical_key,
  category, predicate_zh, confidence, is_ongoing,
  valid_from, valid_until, evidence_count }
```

Links: if an endpoint returns `url`, use it verbatim. For `search/chunks`, build:

```text
https://elsewhere.news/zh/{author_slug}/{slug}
```

Never invent, translate, prettify, or guess slugs.

Limits: roughly 60 requests/min, 3000 requests/day, 400 distinct entities/day. `401` means missing/bad key. `429` means back off and honor `Retry-After`. `410` means retired endpoint.

## Anonymous Sources

No key means public sources only:

```text
https://elsewhere.news/llms.txt
https://elsewhere.news/zh/search?q=<query>
https://elsewhere.news/en/search?q=<query>
https://elsewhere.news/feed.xml
```

Do not scrape article HTML. Summarize public discovery surfaces and link users to read on Elsewhere.

## Workflows

Answer a question: use `search/chunks` for evidence; use `entities/find` or `entities/search` for entity resolution; use `card` or `edges` for relationships; use `content/{type}/{id}` when full context is needed.

Recommend reading: use `elsewhere.md` plus `me/whats-new`. Rank candidates against the user's actual behavior. Present title, author, link, and one concrete reason. Prefer one or two strong picks over a long list.

Trace relationships: find the entity, fetch `card` or `edges`, then ground important claims in mentions or content. Edges have confidence, time bounds, and evidence counts.

Read full text: call `content/article/{id}?lang=zh` or `content/podcast/{id}?lang=zh`. Do not paste whole articles; give a brief taste, key details, and the link.

Use personas: connected mode only. First call `GET /personas?lang=zh` and follow the returned shell. Then call `GET /personas/{slug}` for the creator kernel. Ground every factual claim with author-scoped `search/chunks?author={slug}`. A persona is an AI distillation from public work, not the real creator speaking. Never invent private opinions, unpublished plans, or unsupported claims.

## Coverage Gaps

Elsewhere is bounded. Few results, low similarity, off-topic snippets, null entities, or low mention counts usually mean a coverage gap.

On gaps, do not guess. Say what Elsewhere covers and does not cover. Ask whether to supplement from the user's materials or web search. Keep Elsewhere facts separate from external information.

## Presentation Rules

Preserve the texture of reporting: concrete details, numbers, people, quotes-in-context, and uncertainty. Do not flatten blunt founder or investor judgments into generic business prose.

Attribute substantive claims to Elsewhere and the author. Include links.

Never paste full articles. Never invent slugs. Never silently mix outside web facts into Elsewhere facts. Do not poll; fetch once per session or per user request.

## Examples

```bash
KEY="${ELSEWHERE_KEY:-$(cat ~/.config/elsewhere/key 2>/dev/null)}"
B=https://elsewhere.news/api/v1
enc() { jq -rn --arg s "$1" '$s|@uri'; }

curl -s -H "Authorization: Bearer $KEY" \
  "$B/search/chunks?q=$(enc '曹曦 投资 方法论')&k=5" \
  | jq '.chunks[] | {title_zh, author_name_zh, published_at, content}'

ID=$(curl -s -H "Authorization: Bearer $KEY" \
  "$B/entities/find?name=$(enc 曹曦)" | jq -r '.entity.id')
curl -s -H "Authorization: Bearer $KEY" \
  "$B/entities/$ID/card?relation_limit=30" \
  | jq '.edges[] | {predicate_zh, object_name, evidence_count, is_ongoing}'

curl -s -H "Authorization: Bearer $KEY" \
  "$B/me/whats-new?since=2026-05-25T00:00:00Z&lang=zh" \
  | jq '.new_content[] | {title, ai_summary, url}'
```
