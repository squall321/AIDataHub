"""PowerPoint(.pptx) → JSON 변환기.

[json_schema_rules.md] v1.0 스키마 출력 (data_type=DOC, source_format=pptx).
[ppt_to_json_conversion_rules.md] 의 규칙을 따른다.

설계 핵심:
- 슬라이드 1장 = 섹션 1개 (level 1, 제목에 번호 패턴 있으면 level 2/3 까지 허용).
- 슬라이드 내 도형/플레이스홀더 등장 순서가 곧 reading order.
- 표·그림은 본문 ``blocks`` 흐름에 ``ref`` 로 삽입되고, 데이터는 최상위
  ``tables`` / ``figures`` / ``attachments`` 배열에 저장된다.
- 슬라이드 노트는 별도 ``[Speaker Notes]`` 마커 단락 뒤에 본문 흐름으로 추가.
"""
__version__ = "0.1.0"
