#!/usr/bin/env python3
"""
pinpont - Render an AI response into the HTML intermediate representation.

Intake formats (all normalized to the same IR):
    *.md / *.markdown  -> block-rendered HTML (code fenced blocks get
                          per-line <span class="line"> for line-level anchors)
    *.html / *.htm     -> sanitized, well-formed XHTML fragment
    image files        -> wrapped in a <figure> page, asset copied

IR at rest is clean: no element ids, no annotation attributes. The editor
assigns session-local ids only while a preview is running.

Usage:
    python scripts/render.py <input> [--name NAME] [--out DIR]

    --out DIR   pinpont workspace root (default: ./.pinpont); docs go to
                DIR/docs/<name>.html, images to DIR/assets/

Dependencies:
    markdown, beautifulsoup4 (CLI/server only; check.py stays stdlib-only)
"""

import argparse
import html as html_mod
import html.entities
import re
import shutil
import sys
from collections import namedtuple
from pathlib import Path
from typing import Optional

import markdown as md_lib
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

MARKDOWN_EXTENSIONS = ['tables']

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp'}

VOID_ELEMENTS = frozenset({
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr',
})
STRIP_TAGS = frozenset({'script', 'iframe', 'object', 'embed'})

XML_NATIVE_ENTITIES = {'amp', 'lt', 'gt', 'quot', 'apos'}

Block = namedtuple('Block', 'kind lang lines')

_FENCE_RE = re.compile(r'^(```|~~~)\s*(\S*)\s*$')
_HEADING_RE = re.compile(r'^#{1,6}\s')
_HR_RE = re.compile(r'^ {0,3}(-{3,}|\*{3,}|_{3,})$')
_UL_RE = re.compile(r'^\s*[-*+]\s')
_OL_RE = re.compile(r'^\s*\d+[.)]\s')
_QUOTE_RE = re.compile(r'^\s*>\s?')
_TABLE_SEP_RE = re.compile(r'^\s*\|?[\s:|-]+\|?\s*$')

_ENTITY_RE = re.compile(r'&([a-zA-Z][a-zA-Z0-9]*);')
_BARE_AMP_RE = re.compile(r'&(?!(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#[xX][0-9a-fA-F]+);)')
_MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')


# ---------------------------------------------------------------------------
# XML safety
# ---------------------------------------------------------------------------

def _entity_sub(match: re.Match) -> str:
    name = match.group(1)
    if name.lower() in XML_NATIVE_ENTITIES:
        return match.group(0)
    codepoint = html.entities.html5.get(name + ';') or html.entities.html5.get(name)
    if codepoint:
        return f'&#{ord(codepoint)};'
    return match.group(0)


def xml_safe(text: str) -> str:
    """Make an HTML fragment parseable by ElementTree.

    Escapes bare ampersands and converts named HTML entities to numeric
    references; XML-native entities are left untouched.
    """
    text = _BARE_AMP_RE.sub('&amp;', text)
    return _ENTITY_RE.sub(_entity_sub, text)


# ---------------------------------------------------------------------------
# Markdown block splitting
# ---------------------------------------------------------------------------

def _classify(line: str) -> Optional[str]:
    if _FENCE_RE.match(line):
        return 'code'
    if _HEADING_RE.match(line):
        return 'heading'
    if _HR_RE.match(line):
        return 'hr'
    if _QUOTE_RE.match(line):
        return 'quote'
    if _UL_RE.match(line):
        return 'ul'
    if _OL_RE.match(line):
        return 'ol'
    return None


def _is_table_start(lines: list, i: int) -> bool:
    if '|' not in lines[i]:
        return False
    if i + 1 >= len(lines):
        return False
    sep = lines[i + 1]
    return bool(_TABLE_SEP_RE.match(sep)) and '-' in sep


def split_blocks(text: str) -> list:
    """Split markdown source into top-level blocks.

    Loose lists and quotes (blank line between items) stay one block.
    Fenced code blocks span blank lines. Everything else splits on
    blank lines.
    """
    lines = text.replace('\r\n', '\n').split('\n')
    blocks: list = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            lang = fence.group(2)
            collected = []
            i += 1
            while i < n and not _FENCE_RE.match(lines[i]):
                collected.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            blocks.append(Block('code', lang, collected))
            continue

        if _is_table_start(lines, i):
            collected = []
            while i < n and '|' in lines[i]:
                collected.append(lines[i])
                i += 1
            blocks.append(Block('table', '', collected))
            continue

        kind = _classify(line)
        if kind in ('heading', 'hr'):
            blocks.append(Block(kind, '', [line]))
            i += 1
            continue

        if kind in ('ul', 'ol', 'quote'):
            collected = []
            while i < n:
                if lines[i].strip() and _classify(lines[i]) != kind:
                    break
                if not lines[i].strip():
                    # Blank line: continue only if the next non-blank line
                    # belongs to the same construct (loose list / quote).
                    j = i + 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j >= n or _classify(lines[j]) != kind:
                        break
                collected.append(lines[i])
                i += 1
            blocks.append(Block(kind, '', collected))
            continue

        # Paragraph: absorb contiguous non-blank lines (markdown handles
        # setext underlines and inline content inside the chunk).
        collected = []
        while i < n and lines[i].strip():
            collected.append(lines[i])
            i += 1
        blocks.append(Block('para', '', collected))

    return blocks


# ---------------------------------------------------------------------------
# Block rendering
# ---------------------------------------------------------------------------

def _render_code(block: Block) -> str:
    spans = []
    for line in block.lines:
        text = html_mod.escape(line, quote=False) or '\u00a0'
        spans.append(f'<span class="line">{text}</span>')
    lang_attr = f' data-lang="{html_mod.escape(block.lang, quote=True)}"' if block.lang else ''
    return f'<pre{lang_attr}><code>{"".join(spans)}</code></pre>'


def _render_markdown_block(block: Block) -> str:
    rendered = md_lib.markdown(
        '\n'.join(block.lines), extensions=MARKDOWN_EXTENSIONS,
    )
    # markdown passes inline raw HTML through untouched; normalize absorbs
    # it into well-formed elements, sanitizes, and guarantees ET-parseable
    # output.
    return normalize_html_fragment(rendered)


def render_block(block: Block) -> str:
    if block.kind == 'code':
        return _render_code(block)
    if block.kind == 'hr':
        return '<hr/>'
    return _render_markdown_block(block)


def render_blocks(text: str) -> str:
    """Render markdown source to a clean HTML body (no ids)."""
    return '\n'.join(render_block(b) for b in split_blocks(text))


# ---------------------------------------------------------------------------
# Raw HTML intake: sanitize + XHTML-serialize via BeautifulSoup
# ---------------------------------------------------------------------------

def _serialize_xhtml(node) -> str:
    if isinstance(node, Comment):
        return ''
    if isinstance(node, NavigableString):
        return html_mod.escape(str(node), quote=False)
    if not isinstance(node, Tag):
        return ''
    name = node.name.lower()
    attrs = ''.join(
        f' {key}="{html_mod.escape(value if isinstance(value, str) else " ".join(value), quote=True)}"'
        for key, value in node.attrs.items()
        if isinstance(value, (str, list))
    )
    if name in VOID_ELEMENTS:
        return f'<{name}{attrs}/>'
    inner = ''.join(_serialize_xhtml(child) for child in node.children)
    return f'<{name}{attrs}>{inner}</{name}>'


def normalize_html_fragment(raw: str) -> str:
    """Sanitize an HTML fragment and serialize it as well-formed XHTML.

    Removes scripts/iframes/objects/embeds, event-handler attributes, and
    javascript: URLs. Keeps the rest (including <style>).
    """
    soup = BeautifulSoup(raw, 'html.parser')

    for tag in soup.find_all(list(STRIP_TAGS)):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith('on'):
                del tag.attrs[attr]
        for url_attr in ('href', 'src'):
            value = tag.attrs.get(url_attr)
            if isinstance(value, str) and value.strip().lower().startswith('javascript:'):
                del tag.attrs[url_attr]

    container = soup.body if soup.body is not None else soup
    parts = []
    for child in container.children:
        if isinstance(child, Comment):
            continue
        parts.append(_serialize_xhtml(child))
    return ''.join(parts)


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

def image_page(src: str, title: str) -> str:
    safe_src = html_mod.escape(src, quote=True)
    safe_title = html_mod.escape(title, quote=False)
    return (
        f'<figure><img src="{safe_src}"/>'
        f'<figcaption>{safe_title}</figcaption></figure>'
    )


def doc_document(title: str, body: str) -> str:
    safe_title = html_mod.escape(title, quote=True)
    return (
        '<!DOCTYPE html>\n'
        f'<html><head><meta charset="utf-8"/><title>{safe_title}</title></head>\n'
        f'<body>\n{body}\n</body></html>\n'
    )


def slugify(name: str) -> str:
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', name).strip('-').lower()
    return slug or 'doc'


def _collect_md_images(text: str, base_dir: Path) -> dict:
    """Map local image paths referenced in markdown -> absolute source path."""
    mapping = {}
    for match in _MD_IMAGE_RE.finditer(text):
        target = match.group(1).strip()
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', target) or target.startswith('#'):
            continue
        source = (base_dir / target).resolve()
        if source.is_file():
            mapping[target] = source
    return mapping


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _copy_assets(mapping: dict, assets_dir: Path, on_collision=None) -> dict:
    """Copy collected images into assets/; return original-path -> assets name."""
    placement = {}
    used = set()
    for original, source in mapping.items():
        name = source.name
        counter = 2
        while name in used:
            stem = source.stem
            name = f'{stem}-{counter}{source.suffix}'
            counter += 1
        used.add(name)
        target = assets_dir / name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
        placement[original] = name
    return placement


def _rewrite_img_srcs(body: str, placement: dict) -> str:
    for original, asset_name in placement.items():
        body = body.replace(
            f'src="{html_mod.escape(original, quote=True)}"',
            f'src="../assets/{asset_name}"',
        )
    return body


def build_doc_from_input(input_path: Path, workspace: Path,
                         name: Optional[str] = None) -> Path:
    """Create workspace/docs/<name>.html from any supported input."""
    docs_dir = workspace / 'docs'
    assets_dir = workspace / 'assets'
    docs_dir.mkdir(parents=True, exist_ok=True)

    doc_name = name or slugify(input_path.stem)
    suffix = input_path.suffix.lower()

    if suffix in IMAGE_SUFFIXES:
        assets_dir.mkdir(parents=True, exist_ok=True)
        placement = _copy_assets({str(input_path): input_path.resolve()}, assets_dir)
        asset_name = next(iter(placement.values()))
        body = image_page(f'../assets/{asset_name}', input_path.stem)
        title = input_path.stem
    elif suffix in ('.html', '.htm'):
        raw = input_path.read_text(encoding='utf-8-sig')
        body = normalize_html_fragment(raw)
        title = doc_name
    else:  # markdown and anything text-like
        text = input_path.read_text(encoding='utf-8-sig')
        mapping = _collect_md_images(text, input_path.parent)
        if mapping:
            assets_dir.mkdir(parents=True, exist_ok=True)
            placement = _copy_assets(mapping, assets_dir)
            body = _rewrite_img_srcs(render_blocks(text), placement)
        else:
            body = render_blocks(text)
        title = doc_name

    out_path = docs_dir / f'{doc_name}.html'
    out_path.write_text(doc_document(title, body), encoding='utf-8')
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Render an AI response into the pinpont HTML IR',
    )
    parser.add_argument('input', help='Input file: .md, .html, or image')
    parser.add_argument('--name', help='Document name (default: slugified input stem)')
    parser.add_argument('--out', default='.pinpont', help='Workspace root (default: ./.pinpont)')
    return parser


def main(argv: Optional[list] = None) -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        print(f'Error: input not found: {input_path}', file=sys.stderr)
        return 1

    out_path = build_doc_from_input(input_path, Path(args.out).resolve(), args.name)
    print(str(out_path))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
