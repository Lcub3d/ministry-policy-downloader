import unittest

from policy_harvester.extract import extract_document


class ExtractTests(unittest.TestCase):
    def test_extracts_article_markdown_and_attachment_urls(self):
        document = extract_document(
            "https://example.gov.cn/policy/1.html",
            """
            <html><head><title>站点标题</title></head><body>
              <nav>导航</nav>
              <article>
                <h1>节能 政策</h1>
                <p>第一段<strong>正文</strong>。</p>
                <a href="../files/report.pdf">附件：报告.pdf</a>
                <a href="../files/report.pdf">重复链接</a>
                <a href="/about.html">普通链接</a>
              </article>
            </body></html>
            """,
        )

        self.assertEqual(document.title, "节能 政策")
        self.assertIn("第一段", document.markdown)
        self.assertNotIn("导航", document.markdown)
        self.assertEqual(len(document.attachments), 1)
        self.assertEqual(document.attachments[0].name, "附件：报告.pdf")
        self.assertEqual(
            document.attachments[0].url,
            "https://example.gov.cn/files/report.pdf",
        )

    def test_uses_filename_when_link_text_is_not_a_filename(self):
        document = extract_document(
            "https://example.gov.cn/a/",
            '<main><a href="files/form.docx?download=1">点击下载</a></main>',
        )
        self.assertEqual(document.attachments[0].name, "form.docx")


if __name__ == "__main__":
    unittest.main()
