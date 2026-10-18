# pinpoint

pinpoint turns a long AI reply into an annotatable web document: the user
marks exactly the blocks they disagree with, the AI consumes every mark,
and the converged document exports to clean markdown. This glossary fixes
the language used across the skill's instructions, scripts, and editor.

## Language

**pinpoint doc** (or just **doc**):
An HTML document under the workspace's `docs/` — the communication medium
rendered from an AI reply, not the deliverable itself.
_Avoid_: page, article, report

**annotation**:
A user's instruction attached to one element of a doc, to be resolved by
the AI.
_Avoid_: comment, note, remark

**direct edit**:
A user's own text change to a doc element, staged and applied without the
AI.
_Avoid_: manual edit, inline edit

**staged work**:
Annotations and direct edits held in server memory, not yet written to the
doc on disk.
_Avoid_: pending changes, unsaved work

**inbox**:
The set of annotations awaiting consumption by the AI.
_Avoid_: to-do list, queue

**ack**:
The AI's confirmation that an annotation is consumed: its attributes leave
the element and an audit record is appended.
_Avoid_: resolve, close, dismiss

**diagram figure**:
A mermaid diagram kept in a doc as source lines and drawn live by the
editor; never rasterized.
_Avoid_: chart, graph, image diagram

**node mapping**:
The heuristic that maps a drawn diagram node back to the source line
defining it, so an annotation can land on that line.
_Avoid_: click mapping, node matching

**workspace**:
The runtime-state directory (`.pinpoint/`) holding docs, assets, audit
logs, and the server lock for one project.
_Avoid_: project directory, session folder
