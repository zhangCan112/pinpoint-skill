---
name: pinpont
description: Use when the user wants to review a long AI response point-by-point and mark exactly which parts they disagree with; when an AI reply is too long or too structured to discuss precisely in chat; when the user asks for an annotatable web page, a "pinpont" review, or says "apply my annotations" / "应用注解". Do not use for quick questions, global feedback ("redo it"), or edits to final deliverable files.
metadata:
  pattern: tool-wrapper
  domain: ai-response-feedback
  artifact-types: [markdown, html, image]
  interaction: browser-annotation-loop
---

# pinpont

Point-to-point feedback on long AI responses. When your reply is long or
structured, plain chat makes it hard for the user to say exactly which part
they want changed. pinpont turns the reply into a web page. The user clicks
any block and writes a note on it. You consume every note one by one. The
document converges, then exports to clean markdown.

The document is a communication medium, not the deliverable. If the user
just wants the final file changed, edit the file directly instead.

## When to use

Open a pinpont document only when BOTH are true:

1. The content is long or structured (report, plan, multi-part answer,
   code review, comparison table).
2. The expected feedback is local ("change this part"), not global.

AND one of:

- You judge the reply too complex to discuss in chat, or
- The user asks for an annotatable page.

Use chat instead when:

| Situation | Channel |
|---|---|
| Short reply, simple question | Chat |
| Global or directional feedback ("wrong tone", "redo") | Chat |
| Precise small fix on a final file ("change title to X") | Edit the file |
| Reply is still being explored, rewritten every round | Chat; open a document once content stabilizes |

## The loop

Work from the skill directory (`${SKILL_DIR}/scripts`). Use `python` on
Windows, `python3` on macOS/Linux. The workspace is `<project>/.pinpont/`.

1. **Render your reply into a document.** Write the reply as markdown, then:

   ```bash
   python ${SKILL_DIR}/scripts/render.py reply.md
   ```

   Raw `.html` and image files also work as input. The command prints the
   created doc path (`.pinpont/docs/<name>.html`).

2. **Start the editor and give the user the URL.**

   ```bash
   python ${SKILL_DIR}/scripts/server.py --daemon
   ```

   Give the user the deep link `http://127.0.0.1:<port>/#<doc-name>.html`
   (read the port from launch output). The page opens that document
   directly. Tell the user, in their language, in one short message: the
   URL, that they can click any block to write an annotation for you,
   edit plain text themselves without you, and press **Apply changes**
   to submit. Say they should tell you when annotations are submitted.
   Lifecycle and remote access: [`references/editor.md`](references/editor.md).

3. **Wait for the user's go.** Do NOT run the inbox before the user says
   annotations were submitted ("applied" / `apply my annotations` /
   `应用注解`). Then read your inbox:

   ```bash
   python ${SKILL_DIR}/scripts/check.py
   ```

   Output is your to-do list: `file -> element_id -> note -> preview`.

4. **Consume every annotation.** For each item, edit the doc
   (`.pinpont/docs/<name>.html`) so the note is resolved. Follow the
   editing discipline below. Then, per item, remove `data-edit-target`
   and `data-edit-annotation` from that element (this is the ack) and
   append one audit record:

   ```json
   {"ts": 0, "file": "<name>.html", "element_id": "<id>", "action": "annotation_applied", "old": "<note>", "new": null}
   ```

   to `.pinpont/annotations.jsonl`. Never leave a note without either a fix
   or a spoken explanation.

5. **Tell the user** what changed and ask them to refresh the page. New
   annotations restart the loop at step 3. Server keeps running.

6. **When done**, optionally export the converged document:

   ```bash
   python ${SKILL_DIR}/scripts/export.py .pinpont/docs/<name>.html -o final.md
   ```

   Then stop the server if the user is finished:
   `python ${SKILL_DIR}/scripts/server.py --shutdown`.

Protocol details (attributes, ids, audit records): [`references/protocol.md`](references/protocol.md).

## Multiple topics in one conversation

Each topic gets its own document. Before opening a new document:

1. Empty the previous topic's inbox first (`check.py` shows no
   annotations). Consume or withdraw every leftover note.
2. Name the new document after its topic (`--name <topic>`). Rendering
   over a document that still has pending annotations is refused — the
   overwrite guard protects active conversations.
3. A converged document stays in `docs/` and the sidebar for reference.
   To retire it from inbox and sidebar, move it out of `docs/` (for
   example to `.pinpont/archive/`); both scan `docs/*.html` only.

## Editing discipline

These rules keep every other annotation's anchor alive. Annotations are
attributes ON an element, so they survive any edit except deletion of
their element.

1. **Never delete an element that carries `data-edit-target`.** Resolve
   the note first (remove the attributes), then restructure.
2. **Edit in place.** Change text inside the target element. Do not
   rewrite the whole document body between consumption rounds.
3. **Keep the document XML-well-formed.** Close every tag, self-close
   void elements (`<br/>`, `<img .../>`). `check.py` reports broken docs
   loudly — fix them before consuming notes.
4. **Never renumber `id="_pp_N"` values** and never add ids yourself. The
   editor assigns and cleans them.
5. Code blocks have per-line `<span class="line">` elements. Edit the text
   inside a span; keep the span wrapper.
