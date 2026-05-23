# AI Data Hub — 검색·MCP 종합 보강 마스터 플랜

작성일: 2026-05-23 (rev 2 — wave-6 추가, Python 3.12 default 명시)
범위: 검색 품질 / MCP 완성도 / 운영 가시성 / 외부 도구 자동 MCP 등록 (wave-5) / MCP federation (wave-6)
가이드: 메모리 규칙 (dev PC install 금지, 드라이버 자동설치 금지) 준수. 모든 단계는 user-space 또는 타겟 서버 운영자 작업.

**Runtime defaults (wave-5 컨테이너 base 선정 기준)**:

| Runtime | Default 버전 | 비고 |
|---|---|---|
| Python | **3.12** | numpy/pandas/matplotlib 등 주요 라이브러리 모두 지원, 안정 |
| Node | **20** (LTS) | npm 10+ |
| JDK | **17** (LTS) | jar 실행 |
| .NET | **8** (LTS) | self-contained 직접 실행 |

사용자가 매니페스트에 명시하지 않으면 위 default 적용. 다른 버전이 필요하면 명시.

---

## 1. 전체 로드맵 한 눈에

| Wave | 주제 | 상태 | 커밋 | 버전 |
|---|---|---|---|---|
| 0 | GPU 사용 셋업 (사용자 드라이버 사전 설치 전제) | DONE | `940242e` | — |
| 1 | HNSW 인덱스 / MCP 호출 로깅 / GPU batch 자동 | DONE | `bdc54f9` | api 0.2.0 |
| 2 | 하이브리드 검색 (RRF) / MCP prompts 3종 / recommend 0건 fallback / 부분 sections | DONE | `bdc54f9` | api 0.2.0 |
| 3 | section_path / figure refs / chunk window / cross-encoder rerank opt-in | DONE | `bdc54f9` | api 0.2.0 |
| 4 | 셸스크립트 → MCP tool 동적 등록 + 6종 보안 게이트 | DONE | `d746dd3` | api 0.3.0 |
| 4.1 | Extension 클라이언트 확장 (Continue / RooCode / Windsurf) | DONE | `b3ba3da` | ext 0.16.0 |
| 4.2 | fetch_rerank_model wave-4 예제 도구 (HF prefetch) | DONE | `b3ba3da` | api 0.3.0 |
| **5** | **CLI binary → MCP tool 자동 등록 (4 phase)** | **PLAN** | (미진행) | api 0.4.0 예정 |
| **6** | **MCP Federation / Proxy (외부 FastMCP 서버 통합 4 phase)** | **PLAN** | (미진행) | api 0.5.0 예정 |
| (deferred) | C-4 인증/Origin 가드 (wave-6 P4 와 연계) | 보류 | — | — |

---

## 2. 완료된 항목 — 세부 내역

### Wave-1 — 인프라/검색 인덱스/운영

| 항목 | 변경 | 활성화 방법 |
|---|---|---|
| HNSW 벡터 인덱스 | alembic 0018 (ivfflat → HNSW m=16 ef_construction=64) | `alembic upgrade head` |
| MCP 호출 로깅 | `middleware/mcp_logging.py` + `/api/metrics/mcp` JSONL tail 집계 | 자동 / env `AIDH_MCP_LOG=0` 비활성 |
| GPU batch 자동 | `embedding.py` encode_many — CUDA 감지 시 batch=128 | 자동 / env `AIDH_EMBED_BATCH=N` 수동 |

### Wave-2 — 검색 품질 + LLM UX

| 항목 | 변경 | 활성화 |
|---|---|---|
| 하이브리드 검색 (RRF k=60) | `search_svc.hybrid_search` + MCP `agent_search(mode='hybrid')` | `mode='hybrid'` 호출 |
| `SearchMode` Literal enum | mcp_runtime 시그니처 강화 | 자동 — 클라이언트 자동완성/오타 차단 |
| recommend_agents 0건 fallback | catalog + sample_queries 반환, `fallback=true` | 자동 |
| MCP prompts 3종 | `aidh-onboard`, `aidh-find`, `aidh-cite` | Claude Desktop "/" 메뉴 자동 노출 |
| `get_record_sections(sections=[...])` | 부분 조회, default limit 50→10 | 인자 지정 시 발동 |

### Wave-3 — 인용 맥락 / 청크 / 재정렬

| 항목 | 변경 | 활성화 |
|---|---|---|
| section_path 컬럼 + ingest 계산 | alembic 0019 + `_flatten_sections` | 재적재 시 자동 채워짐 |
| figure_refs / table_refs 응답 노출 | search 응답에 비어있으면 키 생략 | 자동 |
| chunk window (큰 섹션 슬라이딩 분할) | alembic 0020 + ingest 환경 변수 게이트 | env `AIDH_CHUNK_WINDOW=on` 시 활성 |
| cross-encoder rerank opt-in | `services/rerank.py` — bge-reranker-v2-m3 | env `AIDH_RERANK_PROVIDER=bge_m3` |

### Wave-4 — 동적 도구 등록

| 항목 | 변경 |
|---|---|
| 사이드카 매니페스트 패턴 | `mcp_scripts/*.{sh,mcp.yaml}` |
| 부팅 시 자동 `@mcp.tool` 등록 | `mcp_scripts.py` + `mcp_runtime` 마운트 |
| 6종 보안 게이트 | base_dir prefix / 심볼릭링크 / shell=False / env_allowlist / timeout / 동시실행 semaphore |
| 첫 예제 도구 | `echo_args` (sanity), `fetch_rerank_model` (HF prefetch) |
| 환경 변수 게이트 | `AIDH_MCP_SCRIPTS=off`, `AIDH_MCP_SCRIPTS_DIR=/path`, `AIDH_MCP_SCRIPTS_CONCURRENCY=N` |

### Extension 0.16.0 — MCP 클라이언트 자동 등록 (11종)

`cline · cline_sr · roo_code · continue · windsurf · copilot · claude_desktop · claude_code · cursor · gemini · codex`

웹뷰 드롭다운 + `_mcpSpecFor` 코드 스니펫 + `installMcpConfig` 자동 등록 함수.
Continue 용 `mergeContinueYaml` 의존성 없는 narrow YAML 머지 헬퍼 도입.

### 누적 도구 수 (mcp_runtime list_tools)

총 **14개** = built-in 12 + 동적 스크립트 2 (echo_args, fetch_rerank_model).

---

## 3. Wave-5 — CLI binary → MCP tool 자동 등록 (다음 사이클)

### 3.1 동기

- 사용자가 임의의 Python/Node/.NET/Java/Win32 CLI 도구를 zip 으로 업로드하면 서버가 자동으로 Apptainer 컨테이너화 + 격리 실행 + MCP tool 등록.
- 윈도우즈 개발자가 sif/def 어휘 몰라도 OK — 서버가 떠안음.
- 도구의 출력은 옵션으로 `records` 테이블에 자동 적재 (`persist_output`) → 다음 검색의 근거.

### 3.2 결정된 정책 (사용자 확정)

| 정책 | 결정 |
|---|---|
| 같은 name 다른 sha 재업로드 | version 자동 +1, 이전 버전 archive 보존 (rollback 가능) |
| 출력 자동 적재 (`persist_output`) | Phase 1 에 같이 포함 — wave-5 의 sweet spot |
| 새 `data_type` enum 자동 확장 | 거절 — 기존 7종 (DOC/DATA/SIM/CAD/LOG/FORM/OTHER) 내에서만 선택 |
| Wine 라이선스 | LGPL v2.1+ — 사내 운영 무료 OK. 폐쇄망용 winetricks pre-cache 인프라 plan 포함 |

### 3.3 4 Phase 분할

| Phase | 범위 | 산출물 |
|---|---|---|
| **P1. 백엔드 파이프라인** | platform 감지 / def 자동 생성 / sif 빌드 / 캐시 / smoke / FastMCP 등록 / `mcp_uploads`+history 테이블 / `persist_output` | API + alembic + 통합 테스트 |
| **P2. Dashboard Upload UI** | drag-drop / 매니페스트 위저드 / job 진행상황 / 윈도우즈 친화 | static/dashboard 신규 탭 |
| **P3. Windows CLI 헬퍼** | `aidh-package.exe` — 윈도우즈에서 zip 자동 생성 + 업로드 | Go 또는 PyInstaller 빌드 |
| **P4. VSCode extension 0.17.0 통합** | `aidh.packageAndUpload` 명령 / 워크스페이스 build output 선택 | extension 갱신 |

### 3.4 보안·격리 (Phase 1 핵심)

| 게이트 | 적용 |
|---|---|
| wave-4 6종 게이트 | 그대로 적용 (env_allowlist, timeout, semaphore 등) |
| Apptainer 컨테이너 강제 | 모든 업로드 binary — 정적 ELF 도 컨테이너 |
| `--net=none --containall --readonly --writable-tmpfs --no-home` | 기본. 매니페스트 `platform_capability.net=true` 시만 net 해제 |
| cgroup v2 자원 제한 | CPU 200% / RAM 2GiB / 디스크 1GiB / pids 64 default |
| 업로드 인증 | C-4 미완 → 내부망 운영자 전용. 관리자 토큰 stub |
| 감사 로그 | `mcp_uploads_history` (uploader, sha256, smoke_result, registered_at, deregistered_at) |

### 3.5 폐쇄망 운영 보강

- `AIDH_WINE_CACHE_DIR=/var/lib/aidatahub/wine-cache/` — winetricks pre-cache 마운트
- apt mirror 에 winehq repo 추가 (운영자 1회 작업)
- HF 모델 prefetch (wave-4 의 `fetch_rerank_model` 패턴 활용)

---

## 4. Wave-5 사용자 여정 (end-to-end)

stress-strain 곡선 plot 도구를 예로:

1. **윈도우즈 개발자가 `stress_strain_plot.py` + `matplotlib` requirements 준비** (소스 그대로 OK, 빌드 불필요)
2. **manifest.yaml 작성** — dashboard 위저드 또는 직접 작성. `examples/wave-5/stress_strain_plot/manifest.yaml` 참조.
3. **samples/case_*.json 1~2개** — argv 매핑 검증용
4. **zip 으로 묶어 POST `/api/mcp_tools/upload`** — 또는 dashboard drag-drop
5. **job_id 폴링** — 서버가 platform 감지 → def 자동 생성 → sif 빌드 → smoke → 등록 (30s~5분)
6. **등록 완료 후 MCP 클라이언트 (Claude Desktop / Cursor 등) 에서 자연어로 호출**:
   - "SUS304 의 stress-strain 곡선 그려줘" → `recommend_agents` → `agent_search` → `stress_strain_plot` 호출 → PNG path + 메타데이터 반환
   - `persist_output.enabled=true` 이면 결과가 records 에 SIM 타입으로 자동 적재 → 다음 의미 검색에서 자연스럽게 재발견

상세 예제: `examples/wave-5/stress_strain_plot/`.

---

## 4.5. Wave-6 — MCP Federation / Proxy (요약)

상세: `docs/01-plan/wave-6-mcp-federation.md`

**한 줄 요약**: AIDataHub 가 다수의 외부 FastMCP 서버를 단일 진입점으로 프록시. 사용자는 AIDataHub 1개만 등록하면 사내 5~10개 MCP 서버의 모든 도구를 통합 사용.

**4 Phase 분할**

| Phase | 범위 |
|---|---|
| P1 핵심 proxy | HTTP transport / namespace (`alias__tool`) / `mcp_upstreams` + `mcp_proxy_calls` 테이블 / audit |
| P2 stdio + 다중 + 헬스체크 | subprocess MCP / 자동 비활성 + 복구 / 5~10 upstream 안정 |
| P3 Admin UI | dashboard 신규 탭 — upstream 추가/제거/모니터 |
| P4 RBAC + Rate limit | per-client 도구 권한 + per-upstream 한도 + C-4 인증 연계 |

**Wave-5 와 결합 시너지** — HQ AIDataHub 가 wave-5 로 등록한 도구를 지점 AIDataHub 가 wave-6 으로 자동 발견. 사실상 MCP-as-a-Service.

**진행 순서 권장**: wave-5 P1 + wave-6 P1 **병행** (파일 충돌 없음, 둘 다 백엔드 트랙).

---

## 5. 보류 항목 (C-4 인증/Origin 가드)

| 사유 | 사용자 명시: "보안 게이트 빼고 다 만들자" |
|---|---|
| 위험 | wave-5 가 임의 binary 실행 = RCE 인프라. 외부 노출 전 반드시 필요 |
| 계획 | wave-5 P1 완료 후 별 wave-6 으로 진행 권장. 내부망 운영자 전용 단계에서는 stub 으로 충분 |

---

## 6. 환경 변수 일람 (현행 + wave-5 예정)

| 변수 | 현황 | 의미 |
|---|---|---|
| `AIDH_MCP_LOG` | DONE | `0` 시 MCP 호출 로깅 비활성 |
| `AIDH_MCP_LOG_PATH` | DONE | JSONL 로그 경로 override |
| `AIDH_EMBED_BATCH` | DONE | sentence-transformers 배치 크기 수동 |
| `AIDH_CHUNK_WINDOW` | DONE | `on` 시 큰 섹션 sub-chunk 분할 |
| `AIDH_CHUNK_MAX_CHARS` / `AIDH_CHUNK_WIN_CHARS` / `AIDH_CHUNK_OVERLAP` | DONE | chunk 분할 파라미터 |
| `AIDH_RERANK_PROVIDER` | DONE | `bge_m3` 시 cross-encoder rerank |
| `AIDH_MCP_SCRIPTS` | DONE | `off` 시 동적 등록 비활성 |
| `AIDH_MCP_SCRIPTS_DIR` | DONE | 매니페스트 디렉토리 override |
| `AIDH_MCP_SCRIPTS_CONCURRENCY` | DONE | wave-4 동시 실행 상한 |
| `AIDH_MCP_UPLOADS_DIR` | wave-5 P1 | 업로드 zip / sif 캐시 위치 |
| `AIDH_BUILD_CONCURRENCY` | wave-5 P1 | 동시 apptainer build 상한 (default 2) |
| `AIDH_BUILD_TIMEOUT_SEC` | wave-5 P1 | 빌드 timeout (default 1800) |
| `AIDH_SIF_CACHE_QUOTA_GB` | wave-5 P1 | sif 캐시 총량 상한 (default 100) |
| `AIDH_WINE_CACHE_DIR` | wave-5 P1 | winetricks pre-cache 마운트 경로 |
| `AIDH_UPSTREAM_CONFIG` | wave-6 P1 | upstream MCP 매니페스트 yaml 경로 (default `config/upstream_mcps.yaml`) |
| `AIDH_UPSTREAM_PING_SEC` | wave-6 P1 | 헬스체크 주기 (default 60s) |
| `AIDH_UPSTREAM_CONN_POOL` | wave-6 P1 | upstream 당 connection pool 상한 (default 4) |
| `ANALYTICS_MCP_TOKEN` 등 | wave-6 운영 | upstream 별 인증 토큰 (env_var 명은 매니페스트가 지정) |

---

## 7. 다음 액션 (사용자 신호 대기)

- [ ] wave-5 P1 (도구 업로드 백엔드) + wave-6 P1 (federation 백엔드) **병행** — 파일 충돌 없음
- [ ] wave-5 / wave-6 의 P2~P4 는 P1 통과 후 순차
- [ ] (보류) C-4 인증/Origin 가드 — wave-6 P4 의 RBAC 와 연계해서 처리
