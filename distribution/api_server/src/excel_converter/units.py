"""Header → (label, unit) 분리 유틸.

사용 예::

    parse_header_units("하중(N)")        -> ("하중", "N")
    parse_header_units("Stress [MPa]")    -> ("Stress", "MPa")
    parse_header_units("온도 (deg C)")    -> ("온도", "deg C")
    parse_header_units("count")           -> ("count", None)
    parse_header_units("")                -> ("", None)

규칙
- 마지막에 등장하는 (...) 또는 [...] 의 내용을 단위로 추출.
- 단위로 추출되는 토큰 길이는 1~16자 사이여야 한다 (너무 긴 괄호는 설명문으로 간주, 단위가 아님).
- 헤더가 None 이거나 비어 있으면 ("", None) 을 돌려준다.
"""
from __future__ import annotations

import re
from typing import Optional

# (...) 또는 [...] 형태의 마지막 그룹.
_UNIT_PATTERN = re.compile(r"^(?P<label>.*?)\s*[\(\[](?P<unit>[^\(\)\[\]]+)[\)\]]\s*$")


def parse_header_units(header: object) -> tuple[str, Optional[str]]:
    """헤더 문자열을 (label, unit) 으로 분리한다.

    매개변수:
        header: 임의 객체. 문자열이 아니면 str() 로 캐스팅.

    반환:
        (label, unit) 튜플. unit 이 없으면 None.
    """
    if header is None:
        return ("", None)

    text = str(header).strip()
    if not text:
        return ("", None)

    m = _UNIT_PATTERN.match(text)
    if not m:
        return (text, None)

    label = m.group("label").strip()
    unit = m.group("unit").strip()

    # 단위 길이 휴리스틱: 단위 후보가 너무 길면 단위 아님 (설명문일 확률).
    if not unit or len(unit) > 16:
        return (text, None)

    # label 이 비면 (단위만 있는 헤더) 원본을 라벨로 보존.
    if not label:
        return (text, None)

    return (label, unit)
