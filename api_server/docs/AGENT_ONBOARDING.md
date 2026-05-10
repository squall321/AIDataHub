# AI Agent Onboarding — AI Data Hub

> **If you are an AI agent reading this, this document is your starting point.**
> You should be able to use the entire hub from this single file plus
> `GET /api/discover`. You do not need to read any backend source code.

## 0. The mental model

The hub is a single PostgreSQL table (`records`) wrapped behind a REST API
and an MCP stdio server. Every record has:

| field | meaning |
|---|---|
| `id` | human-readable code, e.g. `DOC-HE-CAE-2026-000001` |
| `data_type` | `DOC` / `DATA` / `SIM` / `CAD` / `LOG` / `FORM` / `OTHER` |
| `team`, `group`, `year`, `seq` | parsed from the id |
| `title`, `summary`, `tags[]` | human metadata |
| `agents[]` | which agent types may use this record |
| `content` | data_type-specific JSON payload (DOC: sections, DATA: rows, ...) |
| `classification`, `status`, `domain`, `language`, `quality_score`, ... | classification meta |
| `capabilities[]` | structural labels (`sections`, `tables`, `attachments`, ...) |
| `parent_record_id`, `related_record_ids[]` | graph relations |
| `agent_hints` | free-form text written for **you**, the agent |
| `query_examples[]` | sample natural-language queries that fit this record |

The same record may be returned through many "views" (slim, hierarchical,
tabular, by-agent, by-tag, by-section, ...). All views eventually drill down
to a single record id.

## 1. The 4-step pattern

Every agent task collapses into these four steps:

```
1. discover   GET /api/discover                       (the map)
2. narrow     POST /api/ask {"query":"..."}            (or filtered GET /api/records)
3. detail     GET /api/records/{id}                    (one full record)
4. traverse   record.related_record_ids / parent_record_id / agents[]
```

If you already know the agent_type or data_type, skip step 1 and go to step 2.

## 2. The endpoints you need

### Discovery (read this first)

| endpoint | purpose |
|---|---|
| `GET /api/discover` | global catalog, counts, agent list, **`starting_points` URLs** |
| `GET /api/schema` | machine-readable JSON Schema (draft-2020-12) |
| `GET /api/hints?context=getting_started` | natural-language hints |
| `GET /api/docs/llm.txt` | this entire API in 5-10 KB markdown |

### Search / filter

| endpoint | purpose |
|---|---|
| `POST /api/ask` | natural-language → `{interpreted_query, results, follow_up_queries}` |
| `GET /api/records?...` | filter by `data_type`, `team`, `group`, `year`, `agent`, `tag`, `q`, `capabilities` |
| `GET /api/data?agent=...` | agent-scoped search with relevance scoring (Cline SR core) |
| `GET /api/search?mode=fts\|tag\|semantic` | classic search modes |

### Detail / drill-down

| endpoint | purpose |
|---|---|
| `GET /api/records/{id}` | full record |
| `GET /api/records/{id}/sections` | DOC: section list |
| `GET /api/records/{id}/tables` | tables only |
| `GET /api/records/{id}/figures` | figures only |
| `GET /api/records/{id}/attachments` | attachments meta (binaries via `/attachments/{...}`) |

### Cross-record analytics

| endpoint | purpose |
|---|---|
| `GET /api/analytics/distribution` | counts by data_type / team / group / year |
| `GET /api/analytics/common-tags?agent=...` | top tags within an agent's scope |
| `GET /api/analytics/cross-agent?agents=A&agents=B` | shared records |
| `GET /api/analytics/timeline?year=2026` | monthly counts |

## 3. MCP tool table

When connected over MCP stdio, you can call these tools (their docstrings
are written for you to read at tool-listing time):

| tool | when to use |
|---|---|
| `discover_schema()` | **always first** — fuses `/api/discover` + `/api/schema` |
| `discover_capabilities(agent_type)` | what's available for one agent |
| `ask(query, limit=5)` | natural-language search |
| `find_related(record_id, mode='auto')` | tags + graph + semantic |
| `explain_field(field_name)` / `explain_schema(field_name)` | inspect one schema field (aliases — same behavior) |
| `query_data(agent, query, limit)` | classic agent-scoped search |
| `list_agents()` | enumerate agent types |
| `get_record(record_id)` | fetch one full record |
| `search(mode, query, tags)` | classic FTS / tag / semantic |

Pick `ask` when the user's intent is conversational. Pick `query_data` or
`search` when you already know the structured filters.

## 4. Five common scenarios

### 4.1 "User asks about IGA"

```
ask("IGA 관련 최근 자료")
# or, if you prefer structured:
GET /api/data?agent=iga-analyst&query=IGA&limit=5
```

### 4.2 "User wants the table from a specific report"

```
GET /api/records/DOC-HE-CAE-2026-000001
GET /api/records/DOC-HE-CAE-2026-000001/tables
```

### 4.3 "User wants high-quality approved documents in 2026"

```
ask("2026년 approved quality 80 이상 문서")
# returns interpreted_query: {year:2026, status:'approved', quality_score_gte:80}
```

### 4.4 "User wants to find related records to a known one"

```
find_related("DOC-HE-CAE-2026-000001", mode="auto")
# returns related[] (graph + tags + semantic merged) + by_mode breakdown
```

### 4.5 "User asks something but you have no idea where it lives"

```
discover_schema()
# inspect data_types_explained + agents[] + starting_points
ask("<user phrase>")
# follow_up_queries point to the next drill-down
```

## 5. What NOT to do

- **Do not try SQL.** There is no SQL endpoint — use the REST API.
- **Do not guess field names.** Always call `discover_schema()` or
  `explain_field()` (alias `explain_schema()`) first. The schema is the
  source of truth.
- **Do not paginate without an upper bound.** Every list endpoint has a
  `limit` (max 100 for `/api/records`, 50 for `/api/ask`, 20 for
  `/api/data`).
- **Do not crawl the figures / attachments static mounts.** Use the meta
  endpoints (`/api/records/{id}/attachments`) and only fetch binaries you
  actually need.
- **Do not hard-code enum values.** Read them from `/api/schema` —
  `data_types`, `classifications`, `statuses`, `derivations`, `languages`,
  `access_patterns`, `capabilities` are all centrally defined there.
- **Do not assume a single language.** Records may be `ko`, `en`, or
  `mixed`. Filter on `language` only when the user's intent is explicit.

## 6. Self-describing contract

The API is intentionally self-describing. The single contract you must
honor:

> Before answering a question whose data you do not already have,
> call **one** of: `discover_schema()`, `ask()`, or `GET /api/discover`.

That is enough to find anything in the hub.

## 7. Where each piece lives

| concern | endpoint |
|---|---|
| "what data types exist?" | `/api/discover.data_types_explained` |
| "what agent types exist?" | `/api/discover.agents` |
| "what is field X?" | `/api/schema.properties.X` (or `explain_field("X")` / `explain_schema("X")`) |
| "how do I do Y?" | `/api/hints?context=Y` |
| "give me everything in 5KB" | `/api/docs/llm.txt` |
| "translate this question to filters" | `POST /api/ask` |
| "filter records by structured keys" | `GET /api/records?...` |
| "find related" | `find_related(id, mode='auto')` |

## 8. Versioning

`/api/discover.version` is the contract version (currently `1.0`). When
fields are added in a backwards-compatible way the minor version is bumped.
Breaking changes bump the major version and are accompanied by a new
`/api/schema` revision. Pin the version you saw at onboarding if your
behavior depends on a specific contract.
