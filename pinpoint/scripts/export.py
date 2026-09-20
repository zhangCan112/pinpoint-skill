#!/usr/bin/env python3
"""
pinpoint - Export the HTML IR back to clean markdown.

Drops all runtime metadata (annotation attributes, session ids) and emits
the content only. Use when a document has converged and the user wants a
plain markdown deliverable.

Usage:
    python scripts/export.py <doc.html> [-o OUT.md]

    -o OUT.md   write to a file (default: stdout)

Dependencies:
    None (only uses standard library)
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from annotations import parse_doc_text

INLINE_WRAP = {
    'strong': '**', 'b': '**',
    'em': '*', 'i': '*',
    'del': '~~', 's': '~~',
    'code': '`',
}
UNWRAP_TAGS = frozenset({
    'div', 'section', 'article', 'header', 'footer', 'main', 'nav', 'aside',
    'body', 'html',
})


def _inline(node, _skip_lists: bool = False) -> str:
    parts = []
    if node.text:
        parts.append(node.text)
    for child in node:
        tag = child.tag.lower()
        if tag in ('ul', 'ol') and _skip_lists:
            pass  # nested lists are rendered as blocks by the list handler
        elif tag in INLINE_WRAP:
            inner = _inline(child)
            if inner:
                parts.append(INLINE_WRAP[tag] + inner + INLINE_WRAP[tag])
        elif tag == 'a':
            href = child.get('href', '')
            parts.append(f'[{_inline(child)}]({href})')
        elif tag == 'img':
            parts.append(f'![{child.get("alt", "")}]({child.get("src", "")})')
        elif tag == 'br':
            parts.append('\n')
        else:
            parts.append(_inline(child))
        if child.tail:
            parts.append(child.tail)
    return ''.join(parts)


def _code_block_md(pre) -> str:
    lang = pre.get('data-lang', '')
    code = pre.find('code')
    container = code if code is not None else pre
    lines = [
        ''.join(span.itertext()).replace('\u00a0', '')
        for span in container
        if span.tag.lower() == 'span'
    ]
    if not lines:
        raw = (container.text or '').strip('\n')
        lines = raw.split('\n') if raw else []
    return f'```{lang}\n' + '\n'.join(lines) + '\n```'


def _table_md(table) -> str:
    rows = list(table.iter('tr'))
    if not rows:
        return ''
    out = []

    def row_md(tr):
        cells = [c for c in tr if c.tag.lower() in ('td', 'th')]
        return '| ' + ' | '.join(_inline(c).strip() for c in cells) + ' |'

    out.append(row_md(rows[0]))
    width = len(rows[0])
    out.append('|' + ' --- |' * width)
    for tr in rows[1:]:
        out.append(row_md(tr))
    return '\n'.join(out)


def _list_md(elem, indent: str = '') -> str:
    ordered = elem.tag.lower() == 'ol'
    lines: list = []
    index = 1
    for li in elem:
        if li.tag.lower() != 'li':
            continue
        marker = f'{index}. ' if ordered else '- '
        content = _inline(li, _skip_lists=True).strip()
        lines.append(indent + marker + content)
        for child in li:
            if child.tag.lower() in ('ul', 'ol'):
                lines.append(_list_md(child, indent + '  '))
        if ordered:
            index += 1
    return '\n'.join(lines)


def _blocks_md(elem, out: list, indent: str = '') -> None:
    for child in elem:
        tag = child.tag.lower()
        if tag in UNWRAP_TAGS:
            _blocks_md(child, out, indent)
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            out.append(indent + '#' * int(tag[1]) + ' ' + _inline(child).strip())
        elif tag == 'p':
            text = _inline(child).strip()
            if text:
                out.append(indent + text)
        elif tag in ('ul', 'ol'):
            md = _list_md(child, indent)
            if md:
                out.append(md)
        elif tag == 'blockquote':
            inner: list = []
            _blocks_md(child, inner)
            for block in inner:
                for line in block.split('\n'):
                    out.append(('> ' + line).rstrip())
        elif tag == 'pre':
            out.append(_code_block_md(child))
        elif tag == 'table':
            md = _table_md(child)
            if md:
                out.append(md)
        elif tag == 'hr':
            out.append(indent + '---')
        elif tag == 'figure':
            for img in child.iter('img'):
                out.append(f'![{img.get("alt", "")}]({img.get("src", "")})')
            caption = child.find('figcaption')
            if caption is not None and _inline(caption).strip():
                out.append('*' + _inline(caption).strip() + '*')
        else:
            text = _inline(child).strip()
            if text:
                out.append(indent + text)


def export_md_from_body(body) -> str:
    out: list = []
    _blocks_md(body, out)
    return '\n\n'.join(out) + '\n'


def export_document(doc_path: Path) -> str:
    root = parse_doc_text(doc_path.read_text(encoding='utf-8-sig'))
    return export_md_from_body(root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Export a pinpoint HTML doc to clean markdown',
    )
    parser.add_argument('doc', help='Path to the .html doc')
    parser.add_argument('-o', '--output', help='Write to this file (default: stdout)')
    return parser


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError, ValueError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    doc_path = Path(args.doc).resolve()
    if not doc_path.is_file():
        print(f'Error: doc not found: {doc_path}', file=sys.stderr)
        return 1

    try:
        md = export_document(doc_path)
    except Exception as exc:  # ET.ParseError and friends
        print(f'Error: failed to parse {doc_path.name}: {exc}', file=sys.stderr)
        return 1

    if args.output:
        Path(args.output).write_text(md, encoding='utf-8')
    else:
        print(md, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
