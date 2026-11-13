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
    return re.sub(r"\s+", " ", value).strip()


def _attr(element: ET.Element, name: str) -> str:
    return element.attrib.get(name, "").strip()


def _attributes(element: ET.Element) -> str:
    if not element.attrib:
        return ""
    return ", ".join(f"{key}={value!r}" for key, value in sorted(element.attrib.items()))


def _number(element: ET.Element, fallback_prefix: str = "") -> str:
    value = _attr(element, "Num")
    return f"{fallback_prefix}{value}" if value else ""


def _join_nonempty(parts: Iterable[str], separator: str = "　") -> str:
    return separator.join(part for part in parts if part)


def _render_inline_container(element: ET.Element) -> str:
    """Collect text in document order, retaining spaces between mixed-content nodes."""
    parts: list[str] = []

    def visit(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in list(node):
            visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(element)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _render_children(
    element: ET.Element,
    out: list[str],
    depth: int = 0,
    excluded: set[str] | None = None,
) -> None:
    excluded = excluded or set()
    for child in list(element):
        if _tag(child) in excluded:
            continue
        _render_node(child, out, depth)


def _render_heading(
    element: ET.Element,
    out: list[str],
    depth: int,
    title_tag: str,
    fallback_suffix: str,
) -> None:
    title = _text(_first(element, title_tag))
    number = _attr(element, "Num")
    title = title or (f"第{number}{fallback_suffix}" if number else fallback_suffix)
    attributes = _attributes(element)
    if not _text(_first(element, title_tag)) and attributes and not number:
        title = f"{title}（{attributes}）"
    level = HEADING_TAGS[_tag(element)][0]
    out.extend(["", "#" * level + " " + title, ""])
    _render_children(element, out, depth + 1, {title_tag})


def _render_article(element: ET.Element, out: list[str], depth: int) -> None:
    title = _text(_first(element, "ArticleTitle"))
    caption = _text(_first(element, "ArticleCaption"))
    number = _number(element, "第")
    title = title or (number + "条" if number else "条文")
    heading = _join_nonempty([title, caption])
    out.extend(["", "### " + heading, ""])
    _render_children(
        element, out, depth + 1,
        {"ArticleTitle", "ArticleCaption"},
    )


def _render_paragraph(element: ET.Element, out: list[str], depth: int) -> None:
    number = _text(_first(element, "ParagraphNum")) or _number(element)
    sentence_nodes = [
        child for child in list(element)
        if _tag(child) in {"ParagraphSentence", "Sentence"}
    ]
    sentence = "　".join(
        text for text in (_render_inline_container(node) for node in sentence_nodes)
        if text
    )
    prefix = "  " * depth
    if sentence:
        marker = f"{number}. " if number else ""
        out.append(prefix + marker + sentence)
    elif number:
        out.append(prefix + f"{number}.")
    else:
        direct = (element.text or "").strip()
        if direct:
            out.append(prefix + direct)

    excluded = {"ParagraphNum", "ParagraphSentence", "Sentence"}
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
    title = _text(_first(element, *title_tags))
    sentence_node = _first(element, *sentence_tags)
    sentence = _render_inline_container(sentence_node) if sentence_node is not None else ""
    if not sentence:
        sentence = _text(_first(element, "Sentence"))
    content = _join_nonempty([title, sentence], " ")
    number = _number(element)
    prefix = "  " * depth
    if number:
        out.append(f"{prefix}{number}. {content}".rstrip())
    elif content:
        out.append(f"{prefix}{marker} {content}".rstrip())
    else:
        attrs = _attributes(element)
        out.append(f"{prefix}{marker} [{_tag(element)}]" + (f"（{attrs}）" if attrs else ""))

    excluded = set(title_tags) | set(sentence_tags) | {"Sentence"}
    _render_children(element, out, depth + 1, excluded)


def _render_node(element: ET.Element, out: list[str], depth: int = 0) -> None:
    tag = _tag(element)

    if tag in SKIP_TITLE_TAGS or tag in {"LawNum", "LawTitle"}:
        return

    if tag == "MainProvision":
        out.extend(["", "## 本則", ""])
        _render_children(element, out, depth + 1)
        return

    if tag == "SupplProvision":
        label = _text(_first(element, "SupplProvisionLabel", "SupplProvisionTitle"))
        heading = "附則" + (f"（{label}）" if label and label != "附則" else "")
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

    if tag == "Sentence":
        value = _render_inline_container(element)
        if value:
            out.append("  " * depth + value)
        return

    if tag in APPENDIX_TAGS:
        label = _text(_first(element, "AppdxTableTitle", "AppdxStyleTitle", "AppdxFormatTitle", "AppdxFigTitle"))
        number = _number(element)
        heading = _join_nonempty([APPENDIX_TAGS[tag], number, label])
        out.extend(["", "## " + heading, ""])
        _render_children(
            element, out, depth + 1,
            {"AppdxTableTitle", "AppdxStyleTitle", "AppdxFormatTitle", "AppdxFigTitle"},
        )
        return

    children = list(element)
    if not children:
        value = _render_inline_container(element)
        if value:
            out.append("  " * depth + value)
        else:
            attrs = _attributes(element)
            if attrs:
                out.append("  " * depth + f"*{tag}（{attrs}）*")
        return

    # Unknown or auxiliary containers stay visible as labelled, nested sections.
    # Use only direct text here; itertext() would duplicate the child text below.
    direct_text = re.sub(r"\\s+", " ", (element.text or "")).strip()
    label = tag
    attrs = _attributes(element)
    if attrs:
        label += f"（{attrs}）"
    if direct_text:
        out.extend(["", "  " * depth + f"**{label}**", "", "  " * (depth + 1) + direct_text])
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
