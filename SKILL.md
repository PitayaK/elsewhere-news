---
name: elsewhere-news
version: 2.0.0
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

> **Check for an update before each use:**
> ```bash
> curl -s https://elsewhere.news/skill.md | head -4
> ```
> If the `version` differs from your cached copy, re-read the full file
> (`curl -s https://elsewhere.news/skill.md`).

---

## Two tiers

- **Anonymous** (no account): RSS + `llms.txt` + public pages. Enough to browse
  latest and recommend, linking the human to the site to read.
- **Connected** (an `els_live_…` key): unlocks the `/api/v1` tools — knowledge
  graph, semantic search, full text, and the human's own Elsewhere footprint
  (what they've read, asked, and bet on) + personalization.

Detect the tier: if the human has given you an Elsewhere key (in your config /
secrets), you're connected; otherwise anonymous. A key comes from
`https://elsewhere.news/me/connections`.

The division of labor is fixed: **Elsewhere returns facts; you reason.** Planning,
synthesis, ranking, and taste are yours. Elsewhere never does them for you.

---

## Setup (connected)

**1. Install `elsewhere.md`.** Fetch the human's footprint and write it to your
workspace alongside `MEMORY.md` / `SOUL.md`:

```bash
curl -s -H "Authorization: Bearer $ELSEWHERE_KEY" https://elsewhere.news/api/v1/me/context > elsewhere.md
```

It's a read-only projection of what the human does on Elsewhere (recent reads,
entity affinity, questions asked, topic positions). Refresh it at the start of a
session if your copy is older than ~30 minutes. You don't author it — Elsewhere
recomputes it live each fetch.

**2. (Optional) Daily check.** Offer the human a daily cron:

> I can check Elsewhere once a day, refresh what I know about your reading, and
> bring you anything new worth your time. Want that on? (Off by default.)

If yes, set a once-daily task in your platform's scheduler that: re-reads this
SKILL.md if the version changed, refreshes `elsewhere.md`, then runs the
**Recommend** flow and sends the result. If no, don't bring it up again.

(No `TASTE.md`. Preferences live in your own memory — see below.)

---

## Understanding the human

Two sources, different in kind — use both, don't merge them:

- **`elsewhere.md`** — what the human *actually did* on Elsewhere. Behavior. Authoritative.
- **Your own memory** (`MEMORY.md` / `SOUL.md` / native memory) — what *you've
  observed* about them across everything else. Inference.

On conflict, behavior outweighs inference.

---

## Auth & tools (connected)

All `/api/v1` calls take `Authorization: Bearer els_live_…`. JSON in, JSON out.
Base: `https://elsewhere.news`.

**Corpus + knowledge graph**

| Tool | Returns |
|---|---|
| `GET /api/v1/search/chunks?q=&k=&published_after=&recency=prefer\|filter` | ranked body passages + source + `published_at` |
| `GET /api/v1/entities/find?name=` | one entity by name (exact/alias) |
| `GET /api/v1/entities/search?q=&k=` | entities by meaning |
| `GET /api/v1/entities/{id}/card?relation_limit=&mention_limit=` | entity + its edges + mention citations |
| `GET /api/v1/entities/{id}/edges?dir=in\|out&key=&limit=` | relations from a seed entity (traverse) |
| `GET /api/v1/content/{type}/{id}?lang=` | full text of one article/podcast (`type`=`article`\|`podcast`) |
| `GET /api/v1/relation-keys?category=` | the relation ontology |
| `GET /api/v1/topics?q=&limit=` | public topics (community predictions) |
| `GET /api/v1/topics/{id}` | one public topic + comments |

**The human's own data** (resolves only to the key owner)

| Tool | Returns |
|---|---|
| `GET /api/v1/me/context?lang=` | the `elsewhere.md` markdown |
| `GET /api/v1/me/sessions?limit=` | their past Elsewhere Q&A sessions |
| `GET /api/v1/me/sessions/{id}` | one session's full turns |
| `GET /api/v1/me/topics?limit=&lang=` | topics they've participated in |
| `GET /api/v1/me/whats-new?since=<ISO>&lang=` | since `since`: new content on entities they follow + their topics that resolved |

KG edges carry the relation, confidence, time, and the source id — fetch the
article via `content/{id}` for grounding. Entities are referenced by `id`; you
get ids from `find`/`search`/`card`/`edges` and `content_id` from `search/chunks`.

**Limits.** ~60 req/min, ~3000 req/day, ~400 distinct entities/day. `401` = bad
or missing key. `429` = over a limit — back off (honor `Retry-After`). `410` =
a retired endpoint. Don't poll aggressively; once per session or on request is enough.

**Anonymous tools** (no key): `https://elsewhere.news/feed.xml` (RSS, latest
articles + podcasts with summaries + links), `https://elsewhere.news/llms.txt`
(site overview + most-covered entities), and the public article/podcast/entity pages.

---

## How to use

You orchestrate; Elsewhere returns facts. Rough shapes:

- **Answer a question** — `search/chunks` for evidence + `entities/find|search`
  → `entities/{id}/card`/`edges` for relations; synthesize from what comes back.
  Pull full text with `content/{id}` when you need it. (Connected. Anonymous:
  search the public pages + RSS.)
- **Recommend** — read `elsewhere.md` + `whats-new(since=<last check>)`; rank the
  candidates against what you know about the human; present the few worth their
  time. (Anonymous: rank the RSS latest instead.)
- **Look something up** — call the one matching tool (an entity, a session, a
  topic, what's new).

### Presenting Elsewhere content

- **Don't flatten it.** Lead with the concrete detail or number; keep the source's
  texture; don't turn a blunt founder quote into corporate-speak.
- **Don't paste full articles into chat.** Give a 2–3 sentence taste and link to
  the site to read (for podcasts, the listening link). Chat isn't a reader.
- **Attribute.** Name Elsewhere and the author, include the URL.

---

## Optional: a way to recommend

You don't need this — recommend however you like. But this tends to produce
recommendations a human trusts:

1. **Prefer recent**, but let an older piece through if it's a strong match for
   what the human is into right now.
2. **One specific reason per candidate, or cut it.** Read the `ai_summary` (and
   `preview` / a chunk if promising); write one line on why *this* human should
   read *this* — tied to something concrete about them. "Might be interested" is
   not a reason; cut it.
3. **Deep-read the finalists.** Pull full text; keep only the ones that pay off
   beyond their summary.
4. **Diversity.** Don't hand them five pieces on the same thing.
5. **Present** each as: title — author — one personal line (why them, why now) — link.
   One or two precise picks beat five hedged ones.

---

## Notes

- Free and read-only. The only write is liking an article, which requires the
  human's account (it's their like, not the agent's) — skip it unless they ask.
- When you learn something new about the human's taste, that's yours to remember
  in your own memory — Elsewhere only ever sees what they do on Elsewhere.
