// Display names for the rtl-buddy hub app family.
//
// DISPLAY STAGE ONLY. The wire is untouched: `hello { client: 'view' }`,
// every envelope's `origin`, the `Origin` enum in the hub protocol, the
// `view.json` artefact and the `/view` route all keep their existing
// names. This module is the single place a wire origin is turned into
// the text a user reads, so renaming an app is a one-line edit here
// instead of a protocol change. The `view` → `sch` rename reaches the
// wire only at protocol v2.
//
// The family scheme:
//
//   origin   short (labels)   long (landing page + docs first mention)
//   ------   --------------   ---------------------------------------
//   view     sch              rtl-buddy-schematic
//   graph    gph              rtl-buddy-graph
//   cov      cov              rtl-buddy-coverage
//   phys     phy              rtl-buddy-phys
//
// SHORT names are for chrome LABELS (wordmark, switcher links, send
// buttons, the peers strip). Prose and tooltips stay natural English —
// "the schematic", "the graph pane", "the coverage pane" — because a
// three-letter token inside a sentence reads as a typo.
//
// The rtl_buddy panes (`src/rtl_buddy/hub/graph_page.html`,
// `cov_page.html`) carry the same table; the two copies must agree or
// the apps disagree about what to call each other in the same strip.

/**
 * Wire origin → short display label.
 *
 * Only the origins whose label differs from the wire name appear here;
 * everything else (`src`, `wave`, `cov`, `cli`, `notebook`, and any
 * origin a future hub invents) passes through unchanged. THIS is the
 * knob: changing the graph app's short name is one edit on this line.
 */
export const ORIGIN_DISPLAY = Object.freeze({
  view: 'sch',
  graph: 'gph',
  phys: 'phy',
})

/** This app's own display name — the wordmark and the document title. */
export const APP_DISPLAY_NAME = 'rtl-buddy-sch'

/**
 * The label for one wire origin. Unknown origins pass through, so a
 * hub that grows a peer we've never heard of still prints something
 * true rather than dropping it.
 *
 * @param {string} origin
 * @returns {string}
 */
export function displayOrigin(origin) {
  if (typeof origin !== 'string' || !origin) return ''
  return ORIGIN_DISPLAY[origin] || origin
}

/**
 * Label a list of wire origins, preserving order.
 *
 * @param {string[]} origins
 * @returns {string[]}
 */
export function displayOrigins(origins) {
  return (Array.isArray(origins) ? origins : []).map(displayOrigin)
}
