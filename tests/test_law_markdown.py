import unittest

from law_markdown import LawMarkdownError, law_markdown_diff, law_xml_to_markdown


SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<law>
  <identifiers>
    <uuid>550e8400-e29b-41d4-a716-446655440000</uuid>
    <e-gov-id>335AC0000000105</e-gov-id>
  </identifiers>
  <metadata>
    <name>道路交通法</name>
    <law-number>昭和三十五年法律第百五号</law-number>
    <type>法律</type>
  </metadata>
  <body>
    <LawTitle>道路交通法</LawTitle>
    <MainProvision>
      <Chapter Num="1">
        <ChapterTitle>第一章　総則</ChapterTitle>
        <Article Num="1">
          <ArticleCaption>（目的）</ArticleCaption>
          <ArticleTitle>第一条</ArticleTitle>
          <Paragraph Num="1">
            <ParagraphNum/>
            <ParagraphSentence><Sentence>この法律は、&lt;道路&gt; &amp; 交通の安全を図る。</Sentence></ParagraphSentence>
          </Paragraph>
          <Paragraph Num="2">
            <ParagraphNum>２</ParagraphNum>
            <ParagraphSentence><Sentence>前項に定めるほか、必要な事項を定める。</Sentence></ParagraphSentence>
            <Item Num="1">
              <ItemTitle>一</ItemTitle>
              <ItemSentence><Sentence>歩行者の安全を確保すること。</Sentence></ItemSentence>
            </Item>
          </Paragraph>
        </Article>
      </Chapter>
    </MainProvision>
    <SupplProvision>
      <SupplProvisionLabel>附則</SupplProvisionLabel>
      <Article Num="1">
        <ArticleTitle>第一条</ArticleTitle>
        <Paragraph Num="1"><ParagraphSentence><Sentence>この法律は、公布の日から施行する。</Sentence></ParagraphSentence></Paragraph>
      </Article>
    </SupplProvision>
  </body>
</law>
"""


class LawMarkdownTests(unittest.TestCase):
    def test_metadata_and_legal_structure_are_rendered(self):
        markdown = law_xml_to_markdown(SAMPLE)
        self.assertIn("# 道路交通法", markdown)
        self.assertIn("> 法令番号：昭和三十五年法律第百五号", markdown)
        self.assertIn("> 種別：法律", markdown)
        self.assertIn("> e-Gov法令ID：335AC0000000105", markdown)
        self.assertIn("## 本則", markdown)
        self.assertIn("## 第一章　総則", markdown)
        self.assertIn("### 第一条　（目的）", markdown)
        self.assertIn("この法律は、<道路> & 交通の安全を図る。", markdown)
        self.assertIn("２. 前項に定めるほか、必要な事項を定める。", markdown)
        self.assertIn("- 一　歩行者の安全を確保すること。", markdown)
        self.assertIn("## 附則", markdown)

    def test_unknown_elements_and_attributes_are_not_discarded(self):
        xml = SAMPLE.replace(
            "</MainProvision>",
            '<SpecialClause Kind="custom"><SpecialHeading>独自の見出し</SpecialHeading>'
            '<SpecialText>独自要素の本文</SpecialText></SpecialClause></MainProvision>',
        )
        markdown = law_xml_to_markdown(xml)
        self.assertIn("**SpecialClause（Kind='custom'）**", markdown)
        self.assertIn("独自の見出し", markdown)
        self.assertIn("独自要素の本文", markdown)

    def test_ruby_is_rendered_as_base_text_and_reading(self):
        xml = SAMPLE.replace(
            "この法律は、&lt;道路&gt; &amp; 交通の安全を図る。",
            "この法律は、<Ruby>道路<Rt>どうろ</Rt></Ruby>の安全を図る。",
        )
        markdown = law_xml_to_markdown(xml)
        self.assertIn("この法律は、道路（どうろ）の安全を図る。", markdown)

    def test_table_struct_is_rendered_as_markdown_table(self):
        table = """<TableStruct>
        <TableStructTitle>別表第一　分類</TableStructTitle>
        <Table WritingMode="vertical">
          <TableHeaderRow><TableHeaderColumn>区分</TableHeaderColumn><TableHeaderColumn>内容</TableHeaderColumn></TableHeaderRow>
          <TableRow><TableColumn>一</TableColumn><TableColumn rowspan="2">第一欄</TableColumn></TableRow>
          <TableRow><TableColumn>二</TableColumn><TableColumn>第二欄</TableColumn></TableRow>
        </Table></TableStruct>"""
        xml = SAMPLE.replace("</MainProvision>", table + "</MainProvision>")
        markdown = law_xml_to_markdown(xml)
        self.assertIn("**別表第一　分類**", markdown)
        self.assertIn("| 区分 | 内容 |", markdown)
        self.assertIn("| 一 | 第一欄 〔rowspan=2〕 |", markdown)
        self.assertIn("元XMLは縦書き指定", markdown)

    def test_table_nested_in_item_sentence_is_rendered_separately(self):
        item = """<Item Num="1"><ItemTitle>一</ItemTitle><ItemSentence>
        <Sentence>本文に続く表。</Sentence>
        <Table><TableHeaderRow><TableHeaderColumn>項目</TableHeaderColumn><TableHeaderColumn>内容</TableHeaderColumn></TableHeaderRow>
        <TableRow><TableColumn>A</TableColumn><TableColumn>B</TableColumn></TableRow></Table>
        </ItemSentence></Item>"""
        xml = SAMPLE.replace("</Paragraph>", item + "</Paragraph>", 1)
        markdown = law_xml_to_markdown(xml)
        self.assertIn("- 一　本文に続く表。", markdown)
        self.assertIn("| 項目 | 内容 |", markdown)
        self.assertIn("| A | B |", markdown)

    def test_table_without_header_gets_column_labels(self):
        table = """<TableStruct><Table><TableRow><TableColumn>値A</TableColumn>
        <TableColumn>値B</TableColumn></TableRow></Table></TableStruct>"""
        xml = SAMPLE.replace("</MainProvision>", table + "</MainProvision>")
        markdown = law_xml_to_markdown(xml)
        self.assertIn("| 列1 | 列2 |", markdown)
        self.assertIn("| 値A | 値B |", markdown)

    def test_table_pipe_characters_are_escaped(self):
        table = """<TableStruct><Table><TableRow><TableColumn>A|B</TableColumn>
        <TableColumn>C</TableColumn></TableRow></Table></TableStruct>"""
        xml = SAMPLE.replace("</MainProvision>", table + "</MainProvision>")
        markdown = law_xml_to_markdown(xml)
        self.assertIn("A\\|B", markdown)

    def test_table_and_toc_nested_structure_are_rendered(self):
        toc = """<TOC><TOCLabel>目次</TOCLabel>
        <TOCChapter Num="1"><ChapterTitle>第一章　総則</ChapterTitle>
        <ArticleRange>（第一条―第三条）</ArticleRange>
        <TOCArticle Num="1"><ArticleTitle>第一条</ArticleTitle>
        <ArticleCaption>（目的）</ArticleCaption></TOCArticle></TOCChapter></TOC>"""
        xml = SAMPLE.replace("<MainProvision>", toc + "<MainProvision>")
        markdown = law_xml_to_markdown(xml)
        self.assertIn("## 目次", markdown)
        self.assertIn("- 第一章　総則　（第一条―第三条）", markdown)
        self.assertIn("- 第一条　（目的）", markdown)

    def test_legal_list_and_sublist_are_rendered_as_nested_lists(self):
        legal_list = """<List><ListSentence><Sentence>次に掲げる事項。</Sentence></ListSentence>
        <Sublist1><Sublist1Sentence><Sentence>第一の細目。</Sentence></Sublist1Sentence>
        <Sublist2><Sublist2Sentence><Sentence>さらに下位の細目。</Sentence></Sublist2Sentence></Sublist2>
        </Sublist1></List>"""
        xml = SAMPLE.replace("</MainProvision>", legal_list + "</MainProvision>")
        markdown = law_xml_to_markdown(xml)
        self.assertIn("- 次に掲げる事項。", markdown)
        self.assertIn("  - 第一の細目。", markdown)
        self.assertIn("    - さらに下位の細目。", markdown)

    def test_subitem_titles_and_sentences_are_preserved(self):
        xml = SAMPLE.replace(
            "</Paragraph>",
            '<Subitem1><Subitem1Title>イ</Subitem1Title>'
            '<Subitem1Sentence><Sentence>細目の本文。</Sentence></Subitem1Sentence></Subitem1></Paragraph>',
            1,
        )
        markdown = law_xml_to_markdown(xml)
        self.assertIn("  - イ　細目の本文。", markdown)

    def test_escaped_entities_are_decoded_as_text(self):
        markdown = law_xml_to_markdown(SAMPLE)
        self.assertIn("<道路> & 交通", markdown)

    def test_doctype_and_entity_declarations_are_rejected(self):
        for payload in (
            '<!DOCTYPE law [<!ENTITY x "unsafe">]>' + SAMPLE,
            '<!ENTITY x "unsafe">' + SAMPLE,
        ):
            with self.subTest(payload=payload[:30]):
                with self.assertRaises(LawMarkdownError):
                    law_xml_to_markdown(payload)

    def test_invalid_or_wrong_root_xml_is_rejected(self):
        with self.assertRaises(LawMarkdownError):
            law_xml_to_markdown("<law><body>")
        with self.assertRaises(LawMarkdownError):
            law_xml_to_markdown("<root><body><Sentence>本文</Sentence></body></root>")

    def test_missing_metadata_or_body_is_rejected(self):
        with self.assertRaises(LawMarkdownError):
            law_xml_to_markdown("<law><metadata/><body/></law>")
        with self.assertRaises(LawMarkdownError):
            law_xml_to_markdown("<law><metadata><name>法</name></metadata></law>")

    def test_diff_shows_only_changed_markdown_lines(self):
        older = SAMPLE
        newer = SAMPLE.replace("交通の安全を図る", "交通の安全と円滑を図る")
        diff = law_markdown_diff(older, newer, "旧版", "新版")
        self.assertIn("--- 旧版", diff)
        self.assertIn("+++ 新版", diff)
        self.assertIn("-1. この法律は、<道路> & 交通の安全を図る。", diff)
        self.assertIn("+1. この法律は、<道路> & 交通の安全と円滑を図る。", diff)

    def test_identical_revisions_have_empty_diff(self):
        self.assertEqual(law_markdown_diff(SAMPLE, SAMPLE), "")

    def test_empty_and_oversized_xml_are_rejected(self):
        with self.assertRaises(LawMarkdownError):
            law_xml_to_markdown("  ")
        with self.assertRaises(LawMarkdownError):
            law_xml_to_markdown(" " * (16 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
