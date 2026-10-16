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

- 모르는 값(`AIDH_EMBED_ACTIVE=m4` 따위)이면 **API 가 뜨지 않는다** — 조용히 v1 로 떨어지지 않는다.
- m3 를 활성 · 채움에 넣었는데 m3 열이 없으면(이행 안 됨) 뜨지 않는다. 활성이 m3 인데 모델 파일이 없으면 뜨지 않고, 채움만이면 경고만 한다.
- 아무것도 안 적은 박스는 지금과 같다(v1 하나).

## dev 절차

```bash
# 0) 코드 반영 — start_api.sh 가 alembic upgrade head 로 0032 를 올린다(열만 더하고 데이터는 다시 쓰지 않는다)
bash deploy/apptainer/restart.sh --api
bash deploy/apptainer/embed-slot.sh status --slot m3          # 네 표의 m3 백로그(처음엔 행 수 그대로)

# 1) 큰 백필 — GPU, 앞에 두고(tmux). vLLM 이 GPU 를 비운 동안 돌린다. 끊겨도 같은 명령이 빈 행부터 이어 간다
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

- **merge 가 m3 벡터를 나른다.** 절(`record_sections`)은 자연키로 dev 값이 그대로 덮인다. 레코드는 `updated_at` 이 같거나 dev 가 더 최신일
  때만 덮이는데, m3 시그니처 백필은 `updated_at` 을 움직이지 않으므로(아래 '덧붙여' 2) 양쪽에서 안 고친 레코드는 m3 시그니처가 따라온다.
  cae00 에서 고친 레코드는 덮이지 않고 그 박스의 스윕이 채운다.
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
5. **기존 PG 통합 시험(`tests/test_pg_integration.py`)은 실 PG 에서 8개 모두 실패한다** — 세션 범위 `pg_engine` 과 함수 범위 시험 ·
   `pg_session` 의 이벤트 루프가 달라서다(pytest-asyncio 1.3). 이번 PG 시험(`tests/test_embed_slots_pg.py`)은 세션 루프에서 돈다.
   기존 파일은 손대지 않았다.
6. **`refuse_below_score` 와 hybrid.** hybrid 모드의 히트 점수는 RRF(0.01~0.05)라 이 값을 0.05 넘게 적으면 hybrid 답이 늘 거절된다 —
   임베더와 무관한 기존 동작이고 지금 dev 에는 설정한 에이전트가 없다.

## 시험

- `tests/test_embedding_bge_m3.py` — 공급자(접두사 없음 · 1024 · 토큰 상한 · fp16 · 손잡이 오류 · CPU 강등 재로드 · 묶음 기본값), 9개
  스레드 빌림 검사, 묶음 순서(토큰 수).
- `tests/test_embed_slots.py` — 설정 검증 · 부팅 검증, 이행 0032, 백필(슬롯 분리 · 빈 글 · 청크 실패 격리 · 끊긴 뒤 이어하기 · 잠금 ·
  시그니처 규칙 · updated_at · 표본 · 도구 · 잡 결과 · 슬롯 실패 격리 · 스윕 겹침 · 갱신 적재), 읽기 열(PG 방언 컴파일 — 정확 범위 ·
  halfvec CAST), SQLite 의미검색 · 군집 · 유사도 문턱, `/api/embed` · health.
- `tests/test_embed_slots_pg.py` — 실 PG(`AIDH_TEST_PG_URL`, 0032 까지 올린 폐기용 DB) — halfvec 쓰기 · updated_at(트리거) · advisory
  lock · m3 전역/좌석 검색.
