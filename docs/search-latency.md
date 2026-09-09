# FTS 지연 — 원인 둘, 고친 것 둘 (2026-09-09)

`agent_search(mode="hybrid")` 가 **102초**, `mode="fts"` 가 **221초** 였다. 이 검색은
HWAX 심의의 좌석별 지식카드 주입(`deliberation.py`)과 챗 페르소나 지식 주입(`app.py`)이
좌석마다 부르는 경로다. 호출부가 타임아웃을 문자열로 삼켜서, 느린 게 아니라 **조용히
'지식 0건'** 으로 흘렀다.

## 실측

게이트웨이 경유, 같은 질의(`sim-drop-impact`, "낙하 충격 해석 세팅").

| mode | 경과 | hits |
|---|---|---|
| semantic | **0.1초** | 3 |
| tag | 1.0초 | 0 |
| hybrid | **102.5초** | 3 |
| fts | **221.4초** | 0 |

`EXPLAIN ANALYZE` — 862,910 행 **Parallel Seq Scan**, 행마다 `to_tsvector` 계산,
**25.6초**에 0건. AND 매칭이 0건이면 코드가 OR 로 **한 번 더** 돈다(`search_svc.py:135`).
섹션 질의와 레코드 질의가 각각 그러므로 4회 스캔이 된다.

```
Parallel Seq Scan on record_sections rs  (actual time=25605..25605 rows=0 loops=3)
  Filter: (to_tsvector('simple'::regconfig, content_text) @@ '''낙하'' & ''충격'' & ...)
  Rows Removed by Filter: 287637
Execution Time: 25633.010 ms
```

## 원인 ① 알고리즘 — 범위를 SQL 이 아니라 파이썬이 걸렀다

`hybrid_search` 가 `fts_search(session, q, limit=fetch_k)` 를 **범위 없이** 부르고,
돌아온 결과를 `record_ids` 로 파이썬에서 걸렀다(`search_svc.py:578-582`, 주석도
"record_ids/data_types 필터는 후처리" 라고 적혀 있었다).

한 좌석의 레코드는 중앙값 **47건**이다. 전체 53,622 건의 0.09% 다. 그런데 검색은
코퍼스 전체를 훑었다.

**느린 것만이 문제가 아니었다.** 전역 상위 18건을 뽑아 **그 다음에** 좌석 범위로 거르므로,
그 좌석의 문서가 전역 상위에 없으면 남는 게 0건이다. 실측에서 `fts` 가 0건이고
`semantic` 이 3건이던 이유가 이것이다 — **hybrid 의 FTS 절반이 사실상 아무것도
기여하지 않고 있었다.** 지연을 다 치르고 결과는 semantic 단독과 같았다.

→ `fts_search` 에 `record_ids` · `data_types` 를 1급 인자로 넣고 **SQL 술어로** 건다.

## 원인 ② 인덱스 — `to_tsvector` 식에 GIN 이 없었다

마이그레이션 전수에 `to_tsvector` 인덱스가 0건이었다. 표현식 인덱스가 없으면 PostgreSQL 은
행마다 `to_tsvector` 를 계산하며 전수 스캔한다.

→ 마이그레이션 `0031` 이 `record_sections.content_text` · `records.title` ·
`records.summary` 에 GIN 표현식 인덱스를 만든다. `CONCURRENTLY` + `autocommit_block`
이라 운영 중에도 쓰기를 막지 않는다.

두 수정은 **서로를 대체하지 않는다.** ①은 좌석 범위가 있는 `agent_search` 를 고치고,
②는 범위가 없는 전역 검색(`/api/search`, `mode="fts"`)을 고친다.

## 안전 확인

- `content_text` 최대 60,038자 / 평균 1,401자 — tsvector 1MB 한계에 걸리는 행 **0건**.
- `record_sections` 8.3GB(대부분 임베딩), `/data` 여유 3.6TB.
- `word is too long to be indexed`(2047자 초과 단어 무시)는 NOTICE 이고 실패가 아니다.

## 원인 ③ 배열 조회 — 인덱스가 있는데 쿼리 모양이 못 썼다

`agent_search` 는 매 호출 좌석의 레코드 목록을 뽑는다. 그 질의가
`'x' = ANY(records.agents)` 였는데, 배열 GIN 이 지원하는 연산자는 `@>` 와 `&&` 뿐이라
`idx_records_agents` 가 **처음부터 있었는데도** 죽어 있었다.

`EXPLAIN` 실측(53,622행) — Seq Scan **13.9ms** → Bitmap Index Scan **3.8ms**.
7곳을 `records.agents @> ARRAY[x]` 로 바꿨다.

## 최종 실측

| 경로 | 전 | 후 |
|---|---|---|
| `agent_search` fts | 221.4초 / 0건 | **0.1초 / 6건** |
| `agent_search` hybrid | 102.5초 / 3건 | **0.5초 / 6건** |
| `agent_search` semantic | 0.1초 / 3건 | 0.3초 / 3건 |
| 전역 `/api/search` fts | 25초 스캔 × 4 | **0.02~0.22초** |
| `recommend_agents` | — | 0.4~1.2초 |

## 호출부가 해야 할 것 — 느린 것을 상정한다

인덱스를 깔아도 이 경로는 네트워크 너머 도구 호출이다. 호출부는 **느릴 수 있다고
가정하고** 쓴다.

- 명시 타임아웃을 건다. `_call` 은 예외를 `"(tool X error: …)"` **문자열**로 삼키므로
  타임아웃이 정상 응답과 구분되지 않는다.
- 시간 안에 못 받으면 **`semantic` 으로 한 번 되묻는다.** hybrid 가 느린 것이지
  semantic 은 0.1초다.
- 그래도 못 받으면 **그 사실을 사용자에게 보인다.** 지식카드가 비었다는 것과 검색이
  실패했다는 것은 다르다 — 섞으면 "이 전문가는 아는 게 없다" 로 오독된다.

구현은 `HWAXAgentServer/deliberation.py` 의 `_agent_search_hits` 하나로 모았고,
심의·챗·띵킹이 그 하나를 쓴다.

---

## 손대지 않기로 한 것 — 샘플 임베딩 프리픽스 (2026-09-09)

`agent_sample_embeddings` 는 저장이 `passage:`, 검색이 `query:` 다. `sample_queries` 는
의미상 **질의문**이라 대칭(STS) 과제로 보이고, 그래서 비대칭이 점수를 왜곡한다는 의심이
있었다. 측정으로 기각한다.

7,611행 전체를 `query:` 로 재인코딩해 `recommend_svc` 의 sample 항 채점을 그대로 재현하고
leave-one-out 라우팅을 돌렸다.

| | top1 정답 | MRR |
|---|---|---|
| 현재 `query→passage` | **15.77%** | **0.2845** |
| 대칭 `query→query` | 15.61% | 0.2824 |

`ΔMRR = -0.0021` (SE 0.0024, **t = -0.89** — 유의하지 않음). 좌석 순위 개선 1,902 /
악화 1,923 으로 방향성이 없다. 1위 좌석이 41% 질의에서 바뀌지만 **순이득 없는 churn** 이다.

원시 분리도만 보면 대칭이 낫다(마진 0.2024 vs 0.1530). 그런데 end-to-end 에서 뒤집힌다 —
채점이 top-20 → 좌석당 상위 3 → 평균 → `(cos+1)/2` 를 거치며 그 차이가 사라진다.
재색인 비용을 치르고 얻는 것이 없으므로 **바꾸지 않는다.** 바꾼다면 `search_samples` 와
동시 배포 + 전량 재색인이 필수다(혼재하면 두 벡터 공간이 섞인다).

**벡터 인덱스도 지금은 불필요하다.** 7,611행, 호출당 정확히 1회 스캔, 20ms. 마이그레이션
0016 이 "수백 행" 을 가정해 건너뛴 판단은 낡았지만(25배), 아직 사고가 아니라 성장 함정이다.
행이 3만을 넘으면 0018 과 같은 형태로 HNSW 를 넣는다.

프리픽스보다 큰 문제는 따로 있다 — `(cos+1)/2` 매핑이 e5 의 [0.79, 0.94] 를
[0.895, 0.97] 로 압축해서, `_SAMPLE_WEIGHT=1.0` 이면 **전역 top-20 에 들기만 하면 누구나
+0.9 를 거의 평평하게 받는다.** `recommend_svc.py:256-262` 가 기록한 오라우팅 사고가
그 결과다. 프리픽스를 바꿔도 이건 안 움직인다. 별건으로 남긴다.
