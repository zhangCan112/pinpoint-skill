#!/usr/bin/env python3
"""
pinpont - Browser annotation editor server.

Flask backend for the pinpont web editor: serves the UI, lists docs,
stages annotations and direct text edits in memory, and writes them to
docs/*.html only on Apply (save-all), appending audit history to
annotations.jsonl / edits.jsonl.

Adapted from ppt-master's battle-tested svg_editor/server.py lifecycle:
per-workspace lock.json single instance, idle timeout, /api/shutdown,
Host/Origin header checks, path-traversal guards.

Usage:
    python scripts/server.py [workspace] [options]

    workspace            pinpont workspace root (default: ./.pinpont)
    --port N             bind exactly this port (default: first free from 6160)
    --timeout SECONDS    idle auto-shutdown (default 900; 0 disables)
    --no-browser         do not auto-open the browser
    --daemon             start in background, return when reachable
    --shutdown           stop the running server for this workspace

Dependencies:
    flask>=3.0.0
"""

import argparse
import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory

from annotations import (
    assign_temp_ids,
    find_by_id,
    parse_annotations,
    parse_doc_text,
    set_annotation,
    set_text,
    strip_unused_temp_ids,
)
from server_common import (
    claim_lock as _claim_lock,
    clear_lock as _clear_lock,
    find_free_port as _find_free_port,
    lock_pid as _lock_pid,
    open_preview_browser,
    popen_detached as _popen_detached,
    process_alive as _process_alive,
    read_lock as _read_lock,
    release_lock as _release_lock,
    validate_port as _validate_port,
)

logger = logging.getLogger('pinpont')

DOCS_DIR_NAME = 'docs'
ASSETS_DIR_NAME = 'assets'
LOCK_FILE_NAME = 'lock.json'
EDIT_LOG_NAME = 'edits.jsonl'
ANNOTATION_LOG_NAME = 'annotations.jsonl'
SERVER_LOG_NAME = 'server.log'

ATTR_TARGET = 'data-edit-target'
ATTR_ANNOTATION = 'data-edit-annotation'

# Keep pinpont away from ppt-master's live preview / confirm UI port range
# so a stale pinpont tab cannot shut down another tool.
DEFAULT_PORT = 6160
PUBLIC_HOST = '127.0.0.1'
STARTUP_TIMEOUT = 15

MAX_ELEMENT_ID_LEN = 200
MAX_ANNOTATION_LEN = 10000
MAX_EDIT_TEXT_LEN = 5000


def _server_url(port: int, path: str = '') -> str:
    suffix = path if path.startswith('/') or not path else f'/{path}'
    return f'http://{PUBLIC_HOST}:{port}{suffix}'


def _append_log(workspace: Path, filename: str, record: dict) -> None:
    """Append one history record to the workspace JSONL log."""
    try:
        with open(workspace / filename, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError as exc:
        logger.warning('log append failed (%s): %s', filename, exc)


def _serialize_inner(root: ET.Element) -> str:
    """Serialize the body's children as an HTML fragment string."""
    body = root if root.tag == 'body' else root.find('body')
    if body is None:
        body = root
    return ''.join(ET.tostring(child, encoding='unicode') for child in body)


def _serialize_document(root: ET.Element) -> str:
    return '<!DOCTYPE html>\n' + ET.tostring(root, encoding='unicode') + '\n'


def create_app(
    workspace_dir: str,
    idle_timeout: int = 900,
    lock_file: Optional[Path] = None,
) -> Flask:
    """Create and configure the Flask app for a pinpont workspace."""
    workspace = Path(workspace_dir).resolve()
    docs_dir = workspace / DOCS_DIR_NAME
    assets_dir = workspace / ASSETS_DIR_NAME

    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config['WORKSPACE'] = workspace
    app.config['DOCS_DIR'] = docs_dir
    app.config['ASSETS_DIR'] = assets_dir
    app.config['LOCK_FILE'] = lock_file
    # In-memory staging: {doc_name: {element_id: annotation_text}}
    app.config['ANNOTATIONS'] = {}
    # Per-doc staged direct edits; written to docs/ only by /api/save-all.
    app.config['PENDING_EDITS'] = {}
    app.config['LAST_REQUEST_TIME'] = time.time()
    # Test seam: os._exit in production, callable hook under test.
    app.config['EXIT_HOOK'] = os._exit
    app.config['SHUTDOWN_DELAY'] = 0.5

    @app.before_request
    def _update_activity():
        allowed_hosts = {PUBLIC_HOST, 'localhost', '::1', '[::1]'}
        host = request.headers.get('Host', '').lower()
        if host.startswith('[') or host.count(':') == 1:
            host = host.rsplit(':', 1)[0]
        if host not in allowed_hosts:
            return jsonify({'error': 'Forbidden Host header'}), 403
        origin = request.headers.get('Origin')
        if origin is not None:
            try:
                origin_host = urllib.parse.urlsplit(origin).hostname
            except ValueError:
                origin_host = None
            if origin_host not in allowed_hosts:
                return jsonify({'error': 'Forbidden Origin header'}), 403
        app.config['LAST_REQUEST_TIME'] = time.time()

    @app.after_request
    def _no_cache_static(resp):
        # Editor JS/CSS must update immediately after skill upgrades;
        # a stale app.js from the browser cache is a support trap.
        if request.path.startswith('/static/'):
            resp.headers['Cache-Control'] = 'no-cache'
        return resp

    def _exit_with_lock_release(code: int = 0) -> None:
        lf = app.config.get('LOCK_FILE')
        if lf is not None:
            _release_lock(lf)
        # os._exit skips atexit handlers; we released the lock manually.
        app.config['EXIT_HOOK'](code)

    def _idle_watchdog():
        if idle_timeout <= 0:
            return
        while True:
            time.sleep(10)
            elapsed = time.time() - app.config['LAST_REQUEST_TIME']
            if elapsed > idle_timeout:
                logger.info('idle for %ds, shutting down', idle_timeout)
                _exit_with_lock_release(0)

    threading.Thread(target=_idle_watchdog, daemon=True).start()

    @app.route('/')
    def index():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/api/health')
    def health():
        """Cheap readiness probe; also identifies this workspace."""
        try:
            doc_count = len(list(docs_dir.glob('*.html'))) if docs_dir.exists() else 0
        except OSError:
            doc_count = 0
        resp = jsonify({
            'status': 'ok',
            'service': 'pinpont',
            'pid': os.getpid(),
            'workspace': str(workspace),
            'docs': doc_count,
        })
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    @app.route('/api/docs')
    def get_docs():
        if not docs_dir.exists():
            return jsonify({'docs': []})
        docs = []
        for doc_file in sorted(docs_dir.glob('*.html')):
            ok = True
            error_msg: Optional[str] = None
            disk_count = 0
            try:
                root = parse_doc_text(doc_file.read_text(encoding='utf-8-sig'))
                disk_count = len(parse_annotations(root))
            except (ET.ParseError, OSError) as exc:
                ok = False
                error_msg = f'parse error: {exc}'
            staged = app.config['ANNOTATIONS'].get(doc_file.name)
            count = len(staged) if staged is not None else disk_count
            try:
                mtime = doc_file.stat().st_mtime
            except OSError:
                mtime = 0
            docs.append({
                'name': doc_file.name,
                'annotated': count > 0,
                'annotation_count': count,
                'ok': ok,
                'error': error_msg,
                'mtime': mtime,
            })
        return jsonify({'docs': docs})

    def _safe_doc_path(name: str) -> Optional[Path]:
        """Reject traversal; resolve and verify the doc stays under docs/."""
        if '/' in name or '\\' in name or '..' in name or not name:
            return None
        doc_file = (docs_dir / name).resolve()
        try:
            doc_file.relative_to(docs_dir.resolve())
        except ValueError:
            return None
        return doc_file

    def _load_doc_root(name: str):
        """Parse a doc file. Returns (root, None) or (None, (response, code))."""
        doc_file = _safe_doc_path(name)
        if doc_file is None:
            return None, (jsonify({'error': 'Invalid doc name'}), 400)
        if not doc_file.exists():
            return None, (jsonify({'error': 'Doc not found'}), 404)
        try:
            root = parse_doc_text(doc_file.read_text(encoding='utf-8-sig'))
        except ET.ParseError as exc:
            logger.warning('doc parse failed: %s: %s', name, exc)
            return None, (jsonify({'error': f'Failed to parse doc: {exc}'}), 500)
        except OSError as exc:
            return None, (jsonify({'error': f'Failed to read doc: {exc}'}), 500)
        return root, None

    def _apply_pending_edits(root: ET.Element, records: list) -> Optional[str]:
        for record in records:
            ok, reason = set_text(root, record['element_id'], record['text'])
            if not ok:
                return reason
        return None

    @app.route('/api/doc/<name>')
    def get_doc(name: str):
        root, error = _load_doc_root(name)
        if error is not None:
            return error
        assert root is not None

        assign_temp_ids(root)
        pending = app.config['PENDING_EDITS'].get(name) or []
        failure = _apply_pending_edits(root, pending)
        if failure is not None:
            return jsonify({'error': f'Failed to apply pending edits: {failure}'}), 500

        disk_annotations = {
            item['element_id']: item['annotation']
            for item in parse_annotations(root)
        }
        id_to_tag: dict = {}
        for elem in root.iter():
            eid = elem.get('id')
            if eid:
                id_to_tag[eid] = elem.tag

        merged = dict(app.config['ANNOTATIONS'].get(name, disk_annotations))
        annotations_list = [
            {'element_id': eid, 'tag': id_to_tag.get(eid, ''), 'annotation': text}
            for eid, text in merged.items()
        ]
        try:
            mtime = _safe_doc_path(name).stat().st_mtime  # type: ignore[union-attr]
        except OSError:
            mtime = 0

        return jsonify({
            'name': name,
            'content': _serialize_inner(root),
            'annotations': annotations_list,
            'mtime': mtime,
            'undo_depth': len(pending),
        })

    def _get_annotation_snapshot(name: str):
        """Return the doc's staged annotation state, loading it once."""
        annotations = app.config['ANNOTATIONS']
        if name in annotations:
            return annotations[name], None
        root, error = _load_doc_root(name)
        if error is not None:
            return None, error
        assert root is not None
        assign_temp_ids(root)
        annotations[name] = {
            item['element_id']: item['annotation']
            for item in parse_annotations(root)
        }
        return annotations[name], None

    @app.route('/api/doc/<name>/annotate', methods=['POST'])
    def post_annotate(name: str):
        data = request.get_json(silent=True)
        if not data or 'element_id' not in data or 'annotation' not in data:
            return jsonify({'error': 'Missing element_id or annotation'}), 400
        element_id = data['element_id']
        annotation = data['annotation']
        if not isinstance(element_id, str) or not isinstance(annotation, str):
            return jsonify({'error': 'element_id and annotation must be strings'}), 400
        if len(element_id) > MAX_ELEMENT_ID_LEN:
            return jsonify({'error': f'element_id too long (max {MAX_ELEMENT_ID_LEN})'}), 400
        if len(annotation) > MAX_ANNOTATION_LEN:
            return jsonify({'error': f'Annotation too long (max {MAX_ANNOTATION_LEN})'}), 400

        snapshot, error = _get_annotation_snapshot(name)
        if error is not None:
            return error
        assert snapshot is not None
        snapshot[element_id] = annotation
        return jsonify({'status': 'ok', 'count': len(snapshot)})

    @app.route('/api/doc/<name>/annotate/<element_id>', methods=['DELETE'])
    def delete_annotate(name: str, element_id: str):
        snapshot, error = _get_annotation_snapshot(name)
        if error is not None:
            return error
        assert snapshot is not None
        if element_id not in snapshot:
            return jsonify({'error': 'Annotation not found'}), 404
        del snapshot[element_id]
        return jsonify({'status': 'ok', 'count': len(snapshot)})

    @app.route('/api/doc/<name>/edit', methods=['POST'])
    def post_edit(name: str):
        data = request.get_json(silent=True)
        if not data or 'element_id' not in data or 'text' not in data:
            return jsonify({'error': 'Missing element_id or text'}), 400
        element_id = data['element_id']
        text = data['text']
        if not isinstance(element_id, str) or not isinstance(text, str):
            return jsonify({'error': 'element_id and text must be strings'}), 400
        if len(element_id) > MAX_ELEMENT_ID_LEN:
            return jsonify({'error': f'element_id too long (max {MAX_ELEMENT_ID_LEN})'}), 400
        if len(text) > MAX_EDIT_TEXT_LEN:
            return jsonify({'error': f'text too long (max {MAX_EDIT_TEXT_LEN})'}), 400

        root, error = _load_doc_root(name)
        if error is not None:
            return error
        assert root is not None
        assign_temp_ids(root)
        pending = app.config['PENDING_EDITS'].get(name) or []
        failure = _apply_pending_edits(root, pending)
        if failure is not None:
            return jsonify({'error': f'Failed to apply pending edits: {failure}'}), 500

        elem = find_by_id(root, element_id)
        if elem is None:
            return jsonify({'error': 'not-found'}), 400
        if len(elem) > 0:
            return jsonify({'error': 'has-element-children'}), 400

        record = {'element_id': element_id, 'text': text, 'old': elem.text or ''}
        app.config['PENDING_EDITS'].setdefault(name, []).append(record)
        return jsonify({'status': 'ok', 'undo_depth': len(app.config['PENDING_EDITS'][name])})

    @app.route('/api/doc/<name>/undo', methods=['POST'])
    def post_undo(name: str):
        """Drop the most recent staged direct edit on this doc (LIFO)."""
        if _safe_doc_path(name) is None:
            return jsonify({'error': 'Invalid doc name'}), 400
        stack = app.config['PENDING_EDITS'].get(name) or []
        if not stack:
            return jsonify({'status': 'empty', 'undo_depth': 0})
        stack.pop()
        return jsonify({'status': 'ok', 'undo_depth': len(stack)})

    @app.route('/api/save-all', methods=['POST'])
    def save_all():
        annotations = app.config['ANNOTATIONS']
        pending_edits = app.config['PENDING_EDITS']
        modified = []
        failures = []

        filenames = sorted(set(annotations.keys()) | set(pending_edits.keys()))
        for filename in filenames:
            # A staged-but-empty snapshot means the user deleted every
            # annotation — still write, so the on-disk markers are cleared.
            has_staged_annotations = filename in annotations
            anns = dict(annotations.get(filename, {}))
            edits = pending_edits.get(filename, [])

            root, error = _load_doc_root(filename)
            if error is not None:
                failures.append(f'{filename}: {error[0].get_json()["error"]}')
                continue
            assert root is not None

            assign_temp_ids(root)
            failure = _apply_pending_edits(root, edits)
            if failure is not None:
                failures.append(f'{filename}: Failed to apply edits: {failure}')
                continue

            old_annotations = {
                item['element_id']: item['annotation']
                for item in parse_annotations(root)
            }
            if not has_staged_annotations:
                anns = old_annotations

            for elem in root.iter():
                elem.attrib.pop(ATTR_TARGET, None)
                elem.attrib.pop(ATTR_ANNOTATION, None)
            for element_id, annotation_text in anns.items():
                if not set_annotation(root, element_id, annotation_text):
                    logger.warning(
                        'annotation target missing on save (%s: %s) — dropped',
                        filename, element_id,
                    )

            # Only annotated elements keep their id: it is the AI's locator
            # via check.py. The rest are session pollution.
            strip_unused_temp_ids(root, set(anns.keys()))

            doc_file = _safe_doc_path(filename)
            if doc_file is None:
                failures.append(f'{filename}: Invalid doc path')
                continue
            try:
                doc_file.write_text(_serialize_document(root), encoding='utf-8')
            except OSError as exc:
                failures.append(f'{filename}: Failed to write doc: {exc}')
                continue

            ts = time.time()
            for element_id, annotation_text in anns.items():
                old_text = old_annotations.get(element_id)
                action = 'annotation_updated' if old_text is not None else 'annotation_saved'
                _append_log(workspace, ANNOTATION_LOG_NAME, {
                    'ts': ts, 'file': filename, 'element_id': element_id,
                    'action': action, 'old': old_text, 'new': annotation_text,
                })
            for element_id, old_text in old_annotations.items():
                if element_id not in anns:
                    _append_log(workspace, ANNOTATION_LOG_NAME, {
                        'ts': ts, 'file': filename, 'element_id': element_id,
                        'action': 'annotation_removed', 'old': old_text, 'new': None,
                    })
            for edit in edits:
                _append_log(workspace, EDIT_LOG_NAME, {
                    'ts': ts, 'file': filename, 'element_id': edit['element_id'],
                    'action': 'edit', 'kind': 'text',
                    'old': edit.get('old'), 'new': edit.get('text'),
                })
            modified.append(filename)
            annotations.pop(filename, None)
            pending_edits.pop(filename, None)

        if failures:
            return jsonify({
                'error': 'Failed to save: ' + '; '.join(failures),
                'files_modified': modified,
            }), 500
        return jsonify({'status': 'ok', 'files_modified': modified})

    @app.route('/assets/<path:filename>')
    def serve_asset(filename: str):
        """Serve images referenced by docs as ../assets/* (traversal-guarded)."""
        if not assets_dir.exists():
            return jsonify({'error': 'assets directory not found'}), 404
        target = (assets_dir / filename).resolve()
        try:
            target.relative_to(assets_dir.resolve())
        except ValueError:
            return jsonify({'error': 'invalid path'}), 400
        if not target.exists() or not target.is_file():
            return jsonify({'error': 'not found'}), 404
        return send_from_directory(str(assets_dir), filename)

    @app.route('/api/shutdown', methods=['POST'])
    def shutdown():
        data = request.get_json(silent=True) or {}
        reason = data.get('reason') or 'shutdown'

        def _stop():
            time.sleep(app.config['SHUTDOWN_DELAY'])
            logger.info('shutting down (%s)', reason)
            _exit_with_lock_release(0)
        threading.Thread(target=_stop, daemon=True).start()
        return jsonify({'status': 'ok'})

    return app


# ---------------------------------------------------------------------------
# Launch / daemon / reuse machinery (ported from ppt-master svg_editor)
# ---------------------------------------------------------------------------

def _lock_file(workspace: Path) -> Path:
    return workspace / LOCK_FILE_NAME


def _shutdown_existing(workspace: Path) -> int:
    """Stop the editor server for this workspace (idempotent)."""
    lock_file = _lock_file(workspace)
    existing = _read_lock(lock_file)
    if not existing:
        logger.info('no pinpont server running — nothing to stop')
        return 0

    pid = _lock_pid(existing)
    try:
        port = int(existing.get('port', 0) or 0)
    except (TypeError, ValueError):
        port = 0
    if not _process_alive(pid):
        _clear_lock(lock_file)
        logger.info('server already stopped; cleared stale lock')
        return 0

    if port:
        try:
            req = urllib.request.Request(
                _server_url(port, '/api/shutdown'),
                data=b'{"reason": "cli-shutdown"}',
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            urllib.request.urlopen(req, timeout=3)
        except OSError:
            pass

    for _ in range(20):
        if not _process_alive(pid):
            break
        time.sleep(0.1)
    if _process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _clear_lock(lock_file)
    logger.info('pinpont server stopped (pid=%s)', pid)
    return 0


def _wait_for_ready(port: int, proc: subprocess.Popen, workspace: Path,
                    timeout: int = STARTUP_TIMEOUT) -> int:
    """Wait until this workspace's detached server responds.

    Returns the server pid recorded in the lock, or 0 on timeout. ``proc.pid``
    is not trusted for identity: on Windows a venv python.exe may be a
    launcher whose child is the real interpreter.
    """
    deadline = time.time() + timeout
    health_url = _server_url(port, '/api/health')
    last_error = ''
    while time.time() < deadline:
        if proc.poll() is not None:
            logger.error('server exited during startup (code=%s)', proc.returncode)
            return 0
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                data = json.load(response)
                lock = _read_lock(_lock_file(workspace))
                server_pid = _lock_pid(lock)
                if (
                    response.status == 200
                    and isinstance(data, dict)
                    and data.get('service') == 'pinpont'
                    and data.get('workspace') == str(workspace)
                    and lock is not None
                    and lock.get('port') == port
                    and data.get('pid') == server_pid
                ):
                    return server_pid
                last_error = 'health response belongs to another service or workspace'
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    logger.error(
        'server did not become ready at %s within %ss%s',
        health_url, timeout, f' (last error: {last_error})' if last_error else '',
    )
    return 0


def _open_browser(url: str) -> bool:
    return open_preview_browser(url, logger=logger)


def _reuse_running_server(existing: dict, *, open_browser: bool,
                          requested_port: Optional[int] = None) -> int:
    """Idempotent relaunch: point at the running server instead of failing."""
    pid = existing.get('pid', '?')
    try:
        port = int(existing.get('port', 0) or 0)
    except (TypeError, ValueError):
        port = 0
    if not port:
        logger.error(
            'pinpont server already running (pid=%s) but its lock records no '
            'usable port; run --shutdown, then start again', pid,
        )
        return 1
    if requested_port is not None and port != requested_port:
        logger.error(
            'pinpont server already running on port %s; explicit --port %s '
            'cannot reuse it. Run --shutdown, then start again', port, requested_port,
        )
        return 1
    url = _server_url(port)
    logger.info('pinpont server already running (pid=%s), reusing: %s', pid, url)
    if open_browser and not _open_browser(url):
        logger.info('browser did not auto-open; open %s manually', url)
    return 0


def _open_browser_async(url: str, delay: float = 0.4) -> None:
    def _open() -> None:
        time.sleep(delay)
        _open_browser(url)
    threading.Thread(target=_open, daemon=True).start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='pinpont annotation editor server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('workspace', nargs='?', default='.pinpont',
                        help='pinpont workspace root (default: ./.pinpont)')
    parser.add_argument('--port', type=int, default=None,
                        help=f'Exact port to listen on (default: first free port from {DEFAULT_PORT})')
    parser.add_argument('--no-browser', action='store_true', help='Do not auto-open browser')
    parser.add_argument('--daemon', action='store_true',
                        help='Start in the background and return once reachable')
    parser.add_argument('--timeout', type=int, default=None,
                        help='Idle timeout in seconds (default: 900; 0 = disabled)')
    parser.add_argument('--shutdown', action='store_true',
                        help='Stop the server running for this workspace, then exit')
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(levelname)s] pinpont: %(message)s',
        datefmt='%H:%M:%S',
    )

    if args.port is not None:
        try:
            args.port = _validate_port(args.port)
        except ValueError as exc:
            logger.error('%s', exc)
            return 2

    workspace = Path(args.workspace).resolve()
    if args.shutdown:
        return _shutdown_existing(workspace)

    docs_dir = workspace / DOCS_DIR_NAME
    if not docs_dir.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        docs_dir.mkdir(parents=True, exist_ok=True)

    lock_file = _lock_file(workspace)

    if args.daemon:
        existing = _read_lock(lock_file)
        if existing and _process_alive(_lock_pid(existing)):
            return _reuse_running_server(
                existing, open_browser=not args.no_browser, requested_port=args.port,
            )
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error('cannot create workspace directory: %s (%s)', workspace, exc)
            return 1
        log_path = workspace / SERVER_LOG_NAME
        try:
            port = args.port if args.port is not None else _find_free_port(DEFAULT_PORT)
        except RuntimeError as exc:
            logger.error('%s', exc)
            return 1
        idle_timeout = args.timeout if args.timeout is not None else 900
        cmd = [
            sys.executable, str(Path(__file__).resolve()), str(workspace),
            '--port', str(port), '--timeout', str(idle_timeout), '--no-browser',
        ]
        try:
            with log_path.open('a', encoding='utf-8') as log:
                proc = _popen_detached(
                    cmd, stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, logger=logger,
                )
        except OSError as exc:
            logger.error('cannot write server log: %s (%s)', log_path, exc)
            return 1
        url = _server_url(port)
        server_pid = _wait_for_ready(port, proc, workspace)
        if not server_pid:
            if proc.poll() is None:
                proc.terminate()
            logger.error('server failed to become reachable: %s (log: %s)', url, log_path)
            return 1
        logger.info('started pinpont in background: %s (pid=%s)', url, server_pid)
        logger.info('log: %s', log_path)
        if not args.no_browser and not _open_browser(url):
            logger.info('browser did not auto-open; open %s manually', url)
        return 0

    try:
        port = args.port if args.port is not None else _find_free_port(DEFAULT_PORT)
    except RuntimeError as exc:
        logger.error('%s', exc)
        return 1

    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error('cannot create workspace directory: %s (%s)', workspace, exc)
        return 1
    existing = _claim_lock(lock_file, port)
    if existing:
        return _reuse_running_server(
            existing, open_browser=not args.no_browser, requested_port=args.port,
        )
    atexit.register(_release_lock, lock_file)

    def _on_sigterm(signum: int, _frame) -> None:
        logger.info('received signal %s, exiting', signum)
        sys.exit(0)
    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        pass

    idle_timeout = args.timeout if args.timeout is not None else 900

    app = create_app(str(workspace), idle_timeout=idle_timeout, lock_file=lock_file)

    url = _server_url(port)
    if not args.no_browser:
        _open_browser_async(url)

    logger.info('running at %s', url)
    logger.info('workspace: %s', workspace)
    logger.info('docs: %s', docs_dir)
    logger.info('idle timeout: %ds (0 = disabled)', idle_timeout)
    app.run(host=PUBLIC_HOST, port=port, debug=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
