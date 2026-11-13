"""Render LawGit legal XML as readable Japanese Markdown and compare revisions.

Only standard-library modules are used. Unknown e-Gov elements are preserved as
labelled Markdown blocks instead of being silently discarded.
"""
from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET
from typing import Iterable


MAX_XML_CHARS = 16 * 1024 * 1024
MAX_DIFF_LINES = 40_000

HEADING_TAGS = {
    "Part": (2, "PartTitle", "編"),
    "Chapter": (2, "ChapterTitle", "章"),
    "Section": (3, "SectionTitle", "節"),
    "Subsection": (4, "SubsectionTitle", "款"),
    "Division": (5, "DivisionTitle", "目"),
    "SubDivision": (6, "SubDivisionTitle", "細目"),
    "ArticleGroup": (3, "ArticleGroupTitle", "条群"),
}

APPENDIX_TAGS = {
    "AppdxTable": "別表",
    "AppdxStyle": "様式",
    "AppdxFormat": "書式",
    "AppdxFig": "図",
    "Appdx": "付録",
    "SupplProvisionAppdxTable": "附則・別表",
    "SupplProvisionAppdxStyle": "附則・様式",
    "SupplProvisionAppdxFormat": "附則・書式",
    "SupplProvisionAppdxFig": "附則・図",
}

KNOWN_WRAPPERS = {
    "Preamble", "EnactStatement", "MainProvision", "SupplProvision",
    "ArticleGroup", "ParagraphSentence", "ItemSentence", "SubitemSentence",
    "Subitem1Sentence", "Subitem2Sentence", "Subitem3Sentence",
    "Subitem4Sentence", "Subitem5Sentence", "Subitem6Sentence",
    "Subitem7Sentence", "Subitem8Sentence", "Subitem9Sentence",
    "Subitem10Sentence", "List", "ListSentence", "TableStruct",
    "Table", "TableRow", "TableColumn", "Fig", "Note", "Remarks",
    "TOC", "TOCLabel", "TOCArticle", "TOCChapter", "TOCSection",
}

SKIP_TITLE_TAGS = {
    "LawTitle", "ChapterTitle", "PartTitle", "SectionTitle",
    "SubsectionTitle", "DivisionTitle", "SubDivisionTitle",
    "ArticleGroupTitle", "ArticleTitle", "ArticleCaption",
    "SupplProvisionLabel", "SupplProvisionTitle",
    "ParagraphNum", "ItemTitle", "ItemNum",
}


class LawMarkdownError(ValueError):
    """Raised when an XML document cannot safely be rendered."""


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, tag: str | None = None) -> list[ET.Element]:
    items = list(element)
    if tag is None:
        return items
    return [item for item in items if _tag(item) == tag]


def _first(element: ET.Element, *tags: str) -> ET.Element | None:
    for child in list(element):
        if _tag(child) in tags:
            return child
    return None


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    value = "".join(element.itertext())
    return re.sub(r"[ \t\r\n]+", " ", value).strip()


def _attr(element: ET.Element, name: str) -> str:
    return element.attrib.get(name, "").strip()


def _attributes(element: ET.Element) -> str:
    if not element.attrib:
        return ""
    return ", ".join(f"{key}={value!r}" for key, value in sorted(element.attrib.items()))


def _status_notes(element: ET.Element) -> str:
    """Expose legal-status attributes which affect interpretation of provisions."""
    notes: list[str] = []
    for key, label in (("Delete", "削除指定"), ("Hide", "非表示指定")):
        value = _attr(element, key)
        if value.lower() in {"true", "1", "yes"}:
            notes.append(label)
        elif value:
            notes.append(f"{key}={value}")
    old_style = _attr(element, "OldStyle")
    if old_style:
        notes.append(f"旧形式（OldStyle={old_style}）")
    old_number = _attr(element, "OldNum")
    if old_number:
        notes.append(f"旧番号（{old_number}）")
    return " 〔" + "、".join(notes) + "〕" if notes else ""


def _number(element: ET.Element, fallback_prefix: str = "") -> str:
    value = _attr(element, "Num")
    return f"{fallback_prefix}{value}" if value else ""


def _join_nonempty(parts: Iterable[str], separator: str = "　") -> str:
    return separator.join(part for part in parts if part)


def _render_inline_container(
    element: ET.Element,
    excluded_tags: set[str] | None = None,
) -> str:
    """Collect text in document order, optionally skipping structural child elements."""
    parts: list[str] = []
    excluded_tags = excluded_tags or set()

    def visit(node: ET.Element) -> None:
        if _tag(node) in {"Sup", "Sub"}:
            value = "".join(node.itertext()).strip()
            if value:
                parts.append(("^" if _tag(node) == "Sup" else "_") + "(" + value + ")")
            attributes = _attributes(node)
            if attributes:
                parts.append(f"〔{_tag(node)}: {attributes}〕")
            return
        if _tag(node) == "Ruby":
            base_parts = [node.text or ""]
            reading_parts = []
            for child in list(node):
                if _tag(child) == "Rt":
                    reading_parts.append("".join(child.itertext()))
                else:
                    base_parts.append("".join(child.itertext()))
                if child.tail:
                    base_parts.append(child.tail)
            base = "".join(base_parts).strip()
            reading = "".join(reading_parts).strip()
            parts.append(base + (f"（{reading}）" if reading else ""))
            attributes = _attributes(node)
            if attributes:
                parts.append(f"〔Ruby: {attributes}〕")
            return
        if node.text:
            parts.append(node.text)
        for child in list(node):
            if _tag(child) in excluded_tags:
                if child.tail:
                    parts.append(child.tail)
                continue
            visit(child)
            if child.tail:
                parts.append(child.tail)
        attributes = _attributes(node)
        if attributes:
            parts.append(f"〔{_tag(node)}: {attributes}〕")

    visit(element)
    return re.sub(r"[ \t\r\n]+", " ", "".join(parts)).strip()


def _inline_text(element: ET.Element | None) -> str:
    return _render_inline_container(element) if element is not None else ""


def _render_table_cell(element: ET.Element, header: bool = False) -> str:
    value = _render_inline_container(element).replace("|", "\\|").replace("\n", " ")
    annotations = []
    for key in (
        "rowspan", "colspan", "Align", "Valign",
        "BorderTop", "BorderBottom", "BorderLeft", "BorderRight",
    ):
        if element.attrib.get(key):
            annotations.append(f"{key}={element.attrib[key]}")
    if annotations:
        value += " 〔" + ", ".join(annotations) + "〕"
    return value or " "


def _render_table(element: ET.Element, out: list[str], depth: int = 0) -> None:
    is_table_node = _tag(element) == "Table"
    title = _inline_text(_first(element, "TableStructTitle")) if not is_table_node else ""
    remarks = _first(element, "Remarks") if not is_table_node else None
    table = element if is_table_node else _first(element, "Table")
    if title:
        out.extend(["", f"**{title}**", ""])
    if table is None:
        _render_children(element, out, depth, {"TableStructTitle"})
        return

    row_nodes = [
        child for child in list(table)
        if _tag(child) in {"TableHeaderRow", "TableRow"}
    ]
    if not row_nodes:
        _render_children(element, out, depth, {"TableStructTitle", "Table"})
        return

    rows: list[tuple[bool, list[str]]] = []
    max_columns = 0
    for row in row_nodes:
        header_row = _tag(row) == "TableHeaderRow"
        column_tag = "TableHeaderColumn" if header_row else "TableColumn"
        values = [
            _render_table_cell(cell, header=header_row)
            for cell in list(row) if _tag(cell) == column_tag
        ]
        max_columns = max(max_columns, len(values))
        rows.append((header_row, values))

    if max_columns == 0:
        _render_children(element, out, depth, {"TableStructTitle", "Table"})
        return

    # Markdown cannot represent merged cells; rowspan/colspan are kept in the
    # affected cell text so the structural information is not silently lost.
    header_index = next((i for i, (is_header, _) in enumerate(rows) if is_header), None)
    if header_index is not None:
        header_values = rows[header_index][1]
        data_rows = [row for i, row in enumerate(rows) if i != header_index]
    else:
        header_values = [f"列{i + 1}" for i in range(max_columns)]
        data_rows = rows

    header_values = (header_values + [" "] * max_columns)[:max_columns]
    divider = ["---"] * max_columns

    def markdown_row(values: list[str]) -> str:
        values = (values + [" "] * max_columns)[:max_columns]
        return "| " + " | ".join(values) + " |"

    writing_mode = _attr(table, "WritingMode") or "vertical"
    if writing_mode == "vertical":
        out.extend(["", "> 元XMLは縦書き指定です。Markdownでは横方向に整形して表示します。"])
    out.extend(["", markdown_row(header_values), markdown_row(divider)])
    for _, values in data_rows:
        out.append(markdown_row(values))

    if remarks is not None:
        remarks_text = _render_inline_container(remarks)
        if remarks_text:
            out.extend(["", "> 備考：" + remarks_text])

    # Keep any non-table siblings from the TableStruct container.
    if not is_table_node:
        _render_children(element, out, depth, {"TableStructTitle", "Table", "Remarks"})


def _render_children(
    element: ET.Element,
    out: list[str],
    depth: int = 0,
    excluded: set[str] | None = None,
) -> None:
    excluded = excluded or set()
    leading_text = (element.text or "").strip()
    if leading_text:
        out.append(leading_text)
    for child in list(element):
        if _tag(child) not in excluded:
            _render_node(child, out, depth)
        # XML mixed-content text can live in a child's tail; retain it too.
        tail = (child.tail or "").strip()
        if tail:
            out.append(tail)


def _render_heading(
    element: ET.Element,
    out: list[str],
    depth: int,
    title_tag: str,
    fallback_suffix: str,
) -> None:
    title = _inline_text(_first(element, title_tag))
    number = _attr(element, "Num")
    title = title or (f"第{number}{fallback_suffix}" if number else fallback_suffix)
    attributes = _attributes(element)
    if not _text(_first(element, title_tag)) and attributes and not number:
        title = f"{title}（{attributes}）"
    title += _status_notes(element)
    level = HEADING_TAGS[_tag(element)][0]
    out.extend(["", "#" * level + " " + title, ""])
    _render_children(element, out, depth + 1, {title_tag})


def _render_article(element: ET.Element, out: list[str], depth: int) -> None:
    title = _inline_text(_first(element, "ArticleTitle"))
    caption = _inline_text(_first(element, "ArticleCaption"))
    number = _number(element, "第")
    title = title or (number + "条" if number else "条文")
    title += _status_notes(element)
    heading = _join_nonempty([title, caption])
    out.extend(["", "### " + heading, ""])
    _render_children(
        element, out, depth + 1,
        {"ArticleTitle", "ArticleCaption"},
    )


def _render_paragraph(element: ET.Element, out: list[str], depth: int) -> None:
    number = _text(_first(element, "ParagraphNum")) or _number(element)
    caption = _inline_text(_first(element, "ParagraphCaption"))
    sentence_nodes = [
        child for child in list(element)
        if _tag(child) in {"ParagraphSentence", "Sentence"}
    ]
    sentence = "　".join(
        text for text in (_render_inline_container(node) for node in sentence_nodes)
        if text
    )
    prefix = ""
    if sentence:
        marker = f"{number}. " if number else ""
        line = prefix + marker + sentence + _status_notes(element)
        if caption:
            out.append("**" + caption + "**")
        out.append(line)
    elif number:
        out.append(prefix + f"{number}." + _status_notes(element))
    elif _status_notes(element):
        out.append(_status_notes(element).strip())

    excluded = {"ParagraphNum", "ParagraphCaption", "ParagraphSentence", "Sentence"}
    _render_children(element, out, depth + 1, excluded)


def _render_item(element: ET.Element, out: list[str], depth: int, marker: str = "-") -> None:
    tag = _tag(element)
    title_tags = ["ItemTitle", "SubitemTitle"]
    sentence_tags = [
        "ItemSentence", "SubitemSentence",
        "Subitem1Sentence", "Subitem2Sentence", "Subitem3Sentence",
        "Subitem4Sentence", "Subitem5Sentence", "Subitem6Sentence",
        "Subitem7Sentence", "Subitem8Sentence", "Subitem9Sentence",
        "Subitem10Sentence",
    ]
    if tag.startswith("Subitem"):
        title_tags.insert(0, tag + "Title")
        sentence_tags.insert(0, tag + "Sentence")
    title = _inline_text(_first(element, *title_tags))
    sentence_node = _first(element, *sentence_tags)
    sentence = _render_inline_container(sentence_node, {"Table"}) if sentence_node is not None else ""
    if not sentence:
        sentence = _text(_first(element, "Sentence"))
    number = _number(element)
    subitem_match = re.fullmatch(r"Subitem([1-9]|10)", tag)
    prefix = "  " * int(subitem_match.group(1)) if subitem_match else ""

    # e-Gov item titles already contain legal numbering (一, 二, イ, ロ).
    # Prefer those labels over duplicating the numeric Num attribute.
    status = _status_notes(element)
    if title and sentence:
        out.append(f"{prefix}{marker} {title}　{sentence}{status}")
    elif title:
        out.append(f"{prefix}{marker} {title}{status}")
    elif sentence and number:
        out.append(f"{prefix}{number}. {sentence}{status}")
    elif sentence:
        out.append(f"{prefix}{marker} {sentence}{status}")
    elif number:
        out.append(f"{prefix}{number}.{status}")
    else:
        attrs = _attributes(element)
        out.append(f"{prefix}{marker} [{tag}]" + (f"（{attrs}）" if attrs else "") + status)

    excluded = set(title_tags) | set(sentence_tags) | {"Sentence"}
    _render_children(element, out, depth + 1, excluded)
    if sentence_node is not None:
        for table_node in list(sentence_node):
            if _tag(table_node) == "Table":
                _render_table(table_node, out, depth + 1)


TOC_TITLE_TAGS = {
    "TOCPart": ("PartTitle", "ArticleRange"),
    "TOCChapter": ("ChapterTitle", "ArticleRange"),
    "TOCSection": ("SectionTitle", "ArticleRange"),
    "TOCSubsection": ("SubsectionTitle", "ArticleRange"),
    "TOCDivision": ("DivisionTitle", "ArticleRange"),
    "TOCArticle": ("ArticleTitle", "ArticleCaption"),
    "TOCSupplProvision": ("SupplProvisionLabel", "ArticleRange"),
}


def _render_toc_item(element: ET.Element, out: list[str], depth: int) -> None:
    tag = _tag(element)
    title_tag, secondary_tag = TOC_TITLE_TAGS[tag]
    title = _inline_text(_first(element, title_tag))
    secondary = _inline_text(_first(element, secondary_tag))
    text = _join_nonempty([title, secondary])
    if not text:
        text = _attr(element, "Num") or tag
    out.append("  " * depth + "- " + text)
    _render_children(element, out, depth + 1, {title_tag, secondary_tag})


def _render_sublist(element: ET.Element, out: list[str], depth: int) -> None:
    tag = _tag(element)
    match = re.fullmatch(r"Sublist([1-3])", tag)
    if not match:
        _render_children(element, out, depth)
        return
    level = int(match.group(1))
    sentence_tag = f"Sublist{level}Sentence"
    sentence_element = _first(element, sentence_tag)
    sentence = _render_inline_container(sentence_element) if sentence_element is not None else ""
    prefix = "  " * level
    if sentence:
        out.append(prefix + "- " + sentence)
    _render_children(element, out, depth + 1, {sentence_tag})


def _render_node(element: ET.Element, out: list[str], depth: int = 0) -> None:
    tag = _tag(element)

    if tag in SKIP_TITLE_TAGS or tag in {"LawNum", "LawTitle"}:
        return

    if tag == "TOC":
        label = _inline_text(_first(element, "TOCLabel")) or "目次"
        out.extend(["", "## " + label, ""])
        _render_children(element, out, depth + 1, {"TOCLabel"})
        return

    if tag in TOC_TITLE_TAGS:
        _render_toc_item(element, out, depth)
        return

    if tag == "TOCAppdxTableLabel":
        value = _render_inline_container(element)
        if value:
            out.append("  " * depth + "- " + value)
        return

    if tag == "MainProvision":
        heading = "本則"
        if _attr(element, "Extract").lower() in {"true", "1", "yes"}:
            heading += "（抄録）"
        heading += _status_notes(element)
        out.extend(["", "## " + heading, ""])
        _render_children(element, out, depth + 1)
        return

    if tag == "SupplProvision":
        label = _inline_text(_first(element, "SupplProvisionLabel", "SupplProvisionTitle"))
        heading = label if label.startswith("附則") else ("附則" + (f"　{label}" if label else ""))
        provision_type = _attr(element, "Type")
        if provision_type:
            type_label = {"New": "制定時", "Amend": "改正時"}.get(provision_type, provision_type)
            heading += f"（{type_label}）"
        amendment = _attr(element, "AmendLawNum")
        if amendment:
            heading += f"（改正法令：{amendment}）"
        if _attr(element, "Extract").lower() in {"true", "1", "yes"}:
            heading += "（抄録）"
        heading += _status_notes(element)
        out.extend(["", "## " + heading, ""])
        _render_children(element, out, depth + 1, {"SupplProvisionLabel", "SupplProvisionTitle"})
        return

    if tag == "Preamble":
        out.extend(["", "## 前文", ""])
        _render_children(element, out, depth + 1)
        return

    if tag in HEADING_TAGS:
        _render_heading(element, out, depth, HEADING_TAGS[tag][1], HEADING_TAGS[tag][2])
        return

    if tag == "Article":
        _render_article(element, out, depth)
        return

    if tag == "Paragraph":
        _render_paragraph(element, out, depth)
        return

    if tag == "Item" or re.fullmatch(r"Subitem(?:[1-9]|10)", tag):
        _render_item(element, out, depth, marker="-")
        return

    if tag in {"TableStruct", "Table"}:
        _render_table(element, out, depth)
        return

    if tag == "List":
        out.append("")
        _render_children(element, out, 0)
        return

    if tag == "ListSentence":
        value = _render_inline_container(element)
        if value:
            out.append("- " + value)
        return

    if re.fullmatch(r"Sublist[1-3]", tag):
        _render_sublist(element, out, depth)
        return

    if tag == "Sentence":
        value = _render_inline_container(element)
        if value:
            out.append(value)
        return

    if tag in APPENDIX_TAGS:
        title_tags = (
            "AppdxTableTitle", "AppdxStyleTitle", "AppdxFormatTitle",
            "AppdxFigTitle", "SupplProvisionAppdxTableTitle",
            "SupplProvisionAppdxStyleTitle", "SupplProvisionAppdxFormatTitle",
        )
        label = _inline_text(_first(element, *title_tags))
        number = _number(element)
        heading = _join_nonempty([APPENDIX_TAGS[tag], number, label])
        out.extend(["", "## " + heading, ""])
        _render_children(element, out, depth + 1, set(title_tags))
        return

    children = list(element)
    if not children:
        value = _render_inline_container(element)
        attrs = _attributes(element)
        if value:
            if attrs:
                out.append("  " * depth + f"**{tag}（{attrs}）** " + value)
            else:
                out.append("  " * depth + value)
        else:
            attrs = _attributes(element)
            if attrs:
                out.append("  " * depth + f"*{tag}（{attrs}）*")
        return

    # Unknown or auxiliary containers stay visible as labelled, nested sections.
    # Use only direct text here; itertext() would duplicate the child text below.
    direct_text = re.sub(r"[ \t\r\n]+", " ", (element.text or "")).strip()
    label = tag
    attrs = _attributes(element)
    if attrs:
        label += f"（{attrs}）"
    if direct_text:
        out.extend(["", f"**{label}**", ""])
    elif tag not in KNOWN_WRAPPERS:
        out.extend(["", "  " * depth + f"**{label}**", ""])
    _render_children(element, out, depth + 1)


def law_xml_to_markdown(xml_text: str) -> str:
    """Convert a LawGit <law> XML document into readable Markdown."""
    if not isinstance(xml_text, str) or not xml_text.strip():
        raise LawMarkdownError("XML本文が空です。")
    if len(xml_text) > MAX_XML_CHARS:
        raise LawMarkdownError("XMLが大きすぎるため表示できません。")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml_text, flags=re.IGNORECASE):
        raise LawMarkdownError("DOCTYPE / ENTITY 宣言を含むXMLは表示できません。")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise LawMarkdownError(f"XMLを解析できません: {exc}") from exc
    if _tag(root) != "law":
        raise LawMarkdownError("LawGit形式の <law> XML ではありません。")

    identifiers = _first(root, "identifiers")
    metadata = _first(root, "metadata")
    body = _first(root, "body")
    if metadata is None or body is None:
        raise LawMarkdownError("metadata または body 要素がありません。")

    name = _text(_first(metadata, "name")) or "名称未設定"
    law_number = _text(_first(metadata, "law-number"))
    law_type = _text(_first(metadata, "type"))
    e_gov_id = _text(_first(identifiers, "e-gov-id")) if identifiers is not None else ""

    lines = [f"# {name}", ""]
    if law_number:
        lines.append(f"> 法令番号：{law_number}")
    if law_type:
        lines.append(f"> 種別：{law_type}")
    if e_gov_id:
        lines.append(f"> e-Gov法令ID：{e_gov_id}")
    lines.extend(["", "---", ""])

    for child in list(body):
        _render_node(child, lines, 0)

    markdown = "\n".join(lines)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    if not markdown:
        raise LawMarkdownError("Markdownに変換できる本文がありません。")
    return markdown + "\n"


def law_markdown_diff(
    older_xml: str,
    newer_xml: str,
    older_label: str = "旧版",
    newer_label: str = "新版",
) -> str:
    """Return a unified diff between two readable Markdown representations."""
    older = law_xml_to_markdown(older_xml).splitlines()
    newer = law_xml_to_markdown(newer_xml).splitlines()
    if len(older) > MAX_DIFF_LINES or len(newer) > MAX_DIFF_LINES:
        raise LawMarkdownError("差分対象のMarkdownが大きすぎます。")
    diff = difflib.unified_diff(
        older, newer,
        fromfile=older_label,
        tofile=newer_label,
        n=4,
        lineterm="",
    )
    return "\n".join(diff)
