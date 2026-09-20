"""Tests for render.py - normalize AI responses into the HTML intermediate
representation (IR): markdown -> block html, raw html -> well-formed fragment,
image -> wrapped page. IR at rest is clean: no ids, no annotations.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import render
from render import (
    doc_document,
    image_page,
    normalize_html_fragment,
    render_blocks,
    slugify,
    split_blocks,
    xml_safe,
)


def body_parses(body_html: str) -> ET.Element:
    return ET.fromstring(f'<html><body>{body_html}</body></html>')


class TestSplitBlocks:
    def test_kinds(self):
        md = "\n".join([
            "# Title",
            "",
            "Intro paragraph.",
            "",
            "- one",
            "- two",
            "",
            "1. first",
            "2. second",
            "",
            "> quoted",
            "> more",
            "",
            "```python",
            "a = 1",
            "",
            "b = 2",
            "```",
            "",
            "| a | b |",
            "|---|---|",
            "| 1 | 2 |",
            "",
            "---",
            "",
            "Final para.",
        ])
        blocks = split_blocks(md)
        kinds = [b.kind for b in blocks]
        assert kinds == [
            'heading', 'para', 'ul', 'ol', 'quote', 'code', 'table', 'hr', 'para',
        ]

    def test_loose_list_stays_one_block(self):
        md = "- one\n\n- two\n\n- three\n"
        blocks = split_blocks(md)
        assert len(blocks) == 1
        assert blocks[0].kind == 'ul'

    def test_code_fence_spans_blank_lines(self):
        md = "```\na\n\nb\n```\n"
        blocks = split_blocks(md)
        assert len(blocks) == 1
        assert blocks[0].kind == 'code'

    def test_captures_language(self):
        blocks = split_blocks("```js\nx\n```\n")
        assert blocks[0].lang == 'js'


class TestRenderBlocks:
    def test_basic_structure(self):
        md = "# Title\n\nHello **world**.\n"
        body = render_blocks(md)
        assert '<h1>Title</h1>' in body
        assert '<p>Hello <strong>world</strong>.</p>' in body

    def test_no_ids_at_rest(self):
        body = render_blocks("# T\n\n- a\n- b\n\n```python\nx = 1\n```\n")
        assert ' id=' not in body

    def test_code_lines_are_spans(self):
        body = render_blocks("```python\na = 1\nb < 2\n```")
        assert '<pre' in body and 'data-lang="python"' in body
        assert '<span class="line">a = 1</span>' in body
        assert 'b &lt; 2' in body
        assert body.count('<span class="line">') == 2

    def test_empty_code_line_has_placeholder(self):
        body = render_blocks("```\na\n\nb\n```")
        assert body.count('<span class="line">') == 3

    def test_table_renders(self):
        md = "| a | b |\n|---|---|\n| 1 | 2 |\n"
        body = render_blocks(md)
        assert '<table>' in body and '<td>1</td>' in body

    def test_inline_markdown_in_paragraph(self):
        body = render_blocks("See [docs](http://x) and `code`.\n")
        assert '<a href="http://x">docs</a>' in body
        assert '<code>code</code>' in body

    def test_output_is_xml_safe(self):
        md = "AT&T <tag> &nbsp; &mdash; end\n"
        body = render_blocks(md)
        body_parses(body)

    def test_blockquote_renders(self):
        body = render_blocks("> wisdom\n")
        assert '<blockquote>' in body


class TestXmlSafe:
    def test_named_entities_become_numeric(self):
        assert xml_safe('a&nbsp;b') == 'a&#160;b'
        assert xml_safe('x&mdash;y') == 'x&#8212;y'

    def test_xml_native_entities_untouched(self):
        assert xml_safe('a&amp;b') == 'a&amp;b'
        assert xml_safe('a&lt;b') == 'a&lt;b'

    def test_bare_ampersand_escaped(self):
        assert xml_safe('AT&T') == 'AT&amp;T'

    def test_numeric_refs_untouched(self):
        assert xml_safe('&#160;') == '&#160;'


class TestNormalizeHtmlFragment:
    def test_fixes_void_elements_and_closes_all_tags(self):
        raw = '<p>two<br>three<img src="a.png">'
        out = normalize_html_fragment(raw)
        body_parses(out)
        assert '<br/>' in out
        assert '<img src="a.png"/>' in out
        assert 'three' in out

    def test_strips_script(self):
        out = normalize_html_fragment('<p>ok</p><script>alert(1)</script>')
        assert 'script' not in out and '<p>ok</p>' in out

    def test_strips_event_handlers(self):
        out = normalize_html_fragment('<p onclick="evil()">hi</p>')
        assert 'onclick' not in out and '<p>hi</p>' in out

    def test_strips_js_urls(self):
        out = normalize_html_fragment('<a href="javascript:evil()">x</a>')
        assert 'javascript' not in out

    def test_strips_iframe_object_embed(self):
        out = normalize_html_fragment('<iframe src="x"></iframe><object></object>')
        assert 'iframe' not in out and 'object' not in out

    def test_keeps_style_tag(self):
        out = normalize_html_fragment('<p>a</p><style>p{color:red}</style>')
        assert '<style>' in out

    def test_output_parses(self):
        raw = '<div><p>x<div>y</div><hr><img src="i.png"></div>'
        body_parses(normalize_html_fragment(raw))


class TestImagePage:
    def test_wraps_image(self):
        body = image_page('assets/chart.png', 'Chart')
        assert '<figure>' in body
        assert 'src="assets/chart.png"' in body
        assert '<figcaption>Chart</figcaption>' in body


class TestDocDocument:
    def test_full_document_parses_with_doctype(self):
        doc = doc_document('My Doc', '<p>hello</p>')
        assert doc.startswith('<!DOCTYPE html>')
        assert '<title>My Doc</title>' in doc
        # ET must parse the document body (DOCTYPE is skipped by expat).
        content = doc[len('<!DOCTYPE html>'):]
        root = ET.fromstring(content)
        assert root.find('.//p').text == 'hello'


class TestSlugify:
    def test_ascii(self):
        assert slugify('My Report!') == 'my-report'

    def test_chinese_falls_back_to_doc(self):
        assert slugify('设计文档') == 'doc'

    def test_mixed(self):
        assert slugify('Plan 方案 v2') == 'plan-v2'


class TestCli:
    def test_md_input_writes_doc(self, tmp_path: Path):
        src = tmp_path / 'notes.md'
        src.write_text('# Hi\n\nBody\n', encoding='utf-8')
        rc = render.main(['--out', str(tmp_path), str(src)])
        assert rc == 0
        out = tmp_path / 'docs' / 'notes.html'
        assert out.exists()
        assert '<h1>Hi</h1>' in out.read_text(encoding='utf-8')

    def test_html_input_normalized(self, tmp_path: Path):
        src = tmp_path / 'page.html'
        src.write_text('<p>one<p>two<br>end', encoding='utf-8')
        rc = render.main(['--out', str(tmp_path), str(src)])
        assert rc == 0
        out = tmp_path / 'docs' / 'page.html'
        text = out.read_text(encoding='utf-8')
        assert '<br/>' in text and '<p>two' in text

    def test_image_input_copies_asset(self, tmp_path: Path):
        src = tmp_path / 'chart.png'
        src.write_bytes(b'\x89PNG fake')
        rc = render.main(['--out', str(tmp_path), str(src)])
        assert rc == 0
        out = tmp_path / 'docs' / 'chart.html'
        assert out.exists()
        asset = tmp_path / 'assets' / 'chart.png'
        assert asset.read_bytes() == b'\x89PNG fake'
        assert 'src="../assets/chart.png"' in out.read_text(encoding='utf-8')

    def test_md_image_referenced_copied(self, tmp_path: Path):
        img = tmp_path / 'pic.png'
        img.write_bytes(b'PNG2')
        src = tmp_path / 'doc.md'
        src.write_text('# T\n\n![pic](pic.png)\n', encoding='utf-8')
        rc = render.main(['--out', str(tmp_path), str(src)])
        assert rc == 0
        assert (tmp_path / 'assets' / 'pic.png').read_bytes() == b'PNG2'
        out = (tmp_path / 'docs' / 'doc.html').read_text(encoding='utf-8')
        assert 'src="../assets/pic.png"' in out

    def test_bom_input_is_tolerated(self, tmp_path: Path):
        src = tmp_path / 'bom.md'
        src.write_bytes('\ufeff# Title\n\nBody\n'.encode('utf-8'))
        rc = render.main(['--out', str(tmp_path), str(src)])
        assert rc == 0
        out = (tmp_path / 'docs' / 'bom.html').read_text(encoding='utf-8')
        assert '<h1>Title</h1>' in out

    def test_name_override(self, tmp_path: Path):
        src = tmp_path / 'notes.md'
        src.write_text('x\n', encoding='utf-8')
        rc = render.main(['--out', str(tmp_path), '--name', 'review-round-1', str(src)])
        assert rc == 0
        assert (tmp_path / 'docs' / 'review-round-1.html').exists()
