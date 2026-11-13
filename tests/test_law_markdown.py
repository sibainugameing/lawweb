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
        self.assertIn("1. 一 歩行者の安全を確保すること。", markdown)
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

    def test_subitem_titles_and_sentences_are_preserved(self):
        xml = SAMPLE.replace(
            "</Paragraph>",
            '<Subitem1><Subitem1Title>イ</Subitem1Title>'
            '<Subitem1Sentence><Sentence>細目の本文。</Sentence></Subitem1Sentence></Subitem1></Paragraph>',
            1,
        )
        markdown = law_xml_to_markdown(xml)
        self.assertIn("- イ 細目の本文。", markdown)

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
