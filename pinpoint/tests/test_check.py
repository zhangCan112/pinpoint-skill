"""Tests for check.py - the annotation inbox CLI (pure stdlib).

Lists pending annotations as an agent to-do list:
file -> element_id -> annotation text -> content preview.
Adapted from ppt-master's check_annotations.py output format.
"""

from pathlib import Path

import pytest

import check


DOC_WITH_ANNOTATIONS = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>t</title></head><body>
<h1 id="_pp_0" data-edit-target="true" data-edit-annotation="make this shorter">Title</h1>
<p>Hello paragraph one.</p>
<p id="_pp_2" data-edit-target="true" data-edit-annotation="换掉这个例证">Bad example here.</p>
</body></html>
"""

DOC_CLEAN = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>t</title></head><body>
<p>Nothing marked.</p>
</body></html>
"""


def make_workspace(tmp_path: Path) -> Path:
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / 'review.html').write_text(DOC_WITH_ANNOTATIONS, encoding='utf-8')
    return tmp_path


class TestScan:
    def test_lists_annotations_as_todo(self, tmp_path, capsys):
        ws = make_workspace(tmp_path)
        rc = check.main([str(ws)])
        out = capsys.readouterr().out
        assert rc == 0
        assert 'review.html' in out
        assert '_pp_0' in out and '_pp_2' in out
        assert 'make this shorter' in out
        assert '换掉这个例证' in out
        assert '[1]' in out and '[2]' in out

    def test_no_annotations(self, tmp_path, capsys):
        docs = tmp_path / 'docs'
        docs.mkdir()
        (docs / 'clean.html').write_text(DOC_CLEAN, encoding='utf-8')
        rc = check.main([str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert 'No annotations' in out

    def test_broken_html_reports_error_and_exit_1(self, tmp_path, capsys):
        docs = tmp_path / 'docs'
        docs.mkdir()
        (docs / 'bad.html').write_text(
            '<html><body><p>broken</body></html>', encoding='utf-8',
        )
        rc = check.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert rc == 1
        assert 'bad.html' in captured.err

    def test_single_file_argument(self, tmp_path, capsys):
        f = tmp_path / 'one.html'
        f.write_text(DOC_WITH_ANNOTATIONS, encoding='utf-8')
        rc = check.main([str(f)])
        out = capsys.readouterr().out
        assert rc == 0
        assert 'make this shorter' in out

    def test_missing_path_exit_1(self, tmp_path, capsys):
        rc = check.main([str(tmp_path / 'nope')])
        assert rc == 1
        assert 'not found' in capsys.readouterr().err

    def test_workspace_without_docs_dir_exit_1(self, tmp_path, capsys):
        rc = check.main([str(tmp_path)])
        assert rc == 1
        assert 'docs' in capsys.readouterr().err

    def test_annotation_without_id_is_flagged(self, tmp_path, capsys):
        docs = tmp_path / 'docs'
        docs.mkdir()
        (docs / 'x.html').write_text(
            '<html><body><p data-edit-target="true" data-edit-annotation="fix me">t</p></body></html>',
            encoding='utf-8',
        )
        rc = check.main([str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert '(no id)' in out

    def test_unreadable_never_passes_as_clean(self, tmp_path, capsys):
        docs = tmp_path / 'docs'
        docs.mkdir()
        (docs / 'bad.html').write_text('<p>broken', encoding='utf-8')
        (docs / 'ok.html').write_text(DOC_CLEAN, encoding='utf-8')
        rc = check.main([str(tmp_path)])
        assert rc == 1
        out = capsys.readouterr().out
        assert 'No annotations' not in out
