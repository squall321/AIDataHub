# Gap Features Roadmap — SaaS 풀스택 vs 자체구축 비교에서 도출된 잔여 기능

작성일: 2026-05-27
근거: [docs/04-report/saas-vs-selfbuild-1000users.csv](../04-report/saas-vs-selfbuild-1000users.csv) — 1,000명 5년 TCO 비교에서 외부 SaaS 가 제공하지만 우리 시스템이 부분/미보유한 기능 정리
상태: **수착 X** — 당장 진행 안함. 차후 결재·도입 결정 시점에 다시 꺼내서 우선순위 재평가용.
영향: 정리된 7개 항목 모두 완성 시 외부 SaaS 풀스택 66억 대체 가능 (자체 21억).

---

## 1. 잔여 기능 7 카테고리 — 우선순위 순

| 순위 | 카테고리 | 현재 보유율 | 필요 작업 | 분량 | 비고 / 의존성 |
|---|---|---|---|---|---|
| 1 | **사내 LLM 서빙** (Llama 3.3 70B + vLLM) | 0% | DGX HGX 4×H100 GPU 서버 구축 + vLLM 안정화 + 라우터 (사내 80% / 외부 20%) | 2~3개월 + HW 3억 | 외부 API 의존 0, Copilot 30억 + Claude API 12억 대체. 다른 항목의 근간. |
| 2 | **거버넌스** (RBAC + C-4 인증 + 감사) | 30% | Wave-6 P4 RBAC + 인증 게이트 + ISO 27001 대응 | 4~6주 | 다부서·다국가 확산 결재의 gate. 영업비밀 보호 필수. |
| 3 | **Wave-6 P2 stdio transport** | 0% (P1 HTTP만 완료) | 외부 stdio MCP 실행 + subprocess 풀 + 헬스체크 worker | 2~3주 | Slack/Confluence/GitHub/Jira MCP 자동 통합. 사내 협업 데이터까지 단일 검색. |
| 4 | **운영 가시성** (Loki + AlertManager + OpenTelemetry) | 70% (Prometheus 만 보유) | Grafana 대시보드 + Loki 로그 집계 + AlertManager 알람 + OpenTelemetry 분산 트레이싱 | 2주 | 24/7 운영 안정성. 모두 오픈소스 무료, 사내 표준과 일치. |
| 5 | **LangSmith 류 기능** (prompt eval / replay / A-B) | 60% (trace + history 만) | prompt eval UI + replay framework + dataset 관리 + A-B 테스트 | 3~4주 | 신모델 도입 평가 자동화. Llama→Llama4 또는 Claude5 전환 시 필수. |
| 6 | **Wave-5 P3 Windows CLI** (`aidh-package.exe`) | 0% | Python PyInstaller 또는 Go 단일 .exe + 매니페스트 위저드 + 자동 zip + upload | 1~2주 | 사내 윈도우즈 개발자 진입장벽 ↓. Wave-5 P2 Dashboard 후속. |
| 7 | **검색 로그 시각화** (Kibana 대체) | 90% (검색은 동작, 시각화만 부재) | Grafana 검색 대시보드 (쿼리수 / latency / hit rate / 인기 query) | 1주 | 운영팀 가시성. 4번 도입 이후 자연 추가. |

**합산 분량**: 약 5~6개월 (1.5인 풀타임 기준) — 모든 항목 완성 시 SaaS 풀스택 100% 대체.

---

## 2. 의존성 그래프

```
                                  ┌─ [1 LLM 서빙]      ─┐
                                  │   ↓                  │
[6 Windows CLI]   [4 운영 가시성]  │   2 거버넌스          │
       │                  │       │     ↓                │
       └─[5 LangSmith류] ─┤       │   [3 federation]     │
                          │       │     ↓                │
                          └─→ [7 Kibana 대시보드] ←─────┘
```

핵심 분기점:
- **#1 LLM 서빙** 이 다른 모든 항목의 backbone — 가장 먼저 또는 별도 트랙
- **#2 거버넌스** 는 다부서·다국가 확산 시점에 필수 (도입 전 아님)
- **#4 운영 가시성** 은 다른 항목과 거의 독립 — 언제든 도입 가능 (가장 ROI 빠름)

---

## 3. 트리거 조건 — 각 항목을 언제 다시 꺼낼까

| 항목 | 다시 꺼낼 시점 |
|---|---|
| 1 LLM 서빙 | 외부 LLM API 청구액이 월 1,000만원 초과 / 또는 보안 감사 통과 필요 / 또는 다부서 확산 결재 시점 |
| 2 거버넌스 | 사내 정식 도입 결재 / 또는 영업비밀 관련 부서 (RF/회로/공정) 도입 요청 / 또는 다국가 확산 |
| 3 Wave-6 P2 | 사내 Slack/Confluence/GitHub 통합 검색 요청 / 또는 외부 SaaS MCP 도입 결정 |
| 4 운영 가시성 | 운영 부담 호소 / 또는 24/7 운영 결재 / 또는 무료 오픈소스라 언제든 |
| 5 LangSmith류 | Llama 70B → 다음 모델 전환 / 또는 사내 모델 fine-tune 시작 / 또는 응답 품질 회귀 발견 |
| 6 Windows CLI | 윈도우즈 개발자 진입장벽 호소 / 또는 사내 Dashboard 이용률 정체 |
| 7 Kibana 대체 | 4번 도입 후 자연 follow-up / 또는 검색 품질 분석 요청 |

---

## 4. 외부 SaaS 결제 vs 자체 구현 — 항목별 손익분기

이 표는 "어느 항목부터 자체 구현 ROI 가 가장 빨리 나오나" 를 보여줌:

| 카테고리 | 외부 SaaS 1년 비용 | 자체 구현 일회성 비용 | 손익분기 |
|---|---|---|---|
| 1 LLM (API → 사내 서빙) | 2.4억 | 6.5억 (5년 분할) | 2.7년 |
| 2 거버넌스 | (간접 — 컴플라이언스 미달 시 도입 차단) | 0.5억 | N/A (필수) |
| 3 federation | 0 (Slack 이미 구독) | 0.1억 | 즉시 |
| 4 운영 가시성 | 0.66억 | 0.1억 | 2개월 |
| 5 LangSmith류 | 1.6억 | 0.3억 | 2.3개월 |
| 6 Windows CLI | (간접 — 사용성) | 0.15억 | N/A |
| 7 Kibana 대체 | 0.8억 | 0.05억 | 1개월 |

→ **#4, #7 이 가장 빠른 ROI** — 무료 오픈소스 + 작은 작업량. 운영 부담 표면화될 때 즉시 도입 가능.

---

## 5. 이 문서의 사용법

1. **현재 (2026-05-27)**: 진행 안함. 다른 우선순위에 집중.
2. **차후 결재 시**: 위 표를 발췌해 "이걸 다 해야 enterprise grade 가 됩니다" 로 활용
3. **새 요구사항 발생 시**: 위 7개 중 매핑되는 항목 찾아 우선순위 재평가
4. **분기별 검토**: 트리거 조건 (3절) 충족 여부 점검

---

## 6. 참고 — 관련 문서

- [docs/04-report/saas-vs-selfbuild-1000users.csv](../04-report/saas-vs-selfbuild-1000users.csv) — 이 로드맵의 출처 데이터
- [docs/01-plan/MASTER-PLAN.md](MASTER-PLAN.md) — 전체 wave 로드맵
- [docs/01-plan/wave-5-binary-mcp.md](wave-5-binary-mcp.md) — Wave-5 P3 (Windows CLI) 상세
- [docs/01-plan/wave-6-mcp-federation.md](wave-6-mcp-federation.md) — Wave-6 P2~P4 상세
- [docs/01-plan/wave-7-agent-tool-integration.md](wave-7-agent-tool-integration.md) — Wave-7 완료 (참고)
