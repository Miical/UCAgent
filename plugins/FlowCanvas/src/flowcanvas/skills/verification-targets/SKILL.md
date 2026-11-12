---
name: verification-targets
description: Maintain verification target cards with explicit source ranges.
---

# Verification target cards

Use only inspect/apply/begin_modify/update/end_modify. Every write needs current expected_revision.
Calls are atomic per batch. Never directly edit management JSON.

- inspect: query all cards or ids, filter kind/parent; files reads raw source; binding_id reads a bound range.
- apply(count): reserve new IDs without visible cards or shimmer. Keep existing IDs on resume.
- begin_modify(ids,action=update|delete): start/resume owned work and shimmer. Deleting parents needs
  all descendant IDs or cascade=true. Register before editing or deleting source.
- update(cards): patch id/kind/name/description/parent/bindings. Kind is assigned once. Omitted fields
  remain unchanged; bindings replaces the complete list and [] explicitly removes bindings.
  New binding: {file,line,line_count,role}; existing binding: {id,file,line_count,role}.
  line is one-based, line_count is positive. Select complete logical code yourself. The tool inserts
  // @flowcanvas id=bind_... or # @flowcanvas id=bind_... before the range and hashes its text.
  Nested FlowCanvas marker lines are not counted. Optional fingerprint rejects changed selected text.
- end_modify(ids): finish registered updates or deletes. Updates require readable text and current
  bindings, or planned=true for explicitly unimplemented cards without bindings.

Hierarchy: group -> point -> testpoint. Titles (name) and descriptions are concise readable Chinese,
referencing verification documentation when available and checked against source; otherwise derive
from source. Do not confuse a plan, a valid binding, coverage hits and passing verification.

Import existing definitions by reading source, not by relying on syntax parsing. Usually covergroups
map to groups, coverpoints/crosses to points and explicit normal bins to testpoints. Exclude
ignore/illegal bins. Do not invent implicit combinations. Read macro bodies when they define targets.
Check complete ranges, meaningful parent relationships and concrete instance ownership yourself.
Server checks only marker uniqueness, range bounds, fingerprints and card state; it does not prove
syntactic completeness or elaboration. Bound source is never evidence of passing tests.
On pause, resume with begin_modify using the same IDs and action. Preserve shared code and historical
artifacts during deletion. Never fabricate coverage counts or acknowledge source without reading it.
