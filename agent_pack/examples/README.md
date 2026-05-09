# Examples

| 파일 | 언어 | 용도 |
|---|---|---|
| [`python_client.py`](./python_client.py) | Python (stdlib only) | 메인 클라이언트 라이브러리 + 자가 진단 |
| [`curl_smoke.sh`](./curl_smoke.sh) | bash | 8개 핵심 endpoint smoke test |
| [`ts_client.ts`](./ts_client.ts) | TypeScript (browser/Node) | fetch 기반 클라이언트 |
| [`discover_walkthrough.py`](./discover_walkthrough.py) | Python | 4단계 시스템 발견 시퀀스 |
| [`group_extraction.py`](./group_extraction.py) | Python | 그룹/체크리스트 발췌 패턴 |
| [`ingest_record.py`](./ingest_record.py) | Python | 새 문서 적재 (multipart) |

## 빠른 시작

```bash
cd agent_pack/examples

# 1) 셸로 즉시 검증
bash curl_smoke.sh

# 2) Python 자가 진단
python python_client.py

# 3) 검색 한 번
python python_client.py search "KooRemapper"

# 4) 시스템 발견 시퀀스
python discover_walkthrough.py

# 5) 그룹 발췌 데모
python group_extraction.py

# 6) 새 문서 적재
python ingest_record.py /path/to/doc.docx \
  --division HE --team CAE --year 2026 --seq 7 \
  --agents iga-analyst --tags KooRemapper,IGA
```

모든 예제는 **API URL `http://110.15.177.125:8000` 가 하드코딩**되어 있다. 변경 시 [`../CONFIG.md`](../CONFIG.md) 참조.

## 의존성

Python 예제: stdlib only (Python 3.10+).
TypeScript 예제: 브라우저 fetch 또는 Node 18+ (내장 fetch).
bash 예제: bash + curl.

추가 라이브러리 설치 불필요.
