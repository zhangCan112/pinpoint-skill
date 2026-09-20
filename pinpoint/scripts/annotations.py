#!/usr/bin/env python3
"""
pinpoint - Inline annotation attributes on HTML elements.

Annotations are data attributes ON the target element, adapted from
ppt-master's proven SVG mechanism (data-edit-target / data-edit-annotation).
The element id is only a session-local address label, never a persistent
anchor: the annotation itself travels with the element through any edit.

Usage:
    (library module - imported by render.py, server.py, check.py)

Dependencies:
    None (only uses standard library)
"""

import xml.etree.ElementTree as ET
from typing import Optional

ATTR_TARGET = 'data-edit-target'
ATTR_ANNOTATION = 'data-edit-annotation'
TEMP_PREFIX = '_pp_'

# Block-level elements that can carry an annotation. Inline elements
# (strong/em/code/a/span) are addressed through their block ancestor.
ANNOTATABLE_TAGS = frozenset({
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'blockquote', 'pre', 'table', 'tr', 'td', 'th',
    'img', 'hr', 'figure', 'figcaption',
    'div', 'section', 'article', 'header', 'footer', 'main', 'aside', 'nav',
})


def strip_doctype(text: str) -> str:
    """Drop the leading <!DOCTYPE ...> so ET.fromstring accepts the doc."""
    if text.startswith('<!DOCTYPE'):
        newline = text.find('\n')
        if newline != -1:
            return text[newline + 1:]
        gt = text.find('>')
        return text[gt + 1:] if gt != -1 else text
    return text


def parse_doc_text(text: str) -> ET.Element:
    """Parse a pinpoint document (HTML IR) into an ElementTree root."""
    return ET.fromstring(strip_doctype(text))


def assign_temp_ids(root: ET.Element) -> None:
    """Assign deterministic temp ids (_pp_0, _pp_1, ...) to annotatable blocks.

    Clears leftover _pp_N ids from previous sessions first, so numbering does
    not shift when elements are added or removed between sessions.
    """
    for elem in root.iter():
        eid = elem.get('id', '')
        if eid.startswith(TEMP_PREFIX):
            elem.attrib.pop('id', None)

    counter = 0
    for elem in root.iter():
        tag = elem.tag.split('}', 1)[-1]
        if elem.get('id') is None and tag in ANNOTATABLE_TAGS:
            elem.set('id', f'{TEMP_PREFIX}{counter}')
            counter += 1


def find_by_id(root: ET.Element, element_id: str) -> Optional[ET.Element]:
    """Find an element by its id attribute in the tree."""
    for elem in root.iter():
        if elem.get('id') == element_id:
            return elem
    return None


def _text_preview(elem: ET.Element, limit: int = 80) -> str:
    """First chunk of the element's visible text, for to-do listings."""
    parts = [t for t in elem.itertext() if t and t.strip()]
    text = ' '.join(part.strip() for part in parts)
    return text[:limit]


def parse_annotations(root: ET.Element) -> list:
    """Extract all annotations from a document tree.

    Each item: {element_id, tag, annotation, preview}.
    """
    annotations = []
    for elem in root.iter():
        if elem.get(ATTR_TARGET) == 'true':
            tag = elem.tag.split('}', 1)[-1]
            annotations.append({
                'element_id': elem.get('id', '(no id)'),
                'tag': tag,
                'annotation': elem.get(ATTR_ANNOTATION, ''),
                'preview': _text_preview(elem),
            })
    return annotations


def set_annotation(root: ET.Element, element_id: str, annotation: str) -> bool:
    """Add or update an annotation on an element. Returns True if found."""
    elem = find_by_id(root, element_id)
    if elem is None:
        return False
    elem.set(ATTR_TARGET, 'true')
    elem.set(ATTR_ANNOTATION, annotation)
    return True


def remove_annotation(root: ET.Element, element_id: str) -> bool:
    """Remove annotation attributes from an element. Returns True if found."""
    elem = find_by_id(root, element_id)
    if elem is None:
        return False
    elem.attrib.pop(ATTR_TARGET, None)
    elem.attrib.pop(ATTR_ANNOTATION, None)
    return True


def set_text(
    root: ET.Element, element_id: str, text: str,
) -> tuple:
    """Direct-edit channel: replace an element's text content. Returns (ok, reason).

    Refuses elements with element children: overwriting their .text would
    orphan the children and destroy inline formatting. Edit the innermost
    text-only element (or annotate) instead.
    """
    elem = find_by_id(root, element_id)
    if elem is None:
        return False, 'not-found'
    if len(elem) > 0:
        return False, 'has-element-children'
    elem.text = text
    return True, None


def strip_unused_temp_ids(root: ET.Element, keep_ids: set) -> None:
    """Drop transient _pp_N ids except those in keep_ids and any element
    still carrying an annotation (its id is the AI's locator).
    """
    protected = set(keep_ids)
    for elem in root.iter():
        if elem.get(ATTR_TARGET) == 'true':
            eid = elem.get('id')
            if eid:
                protected.add(eid)
    for elem in root.iter():
        eid = elem.get('id', '')
        if eid.startswith(TEMP_PREFIX) and eid not in protected:
            elem.attrib.pop('id', None)
