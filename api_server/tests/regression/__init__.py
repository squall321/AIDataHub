"""실제 변환기 산출 형태(realistic shape) 회귀 테스트.

각 변환기(Word/Excel/PPT/MD/PDF) 의 작은 가상 산출물을 in-memory 또는 tmp
파일로 생성한 뒤, ``ingest`` 파이프라인을 거쳐 DB 에 적재되고 API 로 조회되는
모든 단계를 한 번에 검증한다.

목적:
    - 변환기 → JSON → ingest → API 의 end-to-end 회귀 보호.
    - Migration 0006 백필 이후의 메타 컬럼 (``classification`` /
      ``capabilities`` 등) 이 실제 데이터로 정상 채워지는지 확인.
    - 다양한 ``data_type`` 이 한 DB 에 공존할 때 ``/api/discover`` /
      ``/api/ask`` / ``/api/records?data_type=...`` 가 일관성을 유지하는지 확인.
"""
