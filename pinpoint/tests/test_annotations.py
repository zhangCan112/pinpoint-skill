"""Tests for annotations.py — inline annotation attributes on HTML elements.

Adapted from ppt-master's proven SVG mechanism (scripts/svg_editor/annotations.py):
annotations are data-edit-* attributes ON the target element; the element id is
only a session-local address label, not a persistent anchor.
"""

import xml.etree.ElementTree as ET

import pytest

from annotations import (
    ANNOTATABLE_TAGS,
    assign_temp_ids,
    find_by_id,
    parse_annotations,
    remove_annotation,
    set_annotation,
    set_text,
    strip_unused_temp_ids,
)


def parse(text: str) -> ET.Element:
    return ET.fromstring(text)


DOC = """<html><body>
<h1>Title</h1>
<p>Hello <strong>world</strong>, first paragraph.</p>
<ul><li>one</li><li>two</li></ul>
<pre><code><span class="line">a = 1</span></code></pre>
<img src="assets/chart.png"/>
<hr/>
</body></html>"""


class TestAssignTempIds:
    def test_block_elements_get_temp_ids(self):
        root = parse(DOC)
        assign_temp_ids(root)
        ids = [e.get('id') for e in root.iter() if e.get('id')]
        # h1, p, ul, li, li, pre, img, hr = 8 block elements
        assert len(ids) == 8

    def test_inline_elements_get_no_ids(self):
        root = parse(DOC)
        assign_temp_ids(root)
        strong = [e for e in root.iter() if e.tag == 'strong'][0]
        assert strong.get('id') is None

    def test_ids_are_deterministic_and_prefixed(self):
        root_a = parse(DOC)
        root_b = parse(DOC)
        assign_temp_ids(root_a)
        assign_temp_ids(root_b)
        ids_a = [e.get('id') for e in root_a.iter() if e.get('id')]
        ids_b = [e.get('id') for e in root_b.iter() if e.get('id')]
        assert ids_a == ids_b
        assert all(i.startswith('_pp_') for i in ids_a)

    def test_stale_temp_ids_are_cleared_before_assign(self):
        root = parse('<html><body><p id="_pp_9">a</p><p>b</p></body></html>')
        assign_temp_ids(root)
        ids = [e.get('id') for e in root.iter() if e.tag == 'p']
        assert ids == ['_pp_0', '_pp_1']

    def test_real_ids_are_preserved(self):
        root = parse('<html><body><p id="intro">a</p><p>b</p></body></html>')
        assign_temp_ids(root)
        intro = find_by_id(root, 'intro')
        assert intro is not None and intro.text == 'a'

    def test_annotatable_tags_cover_common_blocks(self):
        for tag in ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li',
                    'blockquote', 'pre', 'table', 'tr', 'img', 'hr', 'figure',
                    'figcaption', 'dl', 'dt', 'dd', 'div', 'section', 'article'):
            assert tag in ANNOTATABLE_TAGS


class TestAnnotationRoundtrip:
    def test_set_and_parse(self):
        root = parse(DOC)
        assign_temp_ids(root)
        target = find_by_id(root, '_pp_0')
        assert set_annotation(root, '_pp_0', 'make this shorter') is True
        assert target.get('data-edit-target') == 'true'
        assert target.get('data-edit-annotation') == 'make this shorter'
        anns = parse_annotations(root)
        assert len(anns) == 1
        assert anns[0]['element_id'] == '_pp_0'
        assert anns[0]['tag'] == 'h1'
        assert anns[0]['annotation'] == 'make this shorter'
        assert anns[0]['preview'] == 'Title'

    def test_set_on_missing_id_returns_false(self):
        root = parse(DOC)
        assign_temp_ids(root)
        assert set_annotation(root, '_pp_999', 'x') is False

    def test_remove(self):
        root = parse(DOC)
        assign_temp_ids(root)
        set_annotation(root, '_pp_0', 'x')
        assert remove_annotation(root, '_pp_0') is True
        assert parse_annotations(root) == []
        elem = find_by_id(root, '_pp_0')
        assert 'data-edit-target' not in elem.attrib
        assert 'data-edit-annotation' not in elem.attrib

    def test_parse_skips_elements_without_marker(self):
        root = parse(DOC)
        assign_temp_ids(root)
        assert parse_annotations(root) == []


class TestSetText:
    def test_set_text_on_text_only_element(self):
        root = parse('<html><body><p>old</p></body></html>')
        assign_temp_ids(root)
        ok, reason = set_text(root, '_pp_0', 'new text')
        assert ok and reason is None
        assert find_by_id(root, '_pp_0').text == 'new text'

    def test_set_text_refuses_element_children(self):
        root = parse('<html><body><p>text <strong>bold</strong></p></body></html>')
        assign_temp_ids(root)
        ok, reason = set_text(root, '_pp_0', 'flatten')
        assert ok is False
        assert reason == 'has-element-children'

    def test_set_text_missing_element(self):
        root = parse('<html><body><p>x</p></body></html>')
        ok, reason = set_text(root, 'nope', 'y')
        assert ok is False
        assert reason == 'not-found'


class TestStripUnusedTempIds:
    def test_strips_unannotated_keeps_annotated(self):
        root = parse(DOC)
        assign_temp_ids(root)
        set_annotation(root, '_pp_0', 'fix title')
        set_annotation(root, '_pp_3', 'fix list item')
        strip_unused_temp_ids(root, set())
        remaining = [e.get('id') for e in root.iter() if e.get('id')]
        assert remaining == ['_pp_0', '_pp_3']

    def test_keep_ids_argument_is_protected(self):
        root = parse(DOC)
        assign_temp_ids(root)
        strip_unused_temp_ids(root, keep_ids={'_pp_2'})
        remaining = [e.get('id') for e in root.iter() if e.get('id')]
        assert remaining == ['_pp_2']

    def test_annotated_without_marker_attr_still_kept(self):
        # Defence in depth: element carrying data-edit-target must never lose
        # its id even if the caller passed an empty keep set.
        root = parse('<html><body><p id="_pp_0" data-edit-target="true" data-edit-annotation="x">a</p></body></html>')
        strip_unused_temp_ids(root, set())
        assert root.find('.//p').get('id') == '_pp_0'
