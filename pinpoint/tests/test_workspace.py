"""Tests for the self-ignore guard on the default pinpoint workspace.

The default ``.pinpoint`` workspace lives inside the host project, so it
must never leak into git: creating it also writes a ``.gitignore`` with
``*`` inside, ignoring everything in the directory (this file included).
Custom-named workspaces are the user's own choice and stay untouched.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

import render
from server_common import ensure_workspace


class TestEnsureWorkspace:
    def test_default_workspace_gets_self_ignore(self, tmp_path: Path):
        ws = tmp_path / '.pinpoint'
        ensure_workspace(ws)
        assert (ws / '.gitignore').read_text(encoding='utf-8').strip() == '*'

    def test_creates_nested_workspace(self, tmp_path: Path):
        ws = tmp_path / 'sub' / '.pinpoint'
        ensure_workspace(ws)
        assert ws.is_dir()

    def test_existing_gitignore_not_clobbered(self, tmp_path: Path):
        ws = tmp_path / '.pinpoint'
        ws.mkdir()
        (ws / '.gitignore').write_text('# custom\nlogs/\n', encoding='utf-8')
        ensure_workspace(ws)
        assert (ws / '.gitignore').read_text(encoding='utf-8') == '# custom\nlogs/\n'

    def test_custom_named_workspace_left_alone(self, tmp_path: Path):
        ws = tmp_path / 'my-review-area'
        ensure_workspace(ws)
        assert ws.is_dir()
        assert not (ws / '.gitignore').exists()


class TestRenderIntegration:
    def test_render_writes_self_ignore_for_default_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        project = tmp_path / 'project'
        project.mkdir()
        monkeypatch.chdir(project)
        src = tmp_path / 'reply.md'
        src.write_text('# T\n\nbody\n', encoding='utf-8')
        assert render.main([str(src)]) == 0
        ignore = project / '.pinpoint' / '.gitignore'
        assert ignore.read_text(encoding='utf-8').strip() == '*'

    def test_render_custom_out_dir_gets_no_gitignore(self, tmp_path: Path):
        src = tmp_path / 'reply.md'
        src.write_text('# T\n\nbody\n', encoding='utf-8')
        assert render.main(['--out', str(tmp_path), str(src)]) == 0
        assert not (tmp_path / '.gitignore').exists()


class TestGitIntegration:
    def test_git_ignores_default_workspace_contents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        if shutil.which('git') is None:
            pytest.skip('git not available')
        project = tmp_path / 'project'
        project.mkdir()
        monkeypatch.chdir(project)
        subprocess.run(['git', 'init', '-q'], check=True, capture_output=True)
        src = tmp_path / 'reply.md'
        src.write_text('# T\n\nbody\n', encoding='utf-8')
        assert render.main([str(src)]) == 0
        for rel in ('.pinpoint/.gitignore', '.pinpoint/docs/reply.html'):
            probe = subprocess.run(
                ['git', 'check-ignore', '-q', rel],
                capture_output=True,
            )
            assert probe.returncode == 0, f'git would track {rel}'
