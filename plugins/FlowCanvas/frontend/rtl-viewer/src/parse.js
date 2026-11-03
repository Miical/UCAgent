// view.json v1 loader + structural validator.
//
// The Python side (schemas/view-v1.json + tests/test_render_json.py)
// is the authoritative schema; this validator catches the most
// common producer-side breakage (wrong major version, missing
// top-level keys, malformed node entries) and surfaces a
// human-readable message so the viewer can show a toast instead
// of crashing the SPA. We deliberately don't pull in a full
// JSON-Schema runtime — keeping the JS bundle small matters more
// than complete structural coverage at the consumer layer.

export class ViewJsonError extends Error {
  constructor(message) {
    super(message)
    this.name = 'ViewJsonError'
  }
}

const SUPPORTED_MAJOR = 1

/**
 * Validate ``payload`` against the locked view.json v1 schema and
 * return the original object on success. Throws ViewJsonError on
 * any structural problem; the message is safe to display to users.
 *
 * Notes on what we check, vs. what we trust the producer to get
 * right:
 *
 *  - Required top-level keys: ``schema_version``, ``top``,
 *    ``nodes``, ``edges``, ``overlays_present``. Missing → error.
 *  - ``schema_version`` major bump → error (consumer must update).
 *  - ``nodes`` / ``edges`` array shape: each entry is an object;
 *    nodes have a string ``id``; edges have ``from`` + ``to``.
 *
 * We do NOT validate per-overlay payload structure here — overlay
 * loaders in src/overlays/<name>.js handle that on the consumer
 * side, mirroring the Python producer's per-overlay schemas.
 */
export function parseViewJson(payload) {
  if (payload == null || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new ViewJsonError('top-level must be a JSON object')
  }
  const version = payload.schema_version
  if (typeof version !== 'string') {
    throw new ViewJsonError('schema_version missing or not a string')
  }
  const major = parseInt(version.split('.')[0], 10)
  if (!Number.isFinite(major)) {
    throw new ViewJsonError(
      `schema_version ${JSON.stringify(version)} does not start with a numeric major`,
    )
  }
  if (major !== SUPPORTED_MAJOR) {
    throw new ViewJsonError(
      `schema_version ${JSON.stringify(version)} is not supported ` +
        `(consumer expects ${SUPPORTED_MAJOR}.x); upgrade the viewer or downgrade rtl-buddy-view.`,
    )
  }
  if (typeof payload.top !== 'string') {
    throw new ViewJsonError('top must be a string')
  }
  // v1.1 (issue #99): optional nullable descriptive fields recording
  // the --top / --tb-top values. Tolerate omission for v1.0 payloads.
  // Reject only on wrong type — a typed-but-wrong field means the
  // producer is malformed, not stale.
  if (
    payload.dut_top !== undefined &&
    payload.dut_top !== null &&
    typeof payload.dut_top !== 'string'
  ) {
    throw new ViewJsonError('dut_top must be a string or null')
  }
  if (
    payload.tb_top !== undefined &&
    payload.tb_top !== null &&
    typeof payload.tb_top !== 'string'
  ) {
    throw new ViewJsonError('tb_top must be a string or null')
  }
  if (!Array.isArray(payload.nodes)) {
    throw new ViewJsonError('nodes must be an array')
  }
  if (!Array.isArray(payload.edges)) {
    throw new ViewJsonError('edges must be an array')
  }
  if (!Array.isArray(payload.overlays_present)) {
    throw new ViewJsonError('overlays_present must be an array')
  }
  for (let i = 0; i < payload.nodes.length; i++) {
    const n = payload.nodes[i]
    if (n == null || typeof n !== 'object') {
      throw new ViewJsonError(`nodes[${i}] is not an object`)
    }
    if (typeof n.id !== 'string') {
      throw new ViewJsonError(`nodes[${i}].id must be a string`)
    }
  }
  for (let i = 0; i < payload.edges.length; i++) {
    const e = payload.edges[i]
    if (e == null || typeof e !== 'object') {
      throw new ViewJsonError(`edges[${i}] is not an object`)
    }
    if (typeof e.from !== 'string' || typeof e.to !== 'string') {
      throw new ViewJsonError(`edges[${i}].from/.to must be strings`)
    }
  }
  return payload
}
