"""Tests for server.py - the Flask annotation editor backend.

Adapted from ppt-master's svg_editor/server.py request semantics:
staged in-memory annotations and direct text edits, Apply (save-all) as the
only disk write, audit JSONL history, path traversal guards, Host/Origin
checks.
"""

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from annotations import parse_doc_text, parse_annotations
from server import create_app


DOC = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>review</title></head><body>
<h1 id="_pp_0" data-edit-target="true" data-edit-annotation="existing note">Title</h1>
<p>First para.</p>
<p>Second para.</p>
</body></html>
"""

CLEAN_DOC = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>empty</title></head><body>
<p>Nothing marked.</p>
</body></html>
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    docs = tmp_path / 'docs'
    docs.mkdir()
    (tmp_path / 'assets').mkdir()
    (docs / 'review.html').write_text(DOC, encoding='utf-8')
    (docs / 'empty.html').write_text(CLEAN_DOC, encoding='utf-8')
    return tmp_path


@pytest.fixture
def client(workspace: Path):
    app = create_app(workspace, idle_timeout=0)
    app.config['TESTING'] = True
    app.config['SHUTDOWN_DELAY'] = 0.01
    exits: list = []
    app.config['EXIT_HOOK'] = lambda code=0: exits.append(code)
    with app.test_client() as c:
        yield c, exits, app


class TestBasicRoutes:
    def test_health(self, client, workspace):
        c, _, _ = client
        resp = c.get('/api/health')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        assert data['service'] == 'pinpont'
        assert data['workspace'] == str(workspace.resolve())
        assert data['docs'] == 2

    def test_docs_list_counts_disk_annotations(self, client):
        c, _, _ = client
        data = c.get('/api/docs').get_json()
        names = {d['name']: d for d in data['docs']}
        assert names['review.html']['annotation_count'] == 1
        assert names['review.html']['annotated'] is True
        assert names['empty.html']['annotation_count'] == 0
        assert names['empty.html']['ok'] is True

    def test_get_doc_assigns_temp_ids_and_lists_disk_annotations(self, client):
        c, _, _ = client
        resp = c.get('/api/doc/review.html')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'id="_pp_' in data['content']
        assert data['content'].index('<h1') < data['content'].index('<p')
        anns = {a['element_id']: a for a in data['annotations']}
        assert anns['_pp_0']['annotation'] == 'existing note'
        assert data['undo_depth'] == 0

    def test_get_missing_doc_404(self, client):
        c, _, _ = client
        assert c.get('/api/doc/nope.html').status_code == 404

    def test_index_served(self, client):
        c, _, _ = client
        resp = c.get('/')
        assert resp.status_code == 200

    def test_static_not_cached(self, client):
        c, _, _ = client
        resp = c.get('/static/app.js')
        assert resp.status_code == 200
        assert resp.headers['Cache-Control'] == 'no-cache'


class TestAnnotate:
    def test_stage_annotation(self, client):
        c, _, _ = client
        resp = c.post('/api/doc/review.html/annotate',
                      json={'element_id': '_pp_1', 'annotation': 'new note'})
        assert resp.status_code == 200
        data = c.get('/api/doc/review.html').get_json()
        anns = {a['element_id']: a for a in data['annotations']}
        assert anns['_pp_1']['annotation'] == 'new note'
        assert anns['_pp_0']['annotation'] == 'existing note'

    def test_validation_errors(self, client):
        c, _, _ = client
        assert c.post('/api/doc/review.html/annotate', json={}).status_code == 400
        assert c.post('/api/doc/review.html/annotate',
                      json={'element_id': 1, 'annotation': 'x'}).status_code == 400
        long_note = 'x' * 10001
        assert c.post('/api/doc/review.html/annotate',
                      json={'element_id': '_pp_1', 'annotation': long_note}).status_code == 400

    def test_delete_staged_annotation(self, client):
        c, _, _ = client
        c.post('/api/doc/review.html/annotate',
               json={'element_id': '_pp_1', 'annotation': 'temp'})
        resp = c.delete('/api/doc/review.html/annotate/_pp_1')
        assert resp.status_code == 200
        ids = [a['element_id'] for a in c.get('/api/doc/review.html').get_json()['annotations']]
        assert '_pp_1' not in ids

    def test_delete_disk_annotation_via_stage(self, client):
        # Deleting a disk-loaded annotation stages an empty snapshot on save.
        c, _, _ = client
        resp = c.delete('/api/doc/review.html/annotate/_pp_0')
        assert resp.status_code == 200
        ids = [a['element_id'] for a in c.get('/api/doc/review.html').get_json()['annotations']]
        assert '_pp_0' not in ids

    def test_delete_unknown_annotation_404(self, client):
        c, _, _ = client
        assert c.delete('/api/doc/review.html/annotate/_pp_9').status_code == 404


class TestDirectEdit:
    def test_stage_text_edit_shows_in_preview(self, client):
        c, _, _ = client
        resp = c.post('/api/doc/review.html/edit',
                      json={'element_id': '_pp_1', 'text': 'Replaced.'})
        assert resp.status_code == 200
        data = c.get('/api/doc/review.html').get_json()
        assert 'Replaced.' in data['content']
        assert data['undo_depth'] == 1

    def test_edit_refuses_element_with_children(self, client):
        c, _, _ = client
        resp = c.post('/api/doc/empty.html/edit',
                      json={'element_id': 'nope', 'text': 'x'})
        assert resp.status_code == 400

    def test_edit_validation(self, client):
        c, _, _ = client
        assert c.post('/api/doc/review.html/edit', json={}).status_code == 400
        too_long = 'x' * 5001
        assert c.post('/api/doc/review.html/edit',
                      json={'element_id': '_pp_1', 'text': too_long}).status_code == 400

    def test_undo_pops_last_edit(self, client):
        c, _, _ = client
        c.post('/api/doc/review.html/edit',
               json={'element_id': '_pp_1', 'text': 'First attempt.'})
        c.post('/api/doc/review.html/edit',
               json={'element_id': '_pp_2', 'text': 'Second attempt.'})
        assert c.post('/api/doc/review.html/undo').get_json()['undo_depth'] == 1
        data = c.get('/api/doc/review.html').get_json()
        assert 'Second attempt.' not in data['content']
        assert 'First attempt.' in data['content']

    def test_undo_empty(self, client):
        c, _, _ = client
        assert c.post('/api/doc/review.html/undo').get_json()['status'] == 'empty'


class TestSaveAll:
    def test_save_writes_annotations_and_edits_to_disk(self, client, workspace):
        c, _, _ = client
        c.post('/api/doc/review.html/edit',
               json={'element_id': '_pp_1', 'text': 'Edited para.'})
        c.post('/api/doc/review.html/annotate',
               json={'element_id': '_pp_2', 'annotation': 'tighten this'})
        resp = c.post('/api/save-all')
        assert resp.status_code == 200
        assert 'review.html' in resp.get_json()['files_modified']

        disk = (workspace / 'docs' / 'review.html').read_text(encoding='utf-8')
        root = parse_doc_text(disk)
        anns = {a['element_id']: a['annotation'] for a in parse_annotations(root)}
        assert anns['_pp_0'] == 'existing note'
        assert anns['_pp_2'] == 'tighten this'
        assert 'Edited para.' in disk

    def test_save_strips_unannotated_temp_ids(self, client, workspace):
        c, _, _ = client
        c.post('/api/doc/review.html/annotate',
               json={'element_id': '_pp_2', 'annotation': 'note'})
        c.post('/api/save-all')
        disk = (workspace / 'docs' / 'review.html').read_text(encoding='utf-8')
        ids = [e.get('id') for e in parse_doc_text(disk).iter() if e.get('id')]
        assert ids == ['_pp_0', '_pp_2']

    def test_save_clears_disk_annotations_deleted_in_session(self, client, workspace):
        c, _, _ = client
        c.delete('/api/doc/review.html/annotate/_pp_0')
        c.post('/api/save-all')
        disk = (workspace / 'docs' / 'review.html').read_text(encoding='utf-8')
        assert 'data-edit-target' not in disk
        assert 'existing note' not in disk

    def test_save_appends_audit_records(self, client, workspace):
        c, _, _ = client
        c.post('/api/doc/review.html/edit',
               json={'element_id': '_pp_1', 'text': 'Edited.'})
        c.post('/api/doc/review.html/annotate',
               json={'element_id': '_pp_2', 'annotation': 'tighten this'})
        c.post('/api/save-all')

        ann_log = (workspace / 'annotations.jsonl').read_text(encoding='utf-8')
        records = [json.loads(line) for line in ann_log.strip().splitlines()]
        assert any(r['action'] == 'annotation_saved' and r['element_id'] == '_pp_2'
                   for r in records)

        edit_log = (workspace / 'edits.jsonl').read_text(encoding='utf-8')
        edit_records = [json.loads(line) for line in edit_log.strip().splitlines()]
        assert any(r['kind'] == 'text' and r['new'] == 'Edited.' for r in edit_records)

    def test_save_resets_staged_state(self, client):
        c, _, _ = client
        c.post('/api/doc/review.html/annotate',
               json={'element_id': '_pp_1', 'annotation': 'x'})
        c.post('/api/save-all')
        data = c.get('/api/doc/review.html').get_json()
        assert data['undo_depth'] == 0
        data2 = c.get('/api/docs').get_json()
        review = [d for d in data2['docs'] if d['name'] == 'review.html'][0]
        assert review['annotation_count'] == 2  # _pp_0 existing + _pp_1 staged-then-saved


class TestSecurity:
    def test_path_traversal_rejected(self, client, workspace):
        c, _, _ = client
        (workspace.parent / 'secret.html').write_text('<p>secret</p>', encoding='utf-8')
        for bad in ('..%2Fsecret.html', '..\\secret.html', 'sub/dir.html', '...'):
            resp = c.get(f'/api/doc/{bad}')
            assert resp.status_code in (400, 404), bad

    def test_assets_served_with_traversal_guard(self, client, workspace):
        c, _, _ = client
        (workspace / 'assets' / 'pic.png').write_bytes(b'PNG')
        assert c.get('/assets/pic.png').status_code == 200
        assert c.get('/assets/pic.png').data == b'PNG'
        resp = c.get('/assets/..%2Fdocs%2Freview.html')
        assert resp.status_code in (400, 404)

    def test_foreign_host_header_403(self, client):
        c, _, _ = client
        resp = c.get('/api/health', headers={'Host': 'evil.example.com'})
        assert resp.status_code == 403

    def test_foreign_origin_403(self, client):
        c, _, _ = client
        resp = c.post('/api/save-all',
                      headers={'Origin': 'http://evil.example.com'})
        assert resp.status_code == 403

    def test_shutdown_invokes_exit_hook(self, client):
        c, exits, _ = client
        resp = c.post('/api/shutdown', json={'reason': 'test'})
        assert resp.status_code == 200
        for _ in range(50):
            if exits:
                break
            time.sleep(0.02)
        assert exits == [0]
