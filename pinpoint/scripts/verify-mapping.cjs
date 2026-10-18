/* Verify the diagram node->line mapping heuristic against the vendored
 * mermaid bundle, without a browser: render diagrams in jsdom, then run
 * the REAL mapping module the editor ships — static/diagram.js, imported
 * below — and report what maps where. No copied logic lives here, so the
 * verifier cannot drift from the editor.
 *
 * Manual dev tool (needs node + jsdom; not part of pytest):
 *   npm install jsdom            # anywhere resolvable, or set JS DOM_PATH
 *   node scripts/verify-mapping.cjs
 *
 * Re-run after upgrading static/vendor/mermaid.min.js — if node ids change
 * format, update MERMAID_ID_PREFIXES / tokensFromId in static/diagram.js
 * to match.
 */
const path = require('path');
const { pathToFileURL } = require('url');

function loadJsdom() {
    const candidates = [
        'jsdom',
        process.env.JSDOM_PATH,
        path.join(process.env.TEMP || '/tmp', 'opencode', 'pp-jsdom', 'node_modules', 'jsdom'),
    ].filter(Boolean);
    for (const candidate of candidates) {
        try {
            return require(candidate);
        } catch (err) {
            // try next
        }
    }
    console.error('jsdom not found; npm install jsdom (or set JSDOM_PATH)');
    process.exit(1);
}
const { JSDOM } = loadJsdom();

const SKILL = path.resolve(__dirname); // run with cwd = pinpoint/scripts

const dom = new JSDOM('<!DOCTYPE html><html><body><div id="d"></div></body></html>', {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    url: 'http://localhost/',
});
const { window } = dom;

// jsdom lacks the geometry APIs mermaid uses; stub enough of them.
const zero = () => ({ x: 0, y: 0, width: 10, height: 10, top: 0, left: 0, right: 10, bottom: 10 });
window.HTMLElement.prototype.getBBox = zero;
window.SVGElement.prototype.getBBox = zero;
window.SVGGraphicsElement = window.SVGElement;
if (!window.Element.prototype.getScreenCTM) {
    window.Element.prototype.getScreenCTM = () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0, inverse: () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }) });
}

// Execute the vendored bundle as a real global <script> (strict-mode vars
// only attach to globalThis via script evaluation, not via eval()).
const fs = require('fs');
const bundle = fs.readFileSync(path.join(SKILL, 'static', 'vendor', 'mermaid.min.js'), 'utf8');
try {
    const scriptEl = window.document.createElement('script');
    scriptEl.textContent = bundle;
    window.document.body.appendChild(scriptEl);
} catch (err) {
    console.error('bundle eval failed:', err && err.message);
    process.exit(1);
}
const mermaid = window.mermaid;
if (!mermaid) {
    console.error('window.mermaid missing after eval');
    process.exit(1);
}

// The mapping under test: the exact module the editor loads.
let buildNodeMap;

async function check(name, source) {
    const lines = source.split('\n');
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'default' });
    const { svg } = await mermaid.render(`${name}-1`, source);
    const host = window.document.createElement('div');
    host.innerHTML = svg;
    const lineEls = lines.map((t, i) => {
        const d = window.document.createElement('div');
        d.textContent = t || '\u00a0';
        d.id = `_L${i}`;
        return d;
    });
    const map = buildNodeMap(host, lineEls);
    console.log(`\n=== ${name} ===`);
    for (const m of map) {
        const tag = m.el.tagName.toLowerCase();
        const cls = (m.el.getAttribute('class') || '').split(/\s+/).slice(0, 2).join('.');
        const raw = m.el.getAttribute('data-id') || m.el.getAttribute('id');
        console.log(`${tag} ${cls} id=${JSON.stringify(raw)} -> ${m.lineId} : ${JSON.stringify(lines[Number(m.lineId.slice(2))])}`);
    }
    // Which ids exist but did NOT map?
    const mapped = new Set(map.map((m) => m.el));
    const unmapped = [];
    for (const el of host.querySelectorAll('svg [id], svg [data-id]')) {
        if (!mapped.has(el)) unmapped.push(el.getAttribute('id') || el.getAttribute('data-id'));
    }
    console.log('unmapped:', JSON.stringify(unmapped.slice(0, 12)));
}

(async () => {
    ({ buildNodeMap } = await import(
        pathToFileURL(path.join(SKILL, 'static', 'diagram.js')).href
    ));
    await check('flow', 'flowchart LR\n    client --> gateway --> service\n    service --> db[(replica)]');
    await check('flow-multiline', 'flowchart TD\n    A[Draft] --> B{Review}\n    B -->|reject| A\n    B -->|approve| C[Published]');
    await check('seq', 'sequenceDiagram\n    Alice->>Bob: Hi\n    Bob-->>Alice: Hello');
    await check('class', 'classDiagram\n    Animal <|-- Dog\n    Animal: +name');
    await check('state', 'stateDiagram-v2\n    [*] --> Idle\n    Idle --> Running: start');
    process.exit(0);
})().catch((err) => {
    console.error('FAILED:', err && (err.stack || err.message || err));
    process.exit(1);
});
