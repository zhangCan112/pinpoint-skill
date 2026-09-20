#!/usr/bin/env python3
"""
pinpont - Annotation inbox CLI.

Scans the pinpont workspace docs for pending annotations (data-edit-*
attributes written by the web editor) and prints them as a to-do list for
the AI agent:  file -> element_id -> annotation text -> content preview.

Usage:
    python scripts/check.py [path]

    path   pinpont workspace root (default: ./.pinpont) or a single doc file

Exit codes:
    0  scan completed (annotations may or may not be present)
    1  path not found, no docs/ directory, or a doc failed to parse
       (an unreadable doc never passes as a doc without annotations)

Dependencies:
    None (only uses standard library)
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from annotations import parse_annotations, parse_doc_text

DOCS_DIR_NAME = 'docs'


def scan_doc_file(path: Path) -> list:
    """Parse one doc and return its annotations.

    Propagates ET.ParseError: whether one unreadable doc is fatal is the
    caller's call; an empty list must never stand in for a file that could
    not be read.
    """
    return parse_annotations(parse_doc_text(path.read_text(encoding='utf-8-sig')))


def scan_workspace(workspace: Path) -> tuple:
    """Scan all docs/*.html. Returns (results, unreadable)."""
    docs_dir = workspace / DOCS_DIR_NAME
    results: dict = {}
    unreadable: list = []
    for doc_file in sorted(docs_dir.glob('*.html')):
        try:
            anns = scan_doc_file(doc_file)
        except ET.ParseError as exc:
            unreadable.append(f'{doc_file.name}: {exc}')
            continue
        if anns:
            results[doc_file.name] = anns
    return results, unreadable


def print_results(results: dict) -> None:
    if not results:
        print('[OK] No annotations found.')
        return

    total = sum(len(anns) for anns in results.values())
    file_count = len(results)
    ann_word = 'annotation' if total == 1 else 'annotations'
    file_word = 'file' if file_count == 1 else 'files'
    print(f'Found {total} {ann_word} in {file_count} {file_word}:\n')

    for filename, annotations in results.items():
        print(filename)
        for i, ann in enumerate(annotations, 1):
            preview = f' "{ann["preview"]}"' if ann['preview'] else ''
            print(f'  [{i}] <{ann["tag"]} id="{ann["element_id"]}">{preview}')
            print(f'      -> {ann["annotation"]}')
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='List pending pinpont annotations as an agent to-do list',
    )
    parser.add_argument('path', nargs='?', default='.pinpont',
                        help='Workspace root (default: ./.pinpont) or a single .html doc')
    return parser


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError, ValueError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    target = Path(args.path).resolve()
    if not target.exists():
        print(f'Error: Path not found: {target}', file=sys.stderr)
        return 1

    unreadable: list = []

    if target.is_file():
        try:
            anns = scan_doc_file(target)
        except ET.ParseError as exc:
            unreadable.append(f'{target.name}: {exc}')
            anns = []
        results = {target.name: anns} if anns else {}
    elif target.is_dir():
        docs_dir = target / DOCS_DIR_NAME
        if not docs_dir.exists():
            print(f'Error: No {DOCS_DIR_NAME}/ directory under: {target}', file=sys.stderr)
            return 1
        results, unreadable = scan_workspace(target)
    else:
        print(f'Error: Expected a directory or .html file, got: {target}', file=sys.stderr)
        return 1

    for message in unreadable:
        print(f'[ERROR] Failed to parse doc {message}', file=sys.stderr)

    if results or not unreadable:
        print_results(results)
    return 1 if unreadable else 0


if __name__ == '__main__':
    raise SystemExit(main())
