// Diagnostic severity: one rank order, one colour map.
//
// The rank picks the worst item for a collapsed badge; the colour map
// is the CSS ``.rb-sev[data-severity=…]`` rule set in ``app.css``.
// Components add the shared class and set ``data-severity``; nothing
// else re-states which severity is which colour.

import { token } from './theme.js'

/** error > warning > info > hint. Unknown severities sort as hint. */
export const SEVERITY_RANK = { error: 3, warning: 2, info: 1, hint: 0 }

/** The severity worth showing when a badge collapses several items. */
export function worstSeverity(items) {
  let bestKey = 'info'
  let bestRank = -1
  for (const d of items || []) {
    const key = (d && d.severity) || 'info'
    const rank = SEVERITY_RANK[key] ?? 0
    if (rank > bestRank) {
      bestRank = rank
      bestKey = key
    }
  }
  return bestKey
}

/**
 * Concrete colour for a severity — for the code paths that need a
 * value rather than a class (SVG attributes, canvas). Mirrors the
 * ``.rb-sev`` rules in ``app.css``.
 */
export function severityColor(severity) {
  switch (severity) {
    case 'error':
      return token('--err')
    case 'warning':
      return token('--warn')
    case 'info':
      return token('--info')
    default:
      return token('--fg-muted')
  }
}
