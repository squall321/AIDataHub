"""PPT 변환기 모델 — Word 변환기 공용 모델을 그대로 재사용한다.

PPT 와 Word 의 출력 JSON 스키마는 동일하므로 별도 모델을 정의하지 않고
``converter.models`` 의 데이터클래스를 import 하여 노출한다.
"""
from converter.models import (
    Attachment,
    Block,
    ConversionResult,
    Figure,
    Section,
    Source,
    Table,
)

__all__ = [
    "Attachment",
    "Block",
    "ConversionResult",
    "Figure",
    "Section",
    "Source",
    "Table",
]
