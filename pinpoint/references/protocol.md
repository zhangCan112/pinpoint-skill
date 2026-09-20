# Annotation protocol

How annotations are stored, addressed, and consumed. Adapted from
ppt-master's SVG annotation mechanism; the medium is HTML instead of SVG.

## Storage: annotations are attributes on the element

An annotation lives inside the document file, on the exact element it
refers to:

```html
<p id="_pp_3" data-edit-target="true" data-edit-annotation="rewrite this intro">
```

- `data-edit-target="true"` marks the element as annotated.
- `data-edit-annotation` holds the user's note, as plain text.
- There is no sidecar file and no database. The annotation travels with
  its element through any edit that does not delete the element.

## Addressing: ids are labels, not anchors

- While the editor runs, every annotatable block gets a temporary id
  `_pp_0`, `_pp_1`, ... (assigned fresh each session, in document order).
- On **Apply changes**, the server strips all temp ids except on annotated
  elements — their id is the AI's locator until the note is consumed.
- After the AI removes the annotation attributes (ack), the leftover id is
  harmless; the next editor session cleans it up.
- Annotatable blocks: `p h1-h6 ul ol li dl dt dd blockquote pre table tr
  td th img hr figure figcaption div section article header footer main
  aside nav`. Inline elements (`strong em code a`) are reached through
  their block ancestor. Code blocks get one `<span class="line">` per
  source line, each addressable.

## Lifecycle

```
user clicks element, writes note, presses "Add annotation"
        │  (staged in server memory)
        ▼
"Apply changes"  → attributes written to docs/<name>.html
        │           + annotation_saved / annotation_updated / annotation_removed
        │             record appended to .pinpoint/annotations.jsonl
        ▼
check.py lists pending notes  →  AI edits the doc, resolves the note
        │
        ▼
AI removes both attributes from the element (ack)
        + appends annotation_applied record to .pinpoint/annotations.jsonl
        ▼
next round, or convergence
```

Every note must end in one of exactly two ways: consumed (attributes
removed, audit record appended) or explicitly withdrawn by the user in the
UI (annotation_removed audit record). A note that is read but neither fixed
nor explained is a protocol violation.

## Direct edits (the no-AI channel)

The user can edit the text of an element themselves in the browser.
Staged edits are held in server memory and written by **Apply changes**.
Each edit appends a record to `.pinpoint/edits.jsonl`:

```json
{"ts": 0, "file": "<name>.html", "element_id": "<id>", "action": "edit",
 "kind": "text", "old": "<old text>", "new": "<new text>"}
```

Limitations: only elements with no element children can be direct-edited
(a paragraph containing `<strong>` must go through annotation), and edits
are plain text — inline markup would be flattened, so the server refuses.

## Audit files

All JSONL, one record per line, UTF-8, under the workspace root
(`<project>/.pinpoint/`):

| File | Written by | Records |
|---|---|---|
| `annotations.jsonl` | server (save), AI (ack) | `annotation_saved`, `annotation_updated`, `annotation_removed`, `annotation_applied` |
| `edits.jsonl` | server (save) | `edit` |
| `lock.json` | server | `{"pid": N, "port": N}` while running |
| `server.log` | server | daemon stdout/stderr |

Common record shape: `{ts, file, element_id, action, old, new}`.

## Intake normalization

`render.py` accepts any of these and produces the same clean IR
(no ids, no annotations, XML-well-formed):

- **Markdown** — split into top-level blocks; fenced code blocks become
  per-line spans; local images are copied to `.pinpoint/assets/` and `src`
  rewritten to `../assets/<name>`; named HTML entities are converted to
  numeric form so ElementTree can parse the result.
- **HTML** — sanitized (scripts, iframes, objects, embeds, `on*` handlers,
  and `javascript:` URLs are stripped) and serialized as well-formed XHTML.
  Not full HTML5 error recovery: heavily malformed markup may nest oddly.
- **Image** — wrapped in a `<figure>`, asset copied. v1 annotates the
  image as a whole; region boxes are future work.

UTF-8 BOM in input files is tolerated on read.
