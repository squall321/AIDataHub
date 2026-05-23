# Wave-5 — CLI binary → MCP tool 자동 등록

작성일: 2026-05-23
선행: wave-4 (`mcp_scripts/` 사이드카 매니페스트 패턴, 6종 보안 게이트)
목표: 사용자가 임의의 CLI 도구 zip 을 업로드 → 서버가 Apptainer 컨테이너화 + smoke + FastMCP 등록 → MCP 클라이언트에서 즉시 호출 가능. **윈도우즈 개발자도 sif/def 어휘 몰라도 OK.**

---

## 1. 결정된 정책 (확정)

| 정책 | 결정 |
|---|---|
| Linux 도구도 컨테이너 강제? | **wave-5 업로드만 컨테이너 강제** (정적 ELF 도). wave-4 (운영자 작성 .sh) 는 호스트 직접 유지 |
| 같은 name 다른 sha 재업로드 | **version 자동 +1, 이전 archive 보존** (rollback 가능) |
| `persist_output` (출력 → records 자동 적재) | **Phase 1 에 포함** |
| 새 `data_type` enum 자동 확장 | **거절** — 기존 7종 안에서만 선택 |
| Wine 라이선스 | LGPL v2.1+, 사내 무료. 폐쇄망용 winetricks pre-cache 인프라 plan 포함 |
| 인증 게이트 (C-4) | 보류 — 내부망 운영자 전용 단계까지만 |

---

## 2. 4 Phase 분할

### Phase 1 — 백엔드 파이프라인 (우선순위 최고)

**산출물**

- `api/services/mcp_upload_svc.py` — 업로드 처리 파이프라인
- `api/services/apptainer_build_svc.py` — def 자동 생성 + 빌드 + 캐시
- `api/routes/mcp_tools.py` — `POST /api/mcp_tools/upload` (multipart) + `GET /api/mcp_tools/jobs/{job_id}`
- alembic 0021 — `mcp_uploads` + `mcp_uploads_history` 테이블
- alembic 0022 — `records.tool_run_id` (선택) — `persist_output` 으로 적재된 record 추적
- `api/mcp_runtime.py` — wave-4 의 `register_all_scripts` 옆에 `register_all_uploads` 추가
- `tests/test_mcp_upload.py` — 단위 + 통합

**파이프라인 단계**

```
1. unzip + sha256 → 캐시 hit 검사
2. platform_detect — file/magic/shebang
3. dep_analyze — ldd / requirements.txt / package.json / .csproj — runtime 별
   default 버전: Python 3.12, Node 20, JDK 17, .NET 8 (매니페스트 미지정 시)
4. runtime_decide — python / node / jar / dotnet / linux_native / wine
5. def_gen — Apptainer .def 자동 작성 (base image + apt + COPY)
6. apptainer build — fakeroot 모드, /var/lib/aidatahub/sif/<sha>.sif
7. smoke_run — samples/*.json 각각 apptainer exec, 격리 옵션 강제
8. manifest_emit — mcp_scripts/_uploads/<sha>/manifest.mcp.yaml
9. fastmcp_reload — wave-4 인프라 재사용
10. mcp_uploads_history INSERT
```

**스키마 (alembic 0021)**

```sql
CREATE TABLE mcp_uploads (
  name TEXT PRIMARY KEY,
  current_sha CHAR(64) NOT NULL,
  current_version INT NOT NULL DEFAULT 1,
  registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  registered_by TEXT,
  manifest JSONB NOT NULL,         -- 정규화된 매니페스트
  capabilities JSONB DEFAULT '{}', -- {net, gpu, persist_output...}
  archived_versions JSONB DEFAULT '[]'
);

CREATE TABLE mcp_uploads_history (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  sha CHAR(64) NOT NULL,
  version INT NOT NULL,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  uploaded_by TEXT,
  smoke_result JSONB,
  build_log_path TEXT,
  sif_path TEXT,
  registered BOOLEAN NOT NULL DEFAULT false,
  archived_at TIMESTAMPTZ
);
CREATE INDEX idx_mcp_uploads_history_name ON mcp_uploads_history (name, version DESC);
```

**API**

```
POST /api/mcp_tools/upload  (multipart)
  fields:
    bundle: <zip file>
    metadata: <JSON> { uploader, dry_run? }
  response:
    202 { job_id, status: "queued" }
    409 { error: "name conflict, no version bump (identical sha)" }

GET /api/mcp_tools/jobs/{job_id}
  response:
    { job_id, status: queued|building|smoke_running|registered|failed,
      step, log_tail, elapsed_sec, error? }

GET /api/mcp_tools/{name}
  response:
    { name, current_version, current_sha, manifest, capabilities,
      registered_at, archived_versions: [{version, sha, archived_at}] }

DELETE /api/mcp_tools/{name}
  response:
    204 (도구 비활성화 + sif 보존)
```

**보안 — Apptainer 격리 옵션 매트릭스**

| 항목 | default | platform_capability 로 해제 |
|---|---|---|
| `--net=none` | ON | `net: true` 로 OFF |
| `--containall` | ON | (해제 불가) |
| `--readonly` | ON | (해제 불가, /work, /tmp 는 별도) |
| `--writable-tmpfs` | ON | tmpfs 용량 limit 적용 |
| `--no-home` | ON | (해제 불가) |
| GPU CDI 마운트 | OFF | `gpu: true` 로 ON |
| cgroup CPU / RAM / 디스크 / pids | resource_limits 매니페스트 따름 (default 200% / 2GiB / 1GiB / 64) | — |

**`persist_output` 처리 흐름**

```
tool 호출 → stdout 캡쳐 → 매니페스트 persist_output.enabled?
  if true:
    record_id = next_seq(data_type, team, group)
    content = build_record_content(persist_output.template, args, stdout, parsed_json)
    records INSERT with embedding
    response 에 { stored_record_id } 추가
```

`persist_output.template` 은 매니페스트가 정의한 record content 형판 — 도구 실행 결과를 `{args}` `{stdout}` `{parsed.x}` placeholder 로 채워 record 본문 구성.

**테스트 케이스 (Phase 1 완료 기준)**

- 매니페스트 partial 업로드 + smoke 통과 → 등록 성공
- 같은 zip 재업로드 → 캐시 hit, 무 빌드
- 같은 name 다른 sha → version+1, 이전 archived
- 빌드 실패 (apt missing) → failed 상태 + auto_def 첨부
- smoke 실패 (exit≠0) → 등록 거절 + 진단 메시지
- `--net=none` 강제 → 도구가 외부 호출 시도 시 차단
- timeout 강제 → 30분 초과 빌드 kill
- cgroup RAM 초과 → OOM kill + 진단

### Phase 2 — Dashboard Upload UI

- 신규 탭 `static/dashboard/upload.html` (or 통합 SPA 의 새 view)
- drag-drop zone — 파일 종류 자동 감지
- 매니페스트 위저드 폼 (필드별 hint, 예제 placeholder)
- samples JSON 편집기 (Monaco 또는 textarea 기반)
- job 진행상황 polling + 빌드 로그 tail 표시
- 등록 성공 시 "Try it" 버튼 → MCP recommend_agents 로 도구 시범 호출

### Phase 3 — Windows CLI 헬퍼 (`aidh-package.exe`)

- Go 또는 Python (PyInstaller) 빌드 — 단일 .exe
- `aidh-package my_tool.py --manifest-wizard` — 대화형
- `aidh-package my_tool.py --manifest manifest.yaml --upload` — CI 친화
- 배포: `/downloads/aidh-package-win-x64.exe` (extension publish 인프라 재사용)

### Phase 4 — VSCode extension 0.17.0 통합

- 신규 명령 `aidh.packageAndUpload`
- 워크스페이스 build output 자동 탐지 (dist/, build/, target/, bin/)
- 매니페스트 위저드 (extension webview)
- 업로드 진행 상황을 webview 에 실시간 표시
- 등록 성공 시 자동으로 MCP 클라이언트 (cline/cursor/...) 에 알림

---

## 3. 폐쇄망 운영 고려사항

| 이슈 | 해결 |
|---|---|
| Wine 패키지 설치 | apt mirror 에 winehq repo 추가, 또는 deb 사전 다운로드 |
| winetricks 외부 다운로드 (vcrun*) | 운영자 1회 외부망에서 `winetricks vcrun2019 dotnet48 ...` 실행 → tar 압축 → 서버 `/var/lib/aidatahub/wine-cache/` 로 복사 → 빌드 시 `--bind` 마운트 |
| .NET Framework 4.x 대체 | mono (LGPL/MIT) 권장 — 호환성 다소 낮지만 폐쇄망 친화 |
| HF 모델 prefetch | wave-4 `fetch_rerank_model` 패턴 활용 |

---

## 4. 위험 / 미해결

| 위험 | 완화 |
|---|---|
| 임의 binary 실행 = RCE | Apptainer 격리 + cgroup + net none + 업로드 인증 (내부망 운영자) |
| Wine 호환성 binary 별 가변 | smoke 실패 시 진단 메시지 + 매뉴얼 def 옵션 (mode B) |
| 자동 빌드 분 단위 시간 | async API + 캐시 + 동시 빌드 상한 2 |
| sif 디스크 폭주 | LRU evict + 100GB quota |
| persist_output 폭주 (도구 호출마다 record 누적) | 매니페스트에 sample_rate, dedup_key 옵션 추가 (Phase 1 확장) |

---

## 5. Pre-flight Validation (build 전 빠른 거절)

비용 비싼 apptainer build 진입 전, 1~2초 안에 끝나는 정적 검사로 명백히
잘못된 업로드를 차단한다. 모두 단일 트랜잭션 안에서 수행.

| 검사 | 차단 조건 | 에러 코드 |
|---|---|---|
| 매니페스트 JSON Schema | required 필드 누락, type 불일치, name regex 위반 | `INVALID_MANIFEST` |
| Name conflict | 이미 등록된 name + 동일 sha (idempotent re-upload OK / 다른 sha 면 version bump 의도로 통과) | `NAME_TAKEN` (sha 동일) |
| Reserved name | wave-4 도구 (`echo_args`, `fetch_rerank_model`) 또는 built-in (`discover`, `agent_search` 등) 이름 | `RESERVED_NAME` |
| Sample 형식 | `args` 키가 매니페스트 `args` 와 매핑 가능한지, `expected_exit` 가 정수인지 | `INVALID_SAMPLE` |
| Architecture 일치 | 매니페스트 `runtime=binary` 인데 ELF 가 아님, `runtime=wine` 인데 PE32 아님 | `ARCH_MISMATCH` |
| Sample 최소 1개 | smoke 검증 불가 | `NO_SAMPLES` |
| Zip 크기 상한 | 100MB (sif 본체는 별도 — 이건 소스 zip) | `BUNDLE_TOO_LARGE` |
| Uploader 식별 | metadata.uploader 누락 (감사 추적 불가) | `MISSING_UPLOADER` |

거절 시 즉시 400 응답 + 에러 코드 + 사람이 읽을 진단 메시지. 빌드 큐에 안 들어감.

## 6. persist_output 템플릿 엔진

`title_template` / `summary_template` / `body_template` 에서 사용 가능한 placeholder:

| 형식 | 의미 | 예시 |
|---|---|---|
| `{tool_name}` | 매니페스트 name | `stress_strain_plot` |
| `{tool_version}` | 등록 버전 | `2` |
| `{timestamp}` | 호출 UTC ISO | `2026-05-23T14:23:10Z` |
| `{request_id}` | MCP 호출 트레이스 ID | `f4a8c2...` |
| `{uploader}` | 도구 등록자 식별자 | `alice@example.com` |
| `{args.X}` | 호출 인자 X 의 값 | `{args.material_name}` → `SUS304` |
| `{parsed.Y}` | stdout JSON parse 후 Y 키 (return.format=json 일 때만) | `{parsed.yield_strain}` → `0.001075` |
| `{exit_code}` | 자식 exit code | `0` |

**Escaping**:
- placeholder 가 아닌 `{` `}` 는 `{{` `}}` 로 escape
- 평가 실패 (키 부재 등) 시 placeholder 를 빈 문자열로 치환하고 audit_log 에 warning

**적재 실패 처리** (중요):
- tool 호출 자체는 stdout 반환에 성공했지만 records INSERT 가 실패한 경우 (디스크 풀, embedder 다운 등) → **tool 호출 응답은 유지** (LLM 에게 정상 반환), 응답에 `persist_failed: true` + reason 만 추가
- 다음 호출 시 자동 재시도 안 함 (멱등성 깨짐 방지)
- 운영자는 `mcp_uploads_history` 의 `persist_failures` 카운터로 추적

**Dedup (옵션)**:

```yaml
persist_output:
  dedup_key: "{args.material_name}_{args.e_modulus}_{args.yield_stress}"
```

같은 dedup_key 의 호출은 신규 record 대신 기존 record 의 `updated_at` 만 갱신.
같은 입력으로 100번 호출해도 records 1행만 — 폭주 방지.

## 7. Tool lifecycle (deprecation / rollback)

| API | 동작 |
|---|---|
| `POST /api/mcp_tools/{name}/deprecate` | FastMCP 에서 tool 제거 (list_tools 응답에서 사라짐). sif/manifest 보존. `mcp_uploads.deprecated_at` 갱신 |
| `POST /api/mcp_tools/{name}/restore` | deprecated 도구 재활성 (FastMCP 재등록) |
| `POST /api/mcp_tools/{name}/rollback?to_version=N` | 이전 version 의 sif 로 swap. archived_versions 에서 N 의 sha + sif 찾아 활성. 현재 버전은 archived 로 이동 |
| `DELETE /api/mcp_tools/{name}` | 완전 삭제 — sif 파일 삭제 + history 행 보존 (감사). undo 불가. 관리자 토큰 + 확인 phrase 필수 |

**자동 비활성 트리거** (Phase 1 끝나고 별 단계):
- 24h 동안 호출 실패율 50% 초과 → 자동 deprecate + 운영자 알림
- smoke 회귀 실패 (월간 정기 재실행) → 자동 deprecate

## 8. Sample 합성 fallback (선택)

사용자가 samples 미제공 OR 1개만 제공 시:

- **옵션 A — 거절**: smoke 검증 불가 → upload 거부. default.
- **옵션 B — LLM 합성**: 매니페스트 `auto_synth_samples: true` + 도구 description + args 스키마로 LLM 이 sample 3~5개 생성. 첫 호출 sample 은 일반적 케이스, 마지막은 edge case (null/0/큰값 등). 위험: LLM 환각 가능 → smoke 통과율 떨어짐. 옵션-인 only.
- **옵션 C — 사용자 도구 호출**: 업로드 zip 에 `synth_samples.sh` 같이 포함 → 빌드 후 컨테이너 안에서 sample 생성 → smoke 검증. 도구가 self-test 가능한 경우만.

기본은 옵션 A. wave-5 Phase 1 은 옵션 A 만 구현.

## 9. Cold start 추정 (runtime 별)

sif 캐시 hit + apptainer 컨테이너 cold-start 기준:

| Runtime | Cold start (sif warm) | 첫 호출 latency 추가 요인 |
|---|---|---|
| Python 3.12 (no native deps) | 200~400ms | python interpreter 시작 |
| Python (numpy/pandas/matplotlib) | 500~1000ms | C extension 로드 |
| Python (torch/cuda 적재) | 2~5s | CUDA 컨텍스트 초기화 |
| Node 20 | 150~300ms | V8 워밍 |
| Java 17 (small jar) | 800ms~1.5s | JVM 시작 + JIT 워밍 |
| .NET 8 self-contained | 200~500ms | runtime 동적 로드 |
| Linux native ELF (static) | 50~150ms | apptainer 컨테이너 셋업만 |
| Wine (vcrun2019 부착) | 800ms~2s | Wine prefix + DLL 로드 |

LLM 호출 1회 (RAG + tool call) 가 보통 2~10s 이므로 위 추가 latency 는 5~20% 오버헤드. 운영상 허용 범위.

## 10. Agent ↔ Tool 연결 정책

업로드된 wave-5 도구의 노출 범위 결정:

| 매니페스트 필드 | 동작 |
|---|---|
| (기본 — 미지정) | 모든 agent 에 노출. `recommend_agents` 가 도구 사용 가능성 평가 시 자동 포함 |
| `restrict_agents: [agent_type, ...]` | 지정된 agent 만 호출 가능. 다른 agent context 에서는 list_tools 응답에서 숨김 |
| `require_agent_tag: [tag, ...]` | agent 의 `common_tags` 가 모든 태그 포함 시만 노출 |

**recommend_agents 의 tool-aware ranking** (Phase 1 추가):
- 자연어 쿼리 임베딩과 도구 description 임베딩 cosine 유사도 계산
- 상위 K 도구를 `recommend_agents` 응답의 `relevant_tools` 필드로 동봉
- LLM 이 agent 선택 후 직접 도구 호출 가능

## 11. Build worker 격리

API 프로세스가 직접 `apptainer build` 를 호출하면 — 분 단위 IO/CPU 점유 → API 응답 지연. 별 worker 로 분리.

| 컴포넌트 | 책임 |
|---|---|
| API 프로세스 (uvicorn) | upload 받기, pre-flight 검증, 큐에 job INSERT, job 조회 |
| `aidh-builder.service` (systemd unit, 별 프로세스) | 큐 폴링, apptainer build 실행, smoke run, FastMCP 에 신호 (HUP 또는 SIGUSR1) → API 가 재등록 |
| 큐 구현 | PostgreSQL `SELECT FOR UPDATE SKIP LOCKED` 패턴 (간단, 추가 의존성 없음). 또는 LISTEN/NOTIFY |
| 동시성 | worker 인스턴스 N개 (default 2), 각각 1 빌드씩. `AIDH_BUILD_CONCURRENCY` env 로 조정 |

apptainer build 자체가 `--fakeroot` (MXWhitePaper 패턴 — 운영 정책상 setuid 없음). build 디렉토리는 worker 의 `$XDG_RUNTIME_DIR/aidh-build-<sha>/`, 완료 후 cleanup.

## 12. Migration / Coexistence — wave-4 vs wave-5

| 카테고리 | 디렉토리 | 권한원 | 등록 방식 | 보안 |
|---|---|---|---|---|
| **wave-4 (운영자 작성)** | `mcp_scripts/` (git 관리) | git PR 머지 | 부팅 시 자동 scan | 6종 게이트 + 호스트 직접 실행 |
| **wave-5 (외부 업로드)** | `mcp_uploads/_uploads/<sha>/` (runtime 생성) | HTTP 업로드 + uploader 식별 | upload 완료 후 동적 add_tool | 6종 + Apptainer 컨테이너 강제 + cgroup |

**공존 규칙**:
- list_tools 응답에 양쪽 다 노출, **name 충돌 시 wave-4 우선** (reserved name 검사로 사전 차단)
- 운영자가 wave-5 업로드를 wave-4 로 승격하고 싶으면: sif 안의 스크립트 추출 → `mcp_scripts/` 에 .sh + .mcp.yaml 작성 → PR
- 반대 방향 (wave-4 → wave-5) 은 의미 없음 (이미 git 관리되는 도구를 굳이 컨테이너화 X)

## 13. 에러 메시지 카탈로그

빌드/실행 실패 시 사용자가 받는 진단 메시지의 표준 양식. 한국어 + 액션 제안 한 줄.

| 코드 | 의미 | 사용자 액션 |
|---|---|---|
| `LDD_MISSING_LIB` | 동적 의존 lib 검출 실패 (예: libcudnn8) | `dep_hint: [libcudnn8-dev]` 매니페스트에 추가, 또는 custom.def 업로드 |
| `APT_INSTALL_FAIL` | def 의 apt-get install 실패 (패키지명 오류, repo 미설정) | 패키지명 확인 또는 폐쇄망 mirror 설정 점검 |
| `WINE_MISSING_DLL` | Wine 실행 시 DLL 없음 | `winetricks: [vcrun2019]` 추가, 또는 .NET self-contained 로 재빌드 권장 |
| `WINE_GUI_REQUIRED` | Wine 도구가 DirectX/GUI 사용 시도 | wave-5 비대상 — 콘솔 전용 도구로 리빌드 |
| `SMOKE_EXIT_MISMATCH` | sample.expected_exit 와 실 exit 불일치 | sample 수정 또는 도구 로직 점검 |
| `SMOKE_STDOUT_MISSING` | sample.expected_stdout_contains 가 stdout 에 없음 | 출력 형식 변경 여부 확인 |
| `BUILD_TIMEOUT` | apptainer build 가 30분 초과 | 큰 의존성은 base image 재선정, 또는 custom.def 로 단순화 |
| `BUILD_DISK_FULL` | sif 캐시 quota 초과 | 운영자 알림 — LRU evict 자동 실행되지만 대형 도구는 매뉴얼 정리 |
| `OOM_KILL` | smoke 중 RAM 초과 | `resource_limits.ram_gib` 상향 또는 도구 메모리 최적화 |
| `NETWORK_REQUIRED` | 도구가 외부 호출 시도 (--net=none) | `platform_capability.net: true` 명시 — 단 보안 감사 대상 |

모든 에러 응답에 `code`, `message_ko`, `suggested_action`, `build_log_tail` (마지막 50줄) 포함.

## 14. 수락 테스트 (목표 수치)

Phase 1 통과 기준:

| 항목 | 목표 |
|---|---|
| 업로드~등록 latency (Python 경량) | < 60s (cache miss) / < 2s (cache hit) |
| 업로드~등록 latency (.NET self-contained) | < 90s |
| 업로드~등록 latency (Wine + winetricks) | < 5분 |
| Smoke 통과율 (등급 A 도구 = Python/.NET/Java/static ELF) | 90% 이상 |
| Smoke 통과율 (등급 B = dynamic ELF, .NET Framework wine) | 70% 이상 |
| 도구 호출 cold start (sif warm) | < 1s (Python/Node), < 2s (Java) |
| sif 캐시 hit rate (동일 sha 재업로드) | 99%+ (cache 즉시 응답) |
| 동시 빌드 처리량 | 2 builds parallel default, 큐 대기 |
| persist_output INSERT 성공률 | 99%+ (디스크/embedder 정상 시) |

## 15. 글로사리 (비전공 사용자용)

| 용어 | 1줄 설명 |
|---|---|
| MCP (Model Context Protocol) | LLM 이 외부 도구를 표준 방식으로 호출하는 프로토콜 (Anthropic 표준) |
| Apptainer | HPC 친화 컨테이너 런타임. Docker 와 유사하나 root 권한 없이도 동작 |
| sif | Singularity/Apptainer 의 컨테이너 이미지 파일 (단일 파일) |
| def | Apptainer 컨테이너 정의 파일 (base image + 설치 명령 등) |
| Wine | Windows 응용 프로그램을 Linux 에서 실행하는 호환성 레이어 (LGPL) |
| ldd | Linux 실행 파일이 의존하는 동적 라이브러리 목록을 출력 |
| RRF (Reciprocal Rank Fusion) | 여러 랭킹 결과를 결합하는 알고리즘 (wave-2 의 하이브리드 검색) |
| persist_output | wave-5 의 옵션. 도구 호출 결과를 자동으로 records 에 적재 |
| smoke run | 등록 직전 실제 호출 가능 여부 빠르게 점검하는 단계 |
| version bump | 같은 도구 name 의 새 빌드 업로드 시 version 자동 +1 |

## 16. 완료 정의 (DoD)

**Phase 1** 가 다음을 만족할 때 완료:

- [ ] 예제 `stress_strain_plot` (Python + matplotlib) 자동 등록 성공
- [ ] sha 캐시 hit 검증 (재업로드 무 빌드)
- [ ] version bump + archive 검증 (다른 sha 재업로드)
- [ ] persist_output 활성 시 records 에 SIM 타입 행 자동 INSERT 확인
- [ ] MCP 클라이언트 (Claude Desktop / Cursor) 에서 자연어로 도구 호출 성공
- [ ] 단위 테스트 + 통합 테스트 통과
- [ ] 빌드 실패 진단 메시지가 사용자가 이해 가능한 수준
