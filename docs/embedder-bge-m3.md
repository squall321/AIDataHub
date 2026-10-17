# 임베더 BGE-M3 전환 — 임베딩 슬롯 운영 절차 (2026-10-16)

> 계획과 결정은 HWAXPortal `docs/embedder-bge-m3`(PLAN · context-notes EM-D1~) 에 있다. 이 문서는 AIDataHub 쪽의 **설정 · 절차 ·
> 잰 값**이다. 경로는 모두 리포 루트 기준이다(dev `~/claude/AIDataHub`, cae00 `~/Projects/AIDataHub`).

## 한눈에

- 지금 열(e5-base 768, **슬롯 v1**) 옆에 BGE-M3 열(`*_m3 halfvec(1024)`, **슬롯 m3**)을 나란히 둔다(이행 0032). 의미검색이 비는 때가 없다.
- 읽기는 `AIDH_EMBED_ACTIVE` 한 줄이 고른다. 되돌리기도 그 한 줄이다(v1 열은 그대로 있다).
- 큰 백필은 GPU 박스에서 CLI 로 앞에 두고 돌린다 — 청크마다 커밋하고 끊겨도 이어 간다. 색인은 그 뒤 `CONCURRENTLY`.
- 전환 전에 견주기용 두 번째 API(다른 포트, 검색만)로 같은 DB 에서 v1 · m3 를 잰다.
- cae00 은 110만 절을 CPU 로 다시 임베딩하지 않는다 — dev 가 계산한 m3 벡터를 merge 로 받는다(CPU 로는 약 6.6일).

## 설정

`deploy/apptainer/.env` 에 적는다. 바꾸면 API 재기동(`bash deploy/apptainer/restart.sh --api`).

| 손잡이 | 기본 | 뜻 |
|---|---|---|
| `AIDH_EMBED_ACTIVE` | `v1` | 읽는 슬롯 — 의미검색 · 하이브리드 · `agent_search` · 시그니처 유사도(`find_similar_data`) · 추천 · 맥락 묶음 · 군집 · 도구 검색 · `/api/embed`, 그리고 곧바로 쓰는 임베딩(업로드 절 · 시그니처 · 에이전트 표본 · 도구 설명) |
| `AIDH_EMBED_FILL` | `v1` | 백필(30분 스윕 · 매-tick 시그니처 · `POST /api/jobs/embed`)이 채워 두는 슬롯. **활성 슬롯은 적지 않아도 늘 채운다** |
| `AIDH_BGE_M3_MAX_SEQ` | `2048` | BGE-M3 가 읽는 토큰 상한(모델 한도 8,192). 넘친 절 수는 백필 결과의 `over_model_limit` 이다 |
| `AIDH_BGE_M3_BATCH` | GPU 32 · CPU 4 | 한 번에 인코딩하는 글 수. 묶음 하나가 모델 잠금을 쥐는 동안 같은 모델의 질의가 기다린다 |
| `AIDH_BGE_M3_FP16` | `1` | CUDA 면 반정밀도(CPU 는 늘 fp32) |
| `AIDH_EVAL_INSTANCE` | — | `1` 이면 견주기용 인스턴스 — 부팅 때 DB 에 쓰지 않고(동기화 소스 등록 · 멈춘 동기화 정리) 스케줄러를 띄우지 않는다 |
| `AIDH_HNSW_EF_SEARCH` | `1000` | 전역 의미검색의 HNSW 탐색 폭(두 슬롯 같은 값, pgvector 상한 1000). `0` 이면 걸지 않는다(PG 기본값 40 — 되돌리기). 아래 '덧붙여' 8 |

- 모르는 값(`AIDH_EMBED_ACTIVE=m4` 따위)이면 **API 가 뜨지 않는다** — 조용히 v1 로 떨어지지 않는다. `AIDH_HNSW_EF_SEARCH` 가 정수가 아니거나 음수여도 같다.
- m3 를 활성 · 채움에 넣었는데 m3 열이 없으면(이행 안 됨) 뜨지 않는다. 활성이 m3 인데 모델 파일이 없으면 뜨지 않고, 채움만이면 경고만 한다.
- 아무것도 안 적은 박스는 슬롯이 지금과 같다(v1 하나). 전역 의미검색의 탐색 폭만 40 에서 1000 으로 넓어진다(재현율@10 0.64 → 0.96).

## dev 절차

```bash
# 0) 코드 반영 — start_api.sh 가 alembic upgrade head 로 0032 를 올린다(열만 더하고 데이터는 다시 쓰지 않는다)
bash deploy/apptainer/restart.sh --api
bash deploy/apptainer/embed-slot.sh status --slot m3          # 네 표의 m3 백로그(처음엔 행 수 그대로)

# 1) 큰 백필 — GPU, 앞에 두고(tmux). vLLM 이 GPU 를 비운 동안 돌린다. 끊겨도 같은 명령이 빈 행부터 이어 간다
#    PG 데이터가 HDD 면 먼저 두 표의 표 · TOAST · 색인 파일(dev 약 11GB)을 한 번 순차로 읽어 OS 캐시에 올린다 — 읽기만 한다.
#    안 하면 쓰기가 색인 갱신의 무작위 읽기에 묶인다(dev 레코드 4.8행/초 → 데운 뒤 120행/초, 아래 '덧붙여' 7)
( set -a; . deploy/apptainer/.env; set +a; export PGPASSWORD="$POSTGRES_PASSWORD"
  D="$(readlink -f deploy/apptainer/data/postgres)/pgdata"
  apptainer exec instance://aidh_postgres psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -XAt -c "
    SELECT pg_relation_filepath(oid) FROM pg_class WHERE oid IN (
      SELECT unnest(ARRAY['records'::regclass, 'record_sections'::regclass]) UNION
      SELECT reltoastrelid FROM pg_class WHERE relname IN ('records', 'record_sections') UNION
      SELECT indexrelid FROM pg_index WHERE indrelid IN ('records'::regclass, 'record_sections'::regclass))" 2>/dev/null |
  while read -r f; do cat "$D/$f" "$D/$f".[0-9]* > /dev/null 2>&1; done )
bash deploy/apptainer/embed-slot.sh fill --slot m3 2>&1 | tee deploy/apptainer/logs/embed-m3-fill.log
#    작은 표(시그니처 · 표본 · 도구) 먼저, 절은 마지막. 30초마다 '채운 수/전체 · 행/초 · 남은 시간' 을 찍는다
#    끝나면 표준 출력에 JSON(표마다 채움 · 빈 글 · 실패 · 한도 넘침 · 초 · 행/초). 종료 코드 0 끝 · 1 실패 행 · 3 잠김

# 2) 색인 — CONCURRENTLY(표의 쓰기를 막지 않는다). 이미 유효하면 건너뛰고, INVALID 잔해는 지우고 다시 짓는다
bash deploy/apptainer/embed-slot.sh index --slot m3 --maintenance-work-mem 4GB --workers 2
#    진행은 psql 에서 SELECT phase, tuples_done, tuples_total FROM pg_stat_progress_create_index;

# 3) 새 행도 m3 를 채운다 — .env 에 AIDH_EMBED_FILL=v1,m3 를 적고 재기동
bash deploy/apptainer/restart.sh --api
bash deploy/apptainer/embed-slot.sh status --slot m3          # 1)~3) 사이에 들어온 행은 첫 스윕(재기동 1분 뒤)이 채운다 → 0

# 4) 견주기 — 같은 DB 에 활성 슬롯만 m3 인 두 번째 API(검색만, 다른 포트)
cd api_server
( set -a; . ../deploy/apptainer/.env; set +a
  export PYTHONPATH=src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 AIDH_EVAL_INSTANCE=1 AIDH_EMBED_ACTIVE=m3 AIDH_MCP_LOG=0
  exec .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8011 ) > ../deploy/apptainer/logs/eval-m3.log 2>&1 &
curl -s http://127.0.0.1:8011/api/system/health                 # embed_slot.active = m3, model = BAAI/bge-m3
D=/data/svc/aidh/eval/ko-search-20261016-m3; mkdir -p $D && cp /data/svc/aidh/eval/ko-search-20261016/queries.jsonl $D/
.venv/bin/python scripts/ko_search_eval/run_baseline.py run --mcp-url http://127.0.0.1:8011/mcp/ --data-dir $D
.venv/bin/python scripts/ko_search_eval/run_baseline.py report --data-dir $D
kill "$(ss -ltnpH 'sport = :8011' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"   # 그 포트의 리스너만(pkill -f 금지)
cd ..

# 5) (선택) 날 코사인 문턱을 dev 전체로 다시 잰다 — 읽기 전용 세션, 모델 둘을 올린다
(cd api_server && set -a && . ../deploy/apptainer/.env && set +a && PYTHONPATH=src HF_HUB_OFFLINE=1 \
  .venv/bin/python scripts/embed_calibrate.py --queries /data/svc/aidh/eval/ko-search-20261016/queries.jsonl)
#    m3 값이 api_server/src/api/services/embed_slots.py 의 THRESHOLDS["m3"] 와 크게 다르면 코드를 고친다

# 6) 전환 — .env 에 AIDH_EMBED_ACTIVE=m3 (AIDH_EMBED_FILL=v1,m3 는 그대로 둔다 — v1 도 계속 채워 되돌리기를 남긴다)
bash deploy/apptainer/restart.sh --api
curl -s http://127.0.0.1:8001/api/system/health                 # embed_slot.active = m3
#    /api/embed 응답의 model 이 BAAI/bge-m3 · dim 1024 로 바뀐다 — 포털 대화 검색 · 에이전트 서버가 그것을 보고 다시 색인한다

# 7) 되돌리기 — .env 의 AIDH_EMBED_ACTIVE 를 v1 로(또는 지운다) → 재기동. v1 열은 FILL 에 v1 이 있는 한 최신이다
bash deploy/apptainer/restart.sh --api
```

**이행이 잠금을 10초만 기다린다.** 0032 의 ALTER TABLE 은 잠깐 AccessExclusive 잠금을 잡는다 — 그 표에 오래 열린 트랜잭션(예전
임베딩 스윕은 절 21만 개를 한 트랜잭션으로 쥐었다)이 있으면 기다리는 동안 뒤에 줄 선 검색까지 선다. 그래서 `lock_timeout` 10초에
'canceling statement due to lock timeout' 으로 실패하고(start_api.sh 는 alembic 오류로 멈추고 옛 API 는 그대로 돈다) 0031 에 남는다.
그 트랜잭션이 끝난 뒤 다시 올린다 — 예전 스윕이 끝나기를 기다리거나, 옛 API 를 먼저 내린다(`kill "$(cat deploy/apptainer/logs/api.pid)"`
뒤 `bash deploy/apptainer/start_api.sh` — `stop.sh` 는 PG 까지 내린다). cae00 의 갱신 흐름(HWAXPortal update-all → deploy-all →
`boot.sh --force` → `start_api.sh`)도 이행을 옛 API 를 내리기 **전에** 돌린다 — 그때 옛 스윕이 돌고 있으면 update-all 에 'aidh failed' 가
보이고 옛 API 가 옛 코드로 계속 돈다. 다음 update-all 이 다시 올린다. `restart.sh --api` 는 옛 API 를 먼저 내리므로 이 경합이 없다.

**순서 — 백필을 먼저, FILL 은 그 뒤.** FILL 에 m3 를 먼저 넣고 재기동하면 인앱 스윕이 1분 뒤 m3 절 백필을 API 프로세스 안에서 시작해
(m3, record_sections) 잠금을 쥔다. 그러면 CLI 는 그 표를 '잠김'(종료 코드 3)으로 건너뛴다. 그렇게 둬도 같은 일을 하지만 진행은
`deploy/apptainer/logs/uvicorn.log` 에서만 보이고 API 재기동이 백필을 끊는다(다시 띄우면 이어 간다).

**견주기 인스턴스는 검색만 받는다.** 같은 DB 를 쓰는 본 인스턴스의 동기화를 'stale' 로 지우는 부팅 정리 · 동기화 소스 등록 ·
스케줄러를 건너뛴다(`AIDH_EVAL_INSTANCE=1`). BGE-M3 를 따로 올리므로 GPU 를 1~3GB 더 쓴다.

## cae00 절차 (사람)

```bash
# dev(온라인)에서 — 모델 묶음. bge-m3 는 실제로 읽는 파일만 묶어 2.2GB 다(캐시 통째는 6.4GB)
AIDH_MODEL_REPOS="intfloat/multilingual-e5-base BAAI/bge-m3" bash deploy/apptainer/model-to-drive.sh

# cae00 에서
AIDH_MODEL_REPOS="BAAI/bge-m3" bash deploy/apptainer/model-from-drive.sh   # .env 를 바꾸기 전에 받는다
# 코드 갱신(update 흐름) — start_api.sh 가 0032 를 올린다. 아직 AIDH_EMBED_* 는 적지 않는다
bash deploy/apptainer/merge-from-drive.sh            # dev 의 m3 백필 · 색인 뒤에 만든 덤프여야 한다
bash deploy/apptainer/embed-slot.sh status --slot m3 # 남은 것 = cae00 에서만 생긴 행(+ cae00 에서 고친 레코드)
bash deploy/apptainer/embed-slot.sh index --slot m3
# 전환 — .env 에 AIDH_EMBED_ACTIVE=m3 한 줄(채움에 m3 가 저절로 든다 — 남은 행은 인앱 스윕이 CPU 로 채운다)
bash deploy/apptainer/restart.sh --api
```

- **merge 가 m3 벡터를 나른다.** 절(`record_sections`)은 자연키로 dev 값이 그대로 덮인다. 레코드는 dev 의 `updated_at` 이 더 최신일 때만
  덮이고, m3 시그니처 세 열(`signature_embedding_m3` · `_at` · `_model`)은 cae00 값이 비어 있으면 `updated_at` 과 상관없이 채운다(있는 값은
  덮지 않는다). 처음엔 `updated_at` 조건만 있었는데, 예전 병합이 cae00 의 `updated_at` 을 병합 시각으로 올려 둬서 10-17 cae00 병합은 레코드
  m3 시그니처를 한 건도 옮기지 못했다(59,032건, 리뷰 H1-b). 이제 병합 세션도 `aidh.keep_updated_at` 을 켜 cae00 `updated_at` 을 dev 값
  그대로 둔다.
- **이행이 늦으면 병합이 3 으로 끝난다.** 덤프에 있는데 cae00 DB 에 아직 없는 열(0032 가 잠금으로 늦은 경우)은 `⚠ <표>: dev 에만 있는 열 — …`
  로 찍고 종료코드 3 이다 — 포털 update-all 은 '병합함' 표식을 안 적어 다음 실행이 다시 병합한다. 이행 뒤 `merge-from-drive.sh` 를 한 번 더.
- **m3 를 merge 전에 켜지 않는다.** 활성 · 채움에 m3 를 먼저 넣으면 인앱 스윕이 109만 절을 CPU 로 임베딩하기 시작한다(1.9 절/초 — 약
  6.6일, 그동안 같은 모델의 질의가 묶음마다 기다린다).
- **덤프는 dev 가 이행한 뒤의 것이어야 한다.** merge 는 cae00 의 열 목록으로 덤프에서 고른다 — cae00 은 0032 를 올렸는데 덤프가 그 전
  것이면 m3 열이 없어 그 표의 merge 가 실패한다.
- **merge 가 길어진다.** 덤프를 staging DB 에 풀 때 dev 의 색인도 다시 짓는다 — m3 HNSW(약 2.8GB)가 v1 HNSW 옆에 하나 더 붙는다.
- 전환 뒤 cae00 질의 임베딩은 CPU 다 — dev CPU(16 스레드)에서 짧은 질의 p50 BGE-M3 71ms · e5-base 18ms 였다.

## 잰 값 (dev, 2026-10-16)

폐기용 PostgreSQL(같은 postgres SIF, pgvector 0.8.2)에 dev 표본을 읽어 왔다(읽기 전용) — 절 10,852개(평가셋 정답 카드 236장의 절
4,312 + 무작위 0.6%), 레코드 6,029 · 에이전트 표본 305 · 도구 4. 이행 0032 를 올리고 실 BGE-M3 로 백필 · 색인 · 검색을 돌렸다.
GPU(RTX 5070 Ti 16GB)는 그 시간 내내 dev AIDataHub 의 옛 스윕이 함께 쓰고 있었다(사용률 100%).

**처리량 — 실제 CLI 경로(읽기 · 인코딩 · 쓰기 · 청크 커밋).**

| 표 | 행 | 행/초 |
|---|---|---|
| 절 — 청크 1,024 · 토큰 수 정렬(지금 기본값) | 10,781 | **94.6** |
| 절 — 청크 256 · 토큰 수 정렬 | 10,781 | 82.9 |
| 절 — 청크 256 · 글자 수 정렬(sentence-transformers 방식) | 10,781 | 71.5 |
| 레코드 시그니처 | 6,029 | 442 |
| 에이전트 표본 | 305 | 693 |

- **절 1,090,531개 예상 약 3시간 12분**(94.6 절/초, GPU 를 나눠 쓴 값 — GPU 가 한가하면 더 짧다). 시그니처 5만 9천 건은 2~3분이다.
- 묶음은 토큰 수로 세운다 — 한국어 · 영어가 섞여 같은 글자 수의 토큰 수가 몇 배씩 달라, 글자 수로 세운 묶음 32 의 덧채움이 79%(토큰 수
  정렬 7%)였다. 무작위 절 1,024개 인코딩만 재면 묶음 16 · 32 · 64 가 104 · 101 · 97 절/초다(글자 수 정렬 묶음 32 는 56).
- GPU 메모리 — 백필 프로세스 최대 4.8GiB(nvidia-smi, 컨텍스트 · 캐시 포함), 묶음 32 의 최대 할당 2.6GiB.
- 색인 — 표본 10,781행 1초 · 28MB. 109만 행이면 약 2.8GB 다(v1 HNSW 3.4GB). 짓는 시간은 전체로 재지 않았다.
- CPU(dev 16 스레드) — BGE-M3 백필 1.9 절/초(e5-base 11.3), 짧은 질의 p50 71ms(e5-base 18ms).

**dev 실주행 — 전체(2026-10-16, 실 DB · HDD).** 위 표본 수치를 실 DB 에서 다시 쟀다.

| 무엇 | 행 | 시간 | 비고 |
|---|---|---|---|
| m3 절 백필 | 1,090,100(빈 글 431 · 실패 0) | 10,091초(108행/초) | 처음 49분은 GPU 를 인앱 v1 스윕과 나눠 썼다 · 2,048토큰 넘침 23,566(2.2%) |
| m3 레코드 시그니처 | 59,032 | 492초(120행/초) | 첫 3분은 캐시 데우기 전(4.8행/초, '덧붙여' 7) |
| m3 표본 · 도구 | 7,670 · 4 | 12.8초 · 0.1초 | |
| 인앱 v1 스윕(같은 시간, 새 청크 백필) | 214,837(실패 0) | 2,845초(75.5행/초) | e5 512토큰 넘침 105,120(49%) |
| m3 HNSW — 절 | 1,090,100 | 469초 · 2,825MB | maintenance_work_mem 4GB · workers 2(v1 HNSW 4.2GB) |
| m3 HNSW — 레코드 · 도구 | 59,032 · 4 | 16초 · 151MB · 0초 | |

**절 길이(BGE-M3 토큰, 무작위 절 6,537개).** p50 476 · p90 714 · p99 2,789 · 최대 7,191. 512 를 넘는 절이 35.6%, 1,024 6.4%,
2,048 2.4%, 4,096 0.18% 다. **e5-base 는 512 토큰에서 자르므로 지금 절의 36.5%가 뒤쪽이 벡터에 들지 않는다** — BGE-M3 2,048 이면
2.4%다(백필 표본에서 166/10,781 = 1.5%).

**점수 분포(같은 표본, 질의 397개).**

| | e5-base(v1) | BGE-M3(m3) |
|---|---|---|
| 질의–절 코사인 최소 · 중앙 · 최대 | 0.652 · 0.755 · 0.927 | 0.108 · 0.363 · 0.861 |
| 정답 카드의 가장 가까운 절(중앙) | 0.859 | 0.661 |
| 정답 아닌 1위 절(중앙) | 0.840 | 0.556 |
| 표본 안 전역 hit@10(참고) | 0.857 | 0.995 |

e5 는 정답과 정답 아닌 1위의 간격이 0.02 이고 BGE-M3 는 0.105 다. 표본 hit@10 은 레코드 6천 건 안에서 잰 것이라 운영 숫자가 아니다 —
견주기(절차 4)가 그 몫이다.

## 절대 점수 문턱 — 전수 (EM-D7)

검색 점수 `score` 는 `(1 + cos) / 2` 다. 날 코사인 문턱은 슬롯마다 둔다(`embed_slots.THRESHOLDS`) — m3 값은 같은 표본에서 e5 문턱이
가르는 몫(분위)을 BGE-M3 분포에서 같은 몫이 되는 값으로 옮긴 것이다(높음 · 중간 · 같은 그룹의 비율을 지금과 같게 둔다).

| 자리 | 값 | 척도 | 판정 |
|---|---|---|---|
| 에이전트 `retrieval_config.score_threshold` — `agent_search`(semantic 은 min_score, hybrid 는 score_semantic) · 미리보기 · 맥락 묶음 | dev 774개 0.3 · 1개 0.5(market-voc-analyst) · 21개 없음 | score → 코사인 −0.4 · 0 | **그대로** — 표본의 질의–절 쌍 중 걸러지는 몫이 두 모델 모두 0(BGE-M3 최소 코사인 0.108) |
| 군집 `min_score`(`/api/groups/auto`, 대시보드) | 0.4 | score → 코사인 −0.2 | **그대로** — 두 모델 모두 0 |
| 시그니처 확신(`find_similar_data`) high · medium | 0.93 · 0.88 | 날 코사인 | **슬롯마다** — m3 0.74 · 0.55(최근접 이웃이 e5 0.93 이상 29.6% · 0.88 이상 96.9% 와 같은 몫) |
| 군집 `build_query_groups` sim_threshold | 0.85 | 날 코사인 | **슬롯마다** — m3 0.55(히트 절 쌍, e5 0.85 이상 18.3%) |
| 군집 `cluster_around_record` sim_threshold | 0.85 | 날 코사인 | **슬롯마다** — m3 0.58(레코드 첫 절 쌍, e5 0.85 이상 1.74%). API 기본값을 비워 슬롯 값을 쓰게 했다(주면 그 값) |
| 에이전트 `response_config.refuse_below_score` | dev 0/796 설정 | score(재정렬기가 켜지면 그 점수) | **지금 효과 없음** — e5 로 정한 값이면 BGE-M3 에서 대부분 거절한다(정답 절 score e5 ≈0.93 · m3 ≈0.83). 적을 때 슬롯을 보고 정한다 |
| 추천 점수 가중(`recommend_agents`) | 기록 평균 + 1.0×표본 평균 + 2.0×설명 일치 | score 평균 + 어휘 | **문턱 아님 · 손대지 않음** — 기록 · 표본 항이 e5 ≈0.90~0.93 → m3 ≈0.70~0.85 로 내려 어휘 항(2.0)의 몫이 커진다. 평가셋에 추천이 없어 견주기에서 따로 본다 |
| `Embedder.recommended_similarity_threshold` | 0.85 · 0.80 · 0.92 | 날 코사인 | 쓰는 곳 없음 |
| 재정렬기(크로스인코더) | — | 자체 점수 | 임베더와 무관 |

dev 전체로 다시 재려면 `scripts/embed_calibrate.py`(절차 5).

## 덧붙여 — 이번에 고친 것 · 본 것

1. **모델 잠금.** HF fast tokenizer 는 여러 스레드가 함께 쓰면 'Already borrowed' 로 죽는다(dev 2026-10-16 — 백필 묶음과 검색 질의).
   임베더마다 잠금 하나, 묶음마다 놓는다. 질의 인코딩은 스레드로 옮겨 잠금을 기다리는 동안 이벤트 루프가 서지 않는다.
2. **임베딩 쓰기는 `updated_at` 을 움직이지 않는다.** records 의 BEFORE UPDATE 트리거(0002)는 어떤 UPDATE 든 `updated_at` 을 now() 로
   덮는다 — 그대로면 m3 시그니처 백필이 레코드 5만 9천 건을 오늘로 바꿔 cae00 merge 가 cae00 의 최근 편집을 덮고 `updated_at` 순 목록이
   뒤섞인다. 트리거 함수가 `SET LOCAL aidh.keep_updated_at = 'on'` 인 트랜잭션은 건너뛴다(백필만 쓴다). 예전 v1 시그니처 백필은 이
   트리거로 `updated_at` 을 늘 바꿨다.
3. **스윕이 겹치지 않는다.** 예전에는 앞 스윕이 돌아도 30분마다 새 잡을 띄웠고, 빈 절 21만 개를 ORM 객체로 올려 RSS 16GB · 한 트랜잭션
   이었다. 지금은 프로세스 안에서는 앞 잡을 보고, 프로세스 밖(CLI)과는 (슬롯, 표)마다 advisory lock 이 막는다.
4. **부팅 때 풀의 첫 연결은 버려진다.** import 때 `mcp_uploads` 적재가 `asyncio.run` 으로 맺은 연결이 풀에 남아 lifespan 의 첫 연결이
   'attached to a different loop' 로 죽는다(예전부터 — 그동안 차원 검사가 조용히 삼켰다). 슬롯 열 검사는 두 번까지 시도한다.
   — 10-17 고침(cae00 리뷰 H3): 그 적재는 이제 풀 없는 엔진으로 읽고 닫는다.
5. **기존 PG 통합 시험(`tests/test_pg_integration.py`)은 실 PG 에서 8개 모두 실패한다** — 세션 범위 `pg_engine` 과 함수 범위 시험 ·
   `pg_session` 의 이벤트 루프가 달라서다(pytest-asyncio 1.3). 이번 PG 시험(`tests/test_embed_slots_pg.py`)은 세션 루프에서 돈다.
   기존 파일은 손대지 않았다.
6. **`refuse_below_score` 와 hybrid.** hybrid 모드의 히트 점수는 RRF(0.01~0.05)라 이 값을 0.05 넘게 적으면 hybrid 답이 늘 거절된다 —
   임베더와 무관한 기존 동작이고 지금 dev 에는 설정한 에이전트가 없다.
7. **실 DB 에서는 백필 쓰기가 색인 갱신에 묶인다 — HDD 면 캐시를 먼저 데운다(dev 실주행 2026-10-16 09:28~ UTC).** 새 열을 채우는
   UPDATE 는 대부분 HOT 이 아니다 — 페이지에 새 판을 둘 자리가 없어서다(절 7% · 레코드 30%만 HOT). HOT 이 아닌 갱신은 그 표의 **모든
   색인에 새 항목**을 넣는다. 절이면 v1 HNSW(3.5GB) · 전문 검색 GIN · btree 넷이고, 레코드면 시그니처 HNSW · content GIN
   (jsonb_path_ops 라 content 를 풀어 읽는다) 등 17개다. dev 의 PG 데이터는 HDD(`/data`)이고 `shared_buffers` 는 기본값 128MB 라 첫
   청크가 무작위 읽기에 묶였다(레코드 1,024행에 약 3분 · 디스크 사용률 100%). 위 절차의 캐시 데우기(약 11GB, 1분 남짓) 뒤로 레코드
   120~280행/초, 절 155~160행/초(GPU 를 인앱 v1 스윕과 나눠 쓰며)가 됐고 디스크 읽기는 0 이 됐다 — 그 뒤로는 쓰기(WAL · 체크포인트,
   10분에 WAL 5GB · 요청 체크포인트 6번)가 디스크를 채운다. PG 설정은 바꾸지 않았다.
   - 색인이 얼마나 자라나 — v1 HNSW 는 거의 자라지 않는다. pgvector 는 이미 있는 원소와 같은 벡터를 그 원소의 힙 TID 목록에 덧붙이기
     때문이다(HOT 아닌 갱신 약 6만 8천 건 — v1 스윕이 새로 넣은 벡터 포함 — 동안 3,521→3,551MB). 그래서 검색 후보가 죽은 옛 판에
     밀리지 않는다. 갱신마다 드는 비용은 그 같은 원소를 찾는 그래프 탐색이고, 위의 무작위 읽기가 그것이다. btree 는 PG 의 bottom-up
     삭제로 자라지 않았고(pk 44MB 그대로), 전문 검색 GIN 은 561→581MB 로 자랐다. 죽은 옛 판은 자동 VACUUM 이 치운다. m3 색인을
     CONCURRENTLY 로 지을 때 그 VACUUM 과 잠금이 겹치면 PG 가 자동 VACUUM 쪽을 취소한다.
8. **전역 의미검색은 HNSW 탐색 폭 40 에서 정답을 놓친다 — 두 슬롯 모두(dev 2026-10-16).** 견주기에서 BGE-M3 전역 검색이 한국어 질의에 무관한
   영어 문서를 냈다('라즈베리 파이 운영 체제' → TLS RFC). 저장 벡터는 맞았고(무작위 24행, 다시 임베딩한 것과 코사인 1.000), 정확 검색(전수)은
   정답 카드를 1위로 냈다(코사인 0.757) — 색인 탐색이 그래프의 엉뚱한 무리에 갇혀 더 먼 절(0.513)을 냈다. 질의 80개(한국어 · 영어 반반)로
   정확 검색 상위 10 과 색인 상위 10 의 겹침을 쟀다.

   | ef_search | v1 재현율@10 | m3 재현율@10 | v1 p50 · p95 | m3 p50 · p95 |
   |---|---|---|---|---|
   | 40(PG 기본) | 0.641 | 0.665 | 1.7 · 3.5ms | 1.7 · 2.6ms |
   | 200 | 0.820 | 0.891 | 4.3 · 6.7ms | 5.5 · 7.0ms |
   | 400 | 0.895 | 0.931 | 7.6 · 11.5ms | 8.9 · 11.8ms |
   | 1000 | 0.958 | 0.971 | 18.7 · 25.7ms | 22.0 · 29.6ms |

   지연은 데운 캐시에서 상위 30(하이브리드가 가져오는 수)이다 — 차가운 HDD 에서는 탐색이 닿는 색인 페이지를 읽느라 수 초까지 갔다.
   그래서 전역 검색은 이 트랜잭션에만 `SET LOCAL hnsw.ef_search = 1000` 을 건다(`embed_slots.tune_hnsw`, 요청 수보다 작게 두지 않는다).
   좌석 범위 검색은 정확 검색이라 상관없다. 레코드 시그니처 색인(5만 9천 행)은 40 에서도 v1 0.985 · m3 0.982 라 그대로 둔다.
   e5 도 낮았으므로 **지금 v1 로 도는 박스도 이 판을 받으면 전역 의미검색 재현율이 오른다.**
9. **v1 표지(`embedding_model`)는 믿지 않는다.** cae00 에서 79만 행이 'e5-small-d384' 라고 적혀 있는데 실제 벡터는 768차원 e5-base 다(표본 —
   노름 1.000 · 교차 유사도 같은 공간, 10-17 cae00 리뷰 H5). 지금은 표지로 재임베딩을 거는 코드가 없어(채움은 빈 열만 본다 —
   `embed_backfill`) 고치지 않았다. 표지를 보고 다시 임베딩하는 코드를 만들 때는 표지가 아니라 열의 차원과 표본 대조로 판정한다 — 아니면
   80만 행을 다시 계산한다.

## 시험

- `tests/test_embedding_bge_m3.py` — 공급자(접두사 없음 · 1024 · 토큰 상한 · fp16 · 손잡이 오류 · CPU 강등 재로드 · 묶음 기본값), 9개
  스레드 빌림 검사, 묶음 순서(토큰 수).
- `tests/test_embed_slots.py` — 설정 검증 · 부팅 검증, 이행 0032, 백필(슬롯 분리 · 빈 글 · 청크 실패 격리 · 끊긴 뒤 이어하기 · 잠금 ·
  시그니처 규칙 · updated_at · 표본 · 도구 · 잡 결과 · 슬롯 실패 격리 · 스윕 겹침 · 갱신 적재), 읽기 열(PG 방언 컴파일 — 정확 범위 ·
  halfvec CAST), SQLite 의미검색 · 군집 · 유사도 문턱, `/api/embed` · health.
- `tests/test_embed_slots_pg.py` — 실 PG(`AIDH_TEST_PG_URL`, 0032 까지 올린 폐기용 DB) — halfvec 쓰기 · updated_at(트리거) · advisory
  lock · m3 전역/좌석 검색.
- 본 venv(`api_server/.venv`)에는 `aiosqlite` 가 없어 SQLite 픽스처를 쓰는 시험 270여 개가 건너뛰어진다. 돌리려면 venv 는 그대로 두고 따로
  깐 곳을 `PYTHONPATH` 에 붙인다(`pip install --target <임시 폴더> aiosqlite`). 그렇게 돌리면 합친 main(`e036812`)이 629 통과 · 35 실패다.
  **35건은 합치기 전 main(`d99ddc9`)도 똑같이 실패한다** — 적재 · 가져오기 · 시드 시험('team HE is not registered' 등)이고 임베딩과 무관하다.
- `tests/test_hnsw_ef_search.py` — 전역은 검색 문장 앞에 `SET LOCAL hnsw.ef_search` 를 건다(두 슬롯) · 좌석 범위에는 걸지 않는다 · 0 이면
  걸지 않는다 · 요청 수보다 작게 두지 않고 1000 을 넘지 않는다 · 잘못된 값은 부팅에서 멈춘다. 변이 4/4.
