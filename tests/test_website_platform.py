"""Public platform copy, crawl metadata and administrator isolation regressions."""
from html.parser import HTMLParser
import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from xml.etree import ElementTree

from website.server import create_server


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class WebsitePlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.server = create_server('127.0.0.1', 0, root / 'site.db', root / 'rank.db',
                                    admin_token='platform-test-token', download_dir=root / 'downloads')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_home_metadata_and_platform_links_are_server_rendered(self):
        status, headers, body = self.request('/')
        self.assertEqual(status, 200)
        page = PageParser()
        html = body.decode('utf-8')
        page.feed(html)
        meta = {attrs.get('name', attrs.get('property')): attrs.get('content')
                for tag, attrs in page.tags if tag == 'meta'}
        for keyword in ('桌面图标台球', 'Windows', 'Mac'):
            self.assertIn(keyword, meta['description'])
        self.assertEqual(meta['og:url'], 'https://deskpools.com/')
        self.assertEqual(meta['twitter:card'], 'summary_large_image')
        self.assertTrue(any(tag == 'link' and attrs.get('rel') == 'canonical'
                            and attrs.get('href') == 'https://deskpools.com/'
                            for tag, attrs in page.tags))
        self.assertEqual(sum(tag == 'h1' for tag, _ in page.tags), 1)
        urls = [attrs.get('href', '') for tag, attrs in page.tags if tag == 'a']
        for source in ('hero', 'windows', 'macos'):
            self.assertIn('/download?source=' + source, urls)
        self.assertFalse(any(url.startswith('/admin') for url in urls))
        scripts = [attrs.get('src') for tag, attrs in page.tags if tag == 'script']
        self.assertIn('https://tj.xlsat.cn/script.js', scripts)
        self.assertIn('https://tj.xlsat.cn', headers['Content-Security-Policy'])
        self.assertNotIn("'unsafe-inline'", headers['Content-Security-Policy'])
        for text in ('arm64.dmg', 'x86_64.dmg', 'Applications', 'Mac 测试版说明',
                     '尚未完成 Developer ID 签名与 Apple 公证', 'Windows 版实拍'):
            self.assertIn(text, html)

    def test_unique_ids_and_all_internal_anchors_resolve(self):
        page = PageParser()
        page.feed(self.request('/')[2].decode('utf-8'))
        ids = [attrs['id'] for _, attrs in page.tags if 'id' in attrs]
        self.assertEqual(len(ids), len(set(ids)))
        for tag, attrs in page.tags:
            target = attrs.get('href', '')
            if tag == 'a' and target.startswith('#'):
                self.assertIn(target[1:], ids)

    def test_sitemap_contains_only_public_canonical_home(self):
        status, headers, body = self.request('/sitemap.xml')
        self.assertEqual(status, 200)
        self.assertIn('application/xml', headers['Content-Type'])
        root = ElementTree.fromstring(body)
        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        self.assertEqual([node.text for node in root.findall('s:url/s:loc', ns)],
                         ['https://deskpools.com/'])
        self.assertNotIn(b'admin', body)
        self.assertNotIn(b'download', body)

    def test_admin_remains_hidden_and_noindex(self):
        status, _, body = self.request('/robots.txt')
        self.assertEqual(status, 200)
        for directive in (b'Disallow: /admin', b'Disallow: /api/admin/',
                          b'Sitemap: https://deskpools.com/sitemap.xml'):
            self.assertIn(directive, body)
        for path in ('/admin', '/admin/', '/api/admin/downloads', '/static/admin.js'):
            status, headers, _ = self.request(path)
            self.assertEqual(status, 401 if path.startswith('/api/') else 200)
            self.assertIn('noindex', headers['X-Robots-Tag'])


if __name__ == '__main__':
    unittest.main()
