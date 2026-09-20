# Editor server

Browser editor behind pinpoint. Behavior and lifecycle adapted from
ppt-master's live preview server. The loop in `SKILL.md` owns when to
launch it and how annotations get applied.

## Commands

```bash
python ${SKILL_DIR}/scripts/server.py --daemon              # start in background, open browser
python ${SKILL_DIR}/scripts/server.py --daemon --no-browser # remote host
python ${SKILL_DIR}/scripts/server.py --shutdown            # stop (idempotent)
python ${SKILL_DIR}/scripts/server.py --port 6200           # bind exactly this port
python ${SKILL_DIR}/scripts/server.py --timeout 0           # never auto-stop
```

(Use `python3` on macOS/Linux.) The launcher starts the server detached,
waits for `/api/health`, records `pid` + `port` in
`<project>/.pinpoint/lock.json`, then opens the browser.

## Lifecycle

- **Port**: first free port from `6160` unless `--port N` binds strictly.
  Read the real URL from launch output or `lock.json`; never assume `6160`.
- **One server per workspace**: a second launch reuses the running one
  (and prints its URL) instead of failing. A different explicit `--port`
  while one runs is an error — `--shutdown` first.
- **Idle timeout**: 900 s without any request stops the server
  (`--timeout N` overrides, `0` disables). Unapplied staged work is lost
  on timeout — the UI warns before closing the tab while anything is
  staged.
- **Stop conditions**: **Exit** button in the browser (only UI action that
  stops the server), `--shutdown`, idle timeout, or external kill.
  Stale locks (dead pid) are overwritten on next launch.

## The two channels

- **Opening a document**: the editor auto-opens the most recently modified
  document on load; a URL hash (`/#<doc-name>.html`) deep-links to a
  specific document and survives refresh.

- **Annotate** (AI changes): click a block, write the instruction,
  **Add annotation**. Staged in server memory. Nothing touches disk until
  **Apply changes**.
- **Direct edit** (no AI): click a plain-text block, edit the text,
  **Stage edit**. Shows in the preview at once. Plain-text blocks only —
  a paragraph with inline markup (`<strong>`, links) must use annotation.
  **Undo** drops the last staged edit on the current document (LIFO).

**Apply changes** writes staged annotations and direct edits to
`.pinpoint/docs/*.html`, appends audit records, and keeps the server
running. After the AI consumes notes, the user presses **Reload** to see
the revised document.

## Remote access

On a remote host run with `--no-browser`, then with `<P>` from launch
output or `lock.json`: VS Code / Cursor Remote-SSH — forward `<P>` in the
**PORTS** panel; plain SSH — `ssh -L <P>:127.0.0.1:<P> <user>@<host>`.
Open `http://localhost:<P>`.

The server binds `127.0.0.1` only, rejects foreign `Host`/`Origin`
headers, and serves a strict-CSP page (scripts, connect, and images are
same-origin only).

## Dependencies

`check.py`, `export.py`, `render.py`'s annotation library, and
`annotations.py` use only the standard library. The editor server needs
`flask>=3.0.0`; `render.py` also uses `markdown` and `beautifulsoup4`:

```bash
pip install flask markdown beautifulsoup4
```

Python 3.9+ on Windows and macOS.
