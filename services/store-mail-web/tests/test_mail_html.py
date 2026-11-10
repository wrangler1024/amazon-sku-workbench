"""The HTML viewer preserves email layout and drops active content."""

import unittest

from store_mail_web.mail_html import prepare_email_html


class EmailHtmlTest(unittest.TestCase):
    def test_keeps_tables_inline_styles_and_images(self):
        html = ('<!doctype html><html><head><style>td{color:#123}</style>'
                '<link rel="stylesheet" href="https://cdn.example.test/mail.css">'
                '<link rel="preload" href="https://cdn.example.test/tracker">'
                '</head><body><table style="width:600px"><tr><td>退款&nbsp;通知'
                '<img src="https://cdn.example.test/logo.png" alt="logo"></td></tr>'
                '</table></body></html>')
        blocked = prepare_email_html(html)
        self.assertIn('<table style="width:600px">', blocked)
        self.assertIn('退款&nbsp;通知', blocked)
        self.assertIn('<img src="https://cdn.example.test/logo.png"', blocked)
        self.assertNotIn('<link', blocked)
        allowed = prepare_email_html(html, allow_remote=True)
        self.assertIn('rel="stylesheet"', allowed)
        self.assertNotIn('rel="preload"', allowed)

    def test_removes_active_content_and_navigation(self):
        html = ('<meta http-equiv="refresh" content="0;url=https://evil.test">'
                '<base href="https://evil.test/">'
                '<script>alert(1)</script><iframe src="https://evil.test"></iframe>'
                '<embed src="https://evil.test/flash">'
                '<form action="https://evil.test"><p>正文</p></form>'
                '<a href="javascript:alert(1)" onclick="alert(1)">链接</a>')
        result = prepare_email_html(html, allow_remote=True)
        self.assertIn('<p>正文</p>', result)
        self.assertNotIn('http-equiv="refresh"', result)
        self.assertNotIn('<base', result)
        self.assertNotIn('alert(1)', result)
        self.assertNotIn('<iframe', result)
        self.assertNotIn('<form', result)
        self.assertNotIn('javascript:', result)
        self.assertNotIn('onclick', result)

    def test_upgrades_legacy_amazon_content_image_only(self):
        html = ('<img src="http://g-ecx.images-amazon.com/product.png">'
                '<img src="http://na.r.ads.amazon.com/tracker.gif">')
        result = prepare_email_html(html, allow_remote=True)
        self.assertIn('src="https://g-ecx.images-amazon.com/product.png"', result)
        self.assertIn('src="http://na.r.ads.amazon.com/tracker.gif"', result)


if __name__ == "__main__":
    unittest.main()
