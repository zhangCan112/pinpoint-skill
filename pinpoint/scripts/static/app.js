/* pinpoint annotation editor frontend (vanilla JS, strict CSP, no framework). */
'use strict';

const state = {
    docs: [],
    current: null,
    docData: null,
    annotations: new Map(), // element_id -> text (disk + staged merged)
    selectedId: null,
    dirty: false,
};

function $(selector) {
    return document.querySelector(selector);
}

async function api(path, opts = {}) {
    const init = {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
    };
    const resp = await fetch(path, init);
    let data = null;
    try {
        data = await resp.json();
    } catch (err) {
        data = null;
    }
    if (!resp.ok) {
        const message = (data && data.error) ? data.error : `HTTP ${resp.status}`;
        throw new Error(message);
    }
    return data;
}

function confirmModal(message) {
    return new Promise((resolve) => {
        const overlay = $('#modal-overlay');
        $('#modal-message').textContent = message;
        overlay.classList.remove('hidden');
        const done = (value) => {
            overlay.classList.add('hidden');
            $('#modal-confirm').removeEventListener('click', onYes);
            $('#modal-cancel').removeEventListener('click', onNo);
            resolve(value);
        };
        const onYes = () => done(true);
        const onNo = () => done(false);
        $('#modal-confirm').addEventListener('click', onYes);
        $('#modal-cancel').addEventListener('click', onNo);
    });
}

// ---------------------------------------------------------------------------
// Document list
// ---------------------------------------------------------------------------

async function loadDocs(keepSelection = true) {
    const data = await api('/api/docs');
    state.docs = data.docs;
    const list = $('#doc-list');
    list.textContent = '';
    for (const doc of state.docs) {
        const item = document.createElement('div');
        item.className = 'doc-item' + (doc.name === state.current ? ' active' : '');
        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = doc.name;
        item.appendChild(name);
        if (!doc.ok) {
            const dot = document.createElement('span');
            dot.className = 'error-dot';
            dot.title = doc.error || 'parse error';
            item.appendChild(dot);
        }
        if (doc.annotation_count > 0) {
            const count = document.createElement('span');
            count.className = 'count';
            count.textContent = String(doc.annotation_count);
            item.appendChild(count);
        }
        item.addEventListener('click', () => openDoc(doc.name));
        list.appendChild(item);
    }
    if (!keepSelection || !state.docs.some((d) => d.name === state.current)) {
        state.current = null;
        state.docData = null;
        showPlaceholder();
        await autoOpenDoc();
    }
}

async function autoOpenDoc() {
    // 1. Deep link: /#<doc-name>
    const fromHash = decodeURIComponent(window.location.hash.replace(/^#/, ''));
    if (fromHash && state.docs.some((d) => d.name === fromHash && d.ok)) {
        await openDoc(fromHash);
        return;
    }
    // 2. Default: most recently modified doc (the one just rendered).
    const openable = state.docs.filter((d) => d.ok);
    if (openable.length > 0) {
        const latest = openable.reduce((a, b) => (a.mtime >= b.mtime ? a : b));
        await openDoc(latest.name);
    }
}

// ---------------------------------------------------------------------------
// Document rendering and selection
// ---------------------------------------------------------------------------

function showPlaceholder() {
    $('#doc-title').textContent = 'Select a document on the left to begin';
    $('#doc-content').textContent = '';
    $('#doc-status').textContent = '';
    $('#annotations').textContent = '';
    deselect();
    updateStatusStrip();
}

async function openDoc(name) {
    state.current = name;
    try {
        await refreshDoc();
        await loadDocs();
        if (window.location.hash !== `#${encodeURIComponent(name)}`) {
            history.replaceState(null, '', `#${encodeURIComponent(name)}`);
        }
    } catch (err) {
        $('#doc-status').textContent = `Failed to open: ${err.message}`;
    }
}

async function refreshDoc(keepSelection = false) {
    if (!state.current) return;
    const previousSelection = keepSelection ? state.selectedId : null;
    const data = await api(`/api/doc/${encodeURIComponent(state.current)}`);
    state.docData = data;
    state.annotations = new Map(
        data.annotations.map((a) => [a.element_id, a.annotation]),
    );
    renderContent();
    renderAnnotations();
    $('#doc-title').textContent = data.name;
    $('#doc-status').textContent = '';
    $('#btn-undo').disabled = data.undo_depth === 0;
    if (previousSelection && contentElement(previousSelection)) {
        select(previousSelection);
    } else {
        deselect();
    }
    updateStatusStrip();
}

function contentElement(id) {
    return $('#doc-content').querySelector(`[id="${CSS.escape(id)}"]`);
}

function renderContent() {
    const container = $('#doc-content');
    container.innerHTML = state.docData.content;
    for (const id of state.annotations.keys()) {
        const el = container.querySelector(`[id="${CSS.escape(id)}"]`);
        if (el) el.classList.add('has-annotation');
    }
    enhanceDiagrams();
}

// ---------------------------------------------------------------------------
// Diagrams (mermaid)
// ---------------------------------------------------------------------------
// Doc IR keeps diagram source as <figure class="pp-diagram"> with one
// <div class="line"> per source line. Here we render that source into an
// SVG canvas (vendored mermaid.js) and route clicks back into the protocol:
// a click on a drawn node selects the source line defining it (so the
// annotation lands on an element that exists in the doc on disk); anything
// else selects the figure, annotating the diagram as a whole.

let mermaidInitialized = false;
let diagramSeq = 0;

const MERMAID_ID_PREFIXES = [
    'flowchart-', 'state-', 'statediagram-state-', 'classId-', 'mindmap-', 'er-',
];

function ensureMermaid() {
    if (!window.mermaid) return false;
    if (!mermaidInitialized) {
        window.mermaid.initialize({
            startOnLoad: false,
            securityLevel: 'strict',
            theme: 'default',
        });
        mermaidInitialized = true;
    }
    return true;
}

function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function diagramLines(fig) {
    return Array.from(fig.children).filter(
        (el) => el.tagName === 'DIV' && el.classList.contains('line'),
    );
}

function lineTextsOf(lines) {
    return lines.map((el) => el.textContent.replace(/\u00a0/g, ''));
}

function tokensFromId(raw) {
    let value = raw;
    for (const prefix of MERMAID_ID_PREFIXES) {
        if (value.startsWith(prefix)) value = value.slice(prefix.length);
    }
    value = value.replace(/[-_]\d+$/, '');
    if (!value) return [];
    if (value.startsWith('L_')) {
        // Flowchart edge path L_A_B_0: both endpoints are useful tokens.
        return value.slice(2).replace(/_\d+$/, '').split('_');
    }
    return [value];
}

function wordMatch(token, text) {
    // Word-boundary match so node "B" does not hit a line defining "AB".
    const pattern = new RegExp(
        `(^|[^A-Za-z0-9_])${escapeRegExp(token)}($|[^A-Za-z0-9_])`,
    );
    return pattern.test(text);
}

function lineIndexForTokens(tokens, lineTexts) {
    if (!tokens.length) return -1;
    if (tokens.length === 1) {
        return lineTexts.findIndex((t) => wordMatch(tokens[0], t));
    }
    // An edge's id carries both endpoints, in traversal order: prefer the
    // line mentioning them in that order, then in any order, then the first.
    const inOrder = lineTexts.findIndex((t) => {
        let pos = 0;
        for (const token of tokens) {
            const pattern = new RegExp(
                `(^|[^A-Za-z0-9_])${escapeRegExp(token)}($|[^A-Za-z0-9_])`,
            );
            const found = pattern.exec(t.slice(pos));
            if (!found) return false;
            pos += found.index + found[0].length;
        }
        return true;
    });
    if (inOrder !== -1) return inOrder;
    const all = lineTexts.findIndex(
        (t) => tokens.every((token) => wordMatch(token, t)),
    );
    if (all !== -1) return all;
    return lineTexts.findIndex((t) => wordMatch(tokens[0], t));
}

function lineIndexForLabel(label, lineTexts) {
    const text = label.trim();
    if (text.length < 2) return -1;
    return lineTexts.findIndex((t) => t.includes(text));
}

function buildNodeMap(canvas, lines) {
    // Best-effort mapping of drawn SVG parts -> source line element id.
    const lineTexts = lineTextsOf(lines);
    const map = [];
    for (const el of canvas.querySelectorAll('svg [id], svg [data-id]')) {
        const raw = el.getAttribute('data-id') || el.getAttribute('id');
        let idx = -1;
        if (raw) {
            idx = lineIndexForTokens(tokensFromId(raw), lineTexts);
        }
        if (idx === -1) {
            idx = lineIndexForLabel(el.textContent || '', lineTexts);
        }
        if (idx !== -1 && lines[idx].id) {
            map.push({ el, lineId: lines[idx].id });
        }
    }
    return map;
}

function markNotedNodes(fig) {
    const map = fig.__ppMap || [];
    let any = false;
    for (const entry of map) {
        const noted = state.annotations.has(entry.lineId);
        entry.el.classList.toggle('pp-noted', noted);
        any = any || noted;
    }
    fig.classList.toggle('pp-has-notes', any);
}

function updateDiagramNodeSelection(id) {
    for (const fig of document.querySelectorAll('figure.pp-diagram')) {
        for (const entry of fig.__ppMap || []) {
            entry.el.classList.toggle('pp-node-selected', entry.lineId === id);
        }
    }
}

function setupDiagramFigure(fig) {
    if (fig.dataset.ppReady === 'true') return;
    fig.dataset.ppReady = 'true';

    const lines = diagramLines(fig);
    const source = lineTextsOf(lines).join('\n');

    const canvas = document.createElement('div');
    canvas.className = 'pp-canvas';
    fig.appendChild(canvas);

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'pp-toggle';
    toggle.textContent = 'View source';
    toggle.title = 'Switch between the rendered diagram and its mermaid source';
    fig.appendChild(toggle);

    function setSourceMode(on) {
        fig.dataset.ppSource = on ? 'true' : 'false';
        fig.classList.toggle('pp-source-mode', on);
        toggle.textContent = on ? 'View diagram' : 'View source';
    }

    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        setSourceMode(fig.dataset.ppSource !== 'true');
    });

    fig.addEventListener('click', (e) => {
        if (e.target.closest('.pp-toggle')) return;
        e.stopPropagation(); // mermaid assigns its own ids inside the svg
        // A click on a visible source line (or the figure chrome) keeps the
        // default closest-[id] behaviour.
        const idEl = e.target.closest('[id]');
        if (idEl && fig.contains(idEl) && !canvas.contains(idEl) && idEl.id) {
            select(idEl.id);
            return;
        }
        let hit = null;
        for (let node = e.target; node && node !== fig; node = node.parentElement) {
            const found = (fig.__ppMap || []).find((m) => m.el === node);
            if (found) { hit = found; break; }
        }
        if (hit) {
            select(hit.lineId);
            if (fig.dataset.ppSource !== 'true') {
                fig.scrollIntoView({ block: 'center', behavior: 'smooth' });
            }
        } else if (fig.id) {
            select(fig.id);
        } else {
            deselect();
        }
    });

    async function renderDiagram() {
        if (!ensureMermaid()) {
            toggle.hidden = true;
            setSourceMode(true); // degrade: keep source visible + annotatable
            return;
        }
        try {
            const id = `pp-mermaid-${++diagramSeq}`;
            const { svg } = await window.mermaid.render(id, source);
            if (!canvas.isConnected) return; // doc switched mid-render
            canvas.innerHTML = svg;
            fig.__ppMap = buildNodeMap(canvas, lines);
            markNotedNodes(fig);
            updateDiagramNodeSelection(state.selectedId);
        } catch (err) {
            if (!canvas.isConnected) return;
            fig.classList.add('pp-failed');
            const note = document.createElement('div');
            note.className = 'pp-error';
            note.textContent = `Diagram render failed: ${err && err.message ? err.message : err}`;
            canvas.replaceChildren(note);
            setSourceMode(true);
        }
    }

    setSourceMode(false);
    renderDiagram();
}

function enhanceDiagrams() {
    for (const fig of document.querySelectorAll('figure.pp-diagram')) {
        setupDiagramFigure(fig);
    }
}

function select(id) {
    deselect();
    state.selectedId = id;
    const el = contentElement(id);
    if (!el) return;
    el.classList.add('selected');

    const info = $('#selected-element');
    info.classList.remove('empty');
    info.textContent = '';
    const tagLine = document.createElement('div');
    const tag = document.createElement('span');
    tag.className = 'tag-name';
    tag.textContent = el.tagName.toLowerCase();
    tagLine.appendChild(tag);
    tagLine.appendChild(document.createTextNode(`  #${id}`));
    info.appendChild(tagLine);
    const preview = document.createElement('div');
    preview.textContent = (el.textContent || '').trim().slice(0, 120);
    info.appendChild(preview);

    $('#annotation-input').classList.remove('hidden');
    const editInput = $('#edit-input');
    if (el.children.length === 0) {
        editInput.classList.remove('hidden');
        $('#edit-text').value = el.textContent;
    } else {
        editInput.classList.add('hidden');
    }
    updateDiagramNodeSelection(id);
}

function deselect() {
    if (state.selectedId) {
        const el = contentElement(state.selectedId);
        if (el) el.classList.remove('selected');
    }
    state.selectedId = null;
    updateDiagramNodeSelection(null);
    $('#selected-element').classList.add('empty');
    $('#selected-element').textContent = 'Click a block in the document to select it';
    $('#annotation-input').classList.add('hidden');
    $('#edit-input').classList.add('hidden');
}

// ---------------------------------------------------------------------------
// Annotation list
// ---------------------------------------------------------------------------

function renderAnnotations() {
    const list = $('#annotations');
    list.textContent = '';
    if (!state.docData) return;
    let index = 0;
    for (const [id, text] of state.annotations) {
        index += 1;
        const item = document.createElement('div');
        item.className = 'ann-item';

        const target = document.createElement('div');
        target.className = 'ann-target';
        const locate = document.createElement('span');
        locate.className = 'ann-locate';
        const el = contentElement(id);
        locate.textContent = `[${index}] ${el ? el.tagName.toLowerCase() : '?'} #${id}`;
        locate.title = 'Click to locate';
        locate.addEventListener('click', () => {
            select(id);
            const targetEl = contentElement(id);
            if (!targetEl) return;
            // Source lines of a diagram rendered in diagram mode are hidden;
            // scroll to the figure instead (the mapped nodes glow amber/blue).
            const fig = targetEl.closest('figure.pp-diagram');
            const scrollTarget = fig && fig.dataset.ppSource !== 'true' ? fig : targetEl;
            scrollTarget.scrollIntoView({ block: 'center', behavior: 'smooth' });
        });
        const remove = document.createElement('button');
        remove.className = 'ann-remove';
        remove.type = 'button';
        remove.textContent = '\u00d7';
        remove.title = 'Remove annotation';
        remove.addEventListener('click', () => removeAnnotation(id));
        target.appendChild(locate);
        target.appendChild(remove);

        const body = document.createElement('div');
        body.className = 'ann-text';
        body.textContent = text;

        item.appendChild(target);
        item.appendChild(body);
        list.appendChild(item);
    }
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

async function addAnnotation() {
    if (!state.selectedId || !state.current) return;
    const text = $('#annotation-text').value.trim();
    if (!text) return;
    try {
        await api(`/api/doc/${encodeURIComponent(state.current)}/annotate`, {
            method: 'POST',
            body: JSON.stringify({ element_id: state.selectedId, annotation: text }),
        });
        state.annotations.set(state.selectedId, text);
        state.dirty = true;
        $('#annotation-text').value = '';
        renderAnnotations();
        renderContentMarkers();
        updateStatusStrip();
    } catch (err) {
        $('#doc-status').textContent = err.message;
    }
}

function renderContentMarkers() {
    const container = $('#doc-content');
    for (const el of container.querySelectorAll('[id]')) {
        el.classList.toggle('has-annotation', state.annotations.has(el.id));
    }
    for (const fig of container.querySelectorAll('figure.pp-diagram')) {
        markNotedNodes(fig);
    }
}

async function removeAnnotation(id) {
    if (!state.current) return;
    try {
        await api(`/api/doc/${encodeURIComponent(state.current)}/annotate/${encodeURIComponent(id)}`, {
            method: 'DELETE',
        });
        state.annotations.delete(id);
        state.dirty = true;
        renderAnnotations();
        renderContentMarkers();
        updateStatusStrip();
    } catch (err) {
        $('#doc-status').textContent = err.message;
    }
}

async function stageEdit() {
    if (!state.selectedId || !state.current) return;
    const text = $('#edit-text').value;
    try {
        const data = await api(`/api/doc/${encodeURIComponent(state.current)}/edit`, {
            method: 'POST',
            body: JSON.stringify({ element_id: state.selectedId, text }),
        });
        state.dirty = true;
        await refreshDoc(true);
        $('#btn-undo').disabled = data.undo_depth === 0;
    } catch (err) {
        $('#doc-status').textContent = err.message;
    }
}

async function undoEdit() {
    if (!state.current) return;
    try {
        const data = await api(`/api/doc/${encodeURIComponent(state.current)}/undo`, {
            method: 'POST',
        });
        await refreshDoc(true);
        $('#btn-undo').disabled = !data.undo_depth;
    } catch (err) {
        $('#doc-status').textContent = err.message;
    }
}

async function applyAll() {
    try {
        await api('/api/save-all', { method: 'POST' });
        state.dirty = false;
        await refreshDoc(true);
        await loadDocs();
        updateStatusStrip();
    } catch (err) {
        $('#doc-status').textContent = err.message;
    }
}

async function exitPreview() {
    if (state.dirty) {
        const proceed = await confirmModal(
            'You have staged changes that are not applied yet. Exit anyway?',
        );
        if (!proceed) return;
    }
    try {
        await api('/api/shutdown', {
            method: 'POST',
            body: JSON.stringify({ reason: 'ui-exit' }),
        });
    } catch (err) {
        // Server is stopping; a failed response is expected.
    }
    document.body.textContent = 'pinpoint server stopped. You can close this tab.';
}

function updateStatusStrip() {
    const strip = $('#pending-status');
    if (!state.docData) {
        strip.textContent = '';
        return;
    }
    const editCount = state.docData.undo_depth;
    const annCount = state.annotations.size;
    strip.classList.toggle('clean', !state.dirty && editCount === 0);
    if (!state.dirty && editCount === 0) {
        strip.textContent = 'No pending changes';
    } else {
        const parts = [];
        if (annCount > 0) parts.push(`${annCount} annotation${annCount === 1 ? '' : 's'}`);
        if (editCount > 0) parts.push(`${editCount} staged edit${editCount === 1 ? '' : 's'}`);
        strip.textContent = `${parts.join(', ')} — not applied yet`;
    }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function startHeartbeat() {
    // The idle watchdog counts requests, so an open tab keeps the server
    // alive however idle the user is. Beat immediately on wake/visibility
    // so a just-resumed tab wins the race with the watchdog tick.
    const beat = () => { api('/api/health').catch(() => {}); };
    window.setInterval(beat, 60 * 1000);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') beat();
    });
    window.addEventListener('pageshow', beat);
}

function init() {
    $('#doc-content').addEventListener('click', (e) => {
        const target = e.target.closest('[id]');
        if (target && target.closest('#doc-content') === $('#doc-content')) {
            if (target.id) {
                select(target.id);
                return;
            }
        }
        deselect();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') deselect();
    });

    $('#btn-add-annotation').addEventListener('click', addAnnotation);
    $('#btn-stage-edit').addEventListener('click', stageEdit);
    $('#btn-undo').addEventListener('click', undoEdit);
    $('#btn-apply').addEventListener('click', applyAll);
    $('#btn-exit').addEventListener('click', exitPreview);
    $('#btn-reload').addEventListener('click', () => {
        if (state.current) refreshDoc();
    });

    window.addEventListener('beforeunload', (e) => {
        if (state.dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    window.addEventListener('hashchange', () => {
        const fromHash = decodeURIComponent(window.location.hash.replace(/^#/, ''));
        if (fromHash && fromHash !== state.current) {
            openDoc(fromHash);
        }
    });

    loadDocs();
    startHeartbeat();
}

init();
