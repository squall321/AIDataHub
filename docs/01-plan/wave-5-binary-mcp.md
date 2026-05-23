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
3. dep_analyze — ldd / requirements.txt / package.json
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

## 5. 완료 정의 (DoD)

**Phase 1** 가 다음을 만족할 때 완료:

- [ ] 예제 `stress_strain_plot` (Python + matplotlib) 자동 등록 성공
- [ ] sha 캐시 hit 검증 (재업로드 무 빌드)
- [ ] version bump + archive 검증 (다른 sha 재업로드)
- [ ] persist_output 활성 시 records 에 SIM 타입 행 자동 INSERT 확인
- [ ] MCP 클라이언트 (Claude Desktop / Cursor) 에서 자연어로 도구 호출 성공
- [ ] 단위 테스트 + 통합 테스트 통과
- [ ] 빌드 실패 진단 메시지가 사용자가 이해 가능한 수준
