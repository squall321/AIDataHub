"""``compute_capabilities`` — content shape 로부터 구조 라벨을 도출.

레코드의 ``content`` 페이로드 형태를 검사해 일반화된 ``capabilities`` 라벨
배열을 반환한다. 이 라벨은 ``GET /api/views/{hierarchical|tabular|generalized}``
및 ``?capabilities=...`` 필터에서 슬라이스 키로 사용된다.

표준 라벨 (출현 순서대로 의미 있는 정렬, 중복 없음):
    - sections     : ``content.sections`` 가 비어있지 않은 리스트.
    - blocks       : 어떤 섹션이라도 ``blocks`` 배열을 가짐.
    - tables       : 표가 있음 — 다음 중 하나로 감지됨:
                     * top-level ``content.tables`` 가 비어있지 않거나
                     * DATA 변종처럼 ``headers``/``rows`` 를 가짐
                     * 섹션 ``blocks[].type == "table"`` 또는 비어있지 않은 ``table_refs``.
    - figures      : ``content.figures`` 또는 어떤 ``figure_refs`` 가 비어있지 않음.
    - attachments  : ``content.attachments`` 비어있지 않음 (Agent 10 첨부 모델).
    - embeddings   : ``has_embedding=True`` 가 명시적으로 전달됨.
    - rows         : DATA 행렬 형태 (``rows`` 가 리스트).
    - headers      : DATA 헤더 행 (``headers`` 가 리스트).
    - samples      : ``samples`` 배열.
    - files        : SIM ``input_files``/``inputs``/``outputs`` 또는 CAD ``files``.
    - components   : CAD ``components`` 배열.
    - inputs       : SIM ``inputs`` 가 비어있지 않은 dict.
    - outputs      : SIM ``outputs`` 가 비어있지 않은 dict.

설계 원칙:
    - 부수효과 없음. 입력 dict 를 변경하지 않음.
    - 알 수 없는 키는 무시.
    - 결과는 항상 ``list[str]`` 이며 정렬/중복 제거됨 (정렬 키 = ``CAPABILITY_LABELS`` 순).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# CAPABILITY_LABELS 순서를 따라 정렬하기 위해 우선순위 맵을 만든다.
_CAPABILITY_ORDER: tuple[str, ...] = (
    "sections",
    "blocks",
    "tables",
    "figures",
    "attachments",
    "embeddings",
    "rows",
    "headers",
    "samples",
    "files",
    "components",
    "inputs",
    "outputs",
)
_ORDER_INDEX: dict[str, int] = {
    label: i for i, label in enumerate(_CAPABILITY_ORDER)
}


def _is_nonempty_list(v: Any) -> bool:
    return isinstance(v, list) and len(v) > 0


def _is_nonempty_dict(v: Any) -> bool:
    return isinstance(v, dict) and len(v) > 0


def _walk_blocks(sections: Iterable[Any]) -> tuple[bool, bool, bool, bool]:
    """sections 트리를 재귀 walk 해 (has_blocks, has_inline_table, has_figure_ref, has_table_ref) 를 반환."""
    has_blocks = False
    has_table = False
    has_figure_ref = False
    has_table_ref = False

    def visit(node: Any) -> None:
        nonlocal has_blocks, has_table, has_figure_ref, has_table_ref
        if not isinstance(node, dict):
            return
        blocks = node.get("blocks")
        if _is_nonempty_list(blocks):
            has_blocks = True
            for b in blocks:
                if isinstance(b, dict):
                    btype = b.get("type")
                    if btype == "table":
                        has_table = True
                    elif btype == "figure":
                        has_figure_ref = True
        if _is_nonempty_list(node.get("figure_refs")):
            has_figure_ref = True
        if _is_nonempty_list(node.get("table_refs")):
            has_table_ref = True
        for child in node.get("children") or []:
            visit(child)

    for top in sections:
        visit(top)
    return has_blocks, has_table, has_figure_ref, has_table_ref


def compute_capabilities(
    content: dict[str, Any] | None,
    *,
    has_embedding: bool = False,
) -> list[str]:
    """``content`` 페이로드의 구조 형태로부터 capabilities 라벨 리스트를 계산.

    Args:
        content: 레코드의 ``content`` dict. ``None`` / 비어있는 dict 도 허용.
        has_embedding: 호출자가 임베딩 존재 여부를 명시적으로 알릴 때 ``True``.

    Returns:
        정렬된 라벨 리스트 (중복 제거). 예: ``['sections', 'blocks', 'tables']``.
    """
    found: set[str] = set()
    c: dict[str, Any] = content if isinstance(content, dict) else {}

    # ---- DOC-shape (sections / blocks / figures / tables) ----------------
    sections = c.get("sections")
    if _is_nonempty_list(sections):
        found.add("sections")
        has_blocks, inline_table, fig_ref, tab_ref = _walk_blocks(sections)
        if has_blocks:
            found.add("blocks")
        if inline_table or tab_ref:
            found.add("tables")
        if fig_ref:
            found.add("figures")

    if _is_nonempty_list(c.get("figures")):
        found.add("figures")
    if _is_nonempty_list(c.get("tables")):
        found.add("tables")

    # ---- DATA-shape (headers/rows) ---------------------------------------
    if _is_nonempty_list(c.get("rows")):
        found.add("rows")
        # rows 가 있으면 사실상 표 형태로 간주.
        found.add("tables")
    if _is_nonempty_list(c.get("headers")):
        found.add("headers")
        found.add("tables")
    if _is_nonempty_list(c.get("samples")):
        found.add("samples")

    # ---- SIM-shape (inputs/outputs/input_files) --------------------------
    if _is_nonempty_dict(c.get("inputs")):
        found.add("inputs")
    if _is_nonempty_dict(c.get("outputs")):
        found.add("outputs")
    if _is_nonempty_list(c.get("input_files")):
        found.add("files")

    # ---- CAD-shape (components/files) ------------------------------------
    if _is_nonempty_list(c.get("components")):
        found.add("components")
    if _is_nonempty_list(c.get("files")):
        found.add("files")

    # ---- Attachments (Agent 10 협력) -------------------------------------
    if _is_nonempty_list(c.get("attachments")):
        found.add("attachments")

    # ---- Embeddings (외부 신호) ------------------------------------------
    if has_embedding:
        found.add("embeddings")

    # 정렬 — CAPABILITY_LABELS 순서가 우선, 알 수 없는 라벨은 알파벳 뒤로.
    return sorted(
        found,
        key=lambda x: (_ORDER_INDEX.get(x, len(_ORDER_INDEX)), x),
    )


__all__ = ["compute_capabilities"]
