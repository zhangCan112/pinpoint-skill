"""Tests for export.py - convert the HTML IR back to clean markdown.

The exported file must contain the content only: annotation attributes,
session ids, and runtime metadata are dropped.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import export as export_mod
from export import export_document, export_md_from_body


BODY = """<h1>Plan</h1>
<p>Hello <strong>world</strong> with <em>emphasis</em> and <code>inline()</code> and <a href="http://x">a link</a>.</p>
<ul><li>one</li><li>two <strong>bold</strong></li></ul>
<ol><li>first</li><li>second</li></ol>
<blockquote><p>quoted wisdom</p></blockquote>
<pre data-lang="python"><code><span class="line">a = 1</span><span class="line">b = 2</span></code></pre>
<table><thead><tr><th>name</th><th>value</th></tr></thead><tbody><tr><td>a</td><td>1</td></tr></tbody></table>
<hr/>
<p><img src="../assets/pic.png" alt="pic"/></p>
<h2 id="_pp_0" data-edit-target="true" data-edit-annotation="reword this">Annotated Heading</h2>
<p id="_pp_1" data-edit-target="true" data-edit-annotation="中文注解">带注解的段落。</p>"""


def export_body(body_html: str) -> str:
    root = ET.fromstring(f'<html><body>{body_html}</body></html>')
    return export_md_from_body(root.find('body'))


class TestExportBody:
    def test_headings(self):
        md = export_body(BODY)
        assert '# Plan' in md
        assert '## Annotated Heading' in md

    def test_inline_formatting(self):
        md = export_body(BODY)
        assert '**world**' in md
        assert '*emphasis*' in md
        assert '`inline()`' in md
        assert '[a link](http://x)' in md

    def test_lists(self):
        md = export_body(BODY)
        assert '- one' in md
        assert '- two **bold**' in md
        assert '1. first' in md
        assert '2. second' in md

    def test_blockquote(self):
        md = export_body(BODY)
        assert '> quoted wisdom' in md

    def test_fenced_code_with_language(self):
        md = export_body(BODY)
        assert '```python' in md
        assert 'a = 1\nb = 2' in md

    def test_table(self):
        md = export_body(BODY)
        assert '| name | value |' in md
        assert '| --- | --- |' in md
        assert '| a | 1 |' in md

    def test_hr(self):
        assert '\n---\n' in export_body(BODY)

    def test_image(self):
        assert '![pic](../assets/pic.png)' in export_body(BODY)

    def test_list_items_adjacent_lines(self):
        body = '<ul><li>one</li><li>two</li></ul>'
        md = export_body(body)
        assert '- one\n- two' in md

    def test_nested_list_indented(self):
        body = '<ul><li>outer<ul><li>inner</li></ul></li></ul>'
        md = export_body(body)
        assert '- outer\n  - inner' in md

    def test_annotation_metadata_dropped_content_kept(self):
        md = export_body(BODY)
        assert 'data-edit' not in md
        assert 'reword this' not in md
        assert '中文注解' not in md
        assert '带注解的段落。' in md

    def test_session_ids_dropped(self):
        assert '_pp_' not in export_body(BODY)

    def test_empty_code_lines_preserved(self):
        body = '<pre><code><span class="line">a</span><span class="line">\u00a0</span><span class="line">b</span></code></pre>'
        md = export_body(body)
        assert 'a\n\nb' in md


class TestExportDocument:
    def test_full_doc_file(self, tmp_path: Path):
        doc = tmp_path / 'doc.html'
        doc.write_text(
            '<!DOCTYPE html>\n<html><head><meta charset="utf-8"/><title>t</title></head>'
            f'<body>{BODY}</body></html>',
            encoding='utf-8',
        )
        md = export_document(doc)
        assert '# Plan' in md and '```python' in md


class TestCli:
    def test_stdout_default(self, tmp_path, capsys):
        doc = tmp_path / 'doc.html'
        doc.write_text(
            '<!DOCTYPE html>\n<html><body><p>hello</p></body></html>',
            encoding='utf-8',
        )
        rc = export_mod.main([str(doc)])
        assert rc == 0
        assert 'hello' in capsys.readouterr().out

    def test_output_file(self, tmp_path):
        doc = tmp_path / 'doc.html'
        doc.write_text(
            '<!DOCTYPE html>\n<html><body><p>saved</p></body></html>',
            encoding='utf-8',
        )
        out = tmp_path / 'out.md'
        rc = export_mod.main([str(doc), '-o', str(out)])
        assert rc == 0
        assert 'saved' in out.read_text(encoding='utf-8')
