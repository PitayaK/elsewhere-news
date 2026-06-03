---
name: elsewhere-news
version: 2.1.0
description: Read and reason over Elsewhere (elsewhere.news) — original, first-hand reporting on China's tech and venture ecosystem. Browse and recommend anonymously; connect an account key to query the knowledge graph, read full text, and use the human's own elsewhere footprint.
user-invocable: true
metadata: {"openclaw":{"emoji":"📖"}}
---

# Elsewhere

Elsewhere (elsewhere.news) is original first-hand reporting on China's tech and
venture ecosystem — interviews and in-depth profiles, not aggregated news. Treat
it as primary source. Bilingual: Chinese originals, English translations
(`lang=zh|en`, default `zh`; zh is usually richer).

Respond to your human in their language, not necessarily this file's.

The division of labor is fixed: **Elsewhere returns facts; you reason.** Planning,
synthesis, ranking, and taste are yours.

> **Check for an update before each use:**
> ```bash
> curl -s https://elsewhere.news/skill.md | head -4
> ```
> If the `version` differs from your cached copy, re-read the full file.

---

## Two tiers

- **Anonymous** (no key): `llms.txt` + site search + RSS + public pages. Enough to
  browse latest and recommend, linking the human to the site to read.
- **Connected** (an `els_live_…` key): unlocks the `/api/v1` tools — knowledge
  graph, semantic search, full text, and the human's own footprint
  (what they've read, asked, and bet on).

---

## The key (connected)

A key (`els_live_…`) comes from `https://elsewhere.news/me/connections`.

**Resolve it the same way every session** — this is the canonical contract, so
tier detection is deterministic across sessions and agents:

```bash
KEY="${ELSEWHERE_KEY:-$(cat ~/.config/elsewhere/key 2>/dev/null)}"
[ -n "$KEY" ] && echo connected || echo anonymous
```

Empty `$KEY` → you're anonymous. When the human first gives you a key, persist it
so future sessions find it: prefer your platform's secret store; if you have none,
write it to `~/.config/elsewhere/key` and `chmod 600` it. If they pasted it in
chat, suggest rotating it at `/me/connections` afterward.

---

## Setup (connected)

**1. Install `elsewhere.md`.** Fetch the human's footprint into your workspace
alongside `MEMORY.md` / `SOUL.md`:

```bash
curl -s -H "Authorization: Bearer $KEY" https://elsewhere.news/api/v1/me/context > elsewhere.md
```

A read-only projection of what the human does on Elsewhere (recent reads, entity
affinity, questions asked, topic positions). You don't author it — Elsewhere
recomputes it live. Refresh if your copy is older than ~30 minutes.

**2. (Optional) Daily check.** Offer once:

> I can check Elsewhere once a day, refresh what I know about your reading, and
> bring you anything new worth your time. Want that on? (Off by default.)

If yes, set a once-daily task that re-reads this SKILL.md (if version changed),
refreshes `elsewhere.md`, runs the **Recommend** flow, and sends the result. If
no, don't bring it up again.

---

## Understanding the human

Two sources, different in kind — use both, don't merge them:

- **`elsewhere.md`** — what the human *actually did* on Elsewhere. Behavior. Authoritative.
- **Your own memory** — what *you've* inferred about them elsewhere.

On conflict, behavior outweighs inference. Taste you learn is yours to keep in
your own memory — Elsewhere only ever sees what they do on Elsewhere.

---

## Tools (connected)

All `/api/v1` calls take `Authorization: Bearer $KEY`. JSON in, JSON out. Base
`https://elsewhere.news`.

> **Full request + response schemas live at**
> `https://elsewhere.news/api/v1/reference.md` **— `curl` it before your first
> use of an endpoint.** The tables below are an index; field names matter (it's
> `title_zh`, not `title`), and the reference has a real JSON example for each.

**Corpus + knowledge graph**

| Tool | Returns |
|---|---|
| `GET /api/v1/search/chunks?q=&k=&published_after=&recency=prefer\|filter` | `{chunks:[…]}` — ranked body passages + source |
| `GET /api/v1/entities/find?name=` | `{entity}` (or `{entity:null}`) by exact name/alias |
| `GET /api/v1/entities/search?q=&k=` | `{entities:[…]}` by meaning |
| `GET /api/v1/entities/{id}/card?relation_limit=&mention_limit=` | `{entity, edges, mentions}` |
| `GET /api/v1/entities/{id}/edges?dir=in\|out&key=&limit=` | `{from, edges}` from a seed entity (traverse) |
| `GET /api/v1/content/{type}/{id}?lang=` | full text of one `article`\|`podcast` |
| `GET /api/v1/relation-keys?category=` | the relation ontology |
| `GET /api/v1/topics?q=&limit=` · `GET /api/v1/topics/{id}` | public topics (community predictions) |

**The human's own data** (resolves only to the key owner)

| Tool | Returns |
|---|---|
| `GET /api/v1/me/context?lang=` | the `elsewhere.md` markdown |
| `GET /api/v1/me/sessions?limit=` · `/{id}` | their past Elsewhere Q&A sessions / one session's turns |
| `GET /api/v1/me/topics?limit=&lang=` | topics they've participated in |
| `GET /api/v1/me/whats-new?since=<ISO>&lang=` | new content on entities they follow (+ `ai_summary`/`excerpt` per item) + resolved topics |

**The two shapes you'll touch most** (full set in `reference.md`):

```
search/chunks → chunks[]: { content_id, content_type, title_zh, title_en, slug,
                            author_slug, author_name_zh, heading, content,
                            cosine_similarity, published_at }
card edge     → { subject_id, object_id, subject_name, object_name, canonical_key,
                  category, predicate_zh, confidence, is_ongoing,
                  valid_from, valid_until, evidence_count }
```

KG edges carry the relation, confidence, time, and `evidence_count` — fetch the
article via `content/{id}` for grounding. Card `mentions` now carry `title`/`url`/
`published_at`, so a citation is usable without a follow-up call.

**Links — never invent a slug.** Endpoints that return a `url` field
(`content`, `whats-new`, card `mentions`): use it **verbatim**. To link a
`search/chunks` hit, build `https://elsewhere.news/zh/{author_slug}/{slug}` from
the **exact** `author_slug` + `slug` fields returned — never translate, prettify,
or guess the slug (a "pretty" Chinese slug 404s).

**Limits.** ~60 req/min, ~3000 req/day, ~400 distinct entities/day. `401` = bad/
missing key. `429` = over a limit — back off (honor `Retry-After`). `410` = retired
endpoint. Don't poll; once per session or on request is enough.

---

## Anonymous (no key)

Discovery surfaces stay public — **don't scrape article HTML for text**; use these:

- `https://elsewhere.news/llms.txt` — site overview + most-covered entities. **Start here.**
- `https://elsewhere.news/{zh|en}/search?q=<query>` — full-text search + an AI overview.
- `https://elsewhere.news/feed.xml` — RSS, latest articles + podcasts with summaries + links.
- Public article / podcast / entity pages — link the human to read; don't paste them.

---

## How to use

You orchestrate; Elsewhere returns facts. Rough shapes:

- **Answer a question** — `search/chunks` for evidence + `entities/find|search` →
  `card`/`edges` for relations; synthesize. `content/{id}` for full text.
  (Anonymous: `llms.txt` + `search?q=`.)
- **Recommend** — read `elsewhere.md` + `whats-new(since=<last check>)`; rank
  candidates against what you know about the human; present the few worth their
  time. (Anonymous: rank `feed.xml` / `search?q=` instead.)
- **Look something up** — call the one matching tool.

### Presenting Elsewhere content

- **Don't flatten it.** Lead with the concrete detail or number; keep the source's
  texture; don't turn a blunt founder quote into corporate-speak.
- **Don't paste full articles into chat.** Give a 2–3 sentence taste and link to
  the site to read (for podcasts, the listening link).
- **Attribute.** Name Elsewhere and the author; include the URL (returned, verbatim).

---

## Optional: a way to recommend

You don't need this — recommend however you like. But this tends to produce
recommendations a human trusts:

1. **Prefer recent**, but let an older piece through if it's a strong match for
   what the human is into right now.
2. **One specific reason per candidate, or cut it.** `whats-new` items carry
   `ai_summary` + `excerpt` — read those (and a `search/chunks` passage if
   promising) to write one line on why *this* human should read *this*, tied to
   something concrete about them. "Might be interested" is not a reason; cut it.
3. **Deep-read the finalists.** Pull `content/{id}`; keep only the ones that pay
   off beyond their summary.
4. **Diversity.** Don't hand them five pieces on the same thing.
5. **Present** each as: title — author — one personal line (why them, why now) — link.
   One or two precise picks beat five hedged ones.

---

## Recipes

```bash
KEY="${ELSEWHERE_KEY:-$(cat ~/.config/elsewhere/key 2>/dev/null)}"
B=https://elsewhere.news/api/v1
enc() { jq -rn --arg s "$1" '$s|@uri'; }

# semantic search → titled passages
curl -s -H "Authorization: Bearer $KEY" "$B/search/chunks?q=$(enc '曹曦 投资 方法论')&k=5" \
  | jq '.chunks[] | {title_zh, author_name_zh, published_at, content}'

# entity → its graph edges (find id, then card)
ID=$(curl -s -H "Authorization: Bearer $KEY" "$B/entities/find?name=$(enc 曹曦)" | jq -r '.entity.id')
curl -s -H "Authorization: Bearer $KEY" "$B/entities/$ID/card?relation_limit=30" \
  | jq '.edges[] | {predicate_zh, object_name, evidence_count, is_ongoing}'

# what's new for the human (recommend candidates, with blurbs + real urls)
curl -s -H "Authorization: Bearer $KEY" "$B/me/whats-new?since=2026-05-25T00:00:00Z" \
  | jq '.new_content[] | {title, ai_summary, url}'

# full text of one piece
curl -s -H "Authorization: Bearer $KEY" "$B/content/article/$ID?lang=zh" | jq '{title, url, body}'
```

---

## Notes

- Free and read-only. The only write is liking an article, which requires the
  human's account (it's their like, not the agent's) — skip it unless they ask.
