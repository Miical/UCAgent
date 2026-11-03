// SPA layout constants.
//
// These numbers exist on both sides of the CSS/JS line: the stylesheet
// needs them for ``calc(100vh - …)`` and the AXI tab's grid template,
// the drag handler needs them to clamp the sidebar width. They used to
// be magic ``48px`` / ``280px`` literals repeated across App.vue and
// AxiPerfView.vue.
//
// The CSS side is ``--header-h`` / ``--sidebar-w`` in ``tokens.css``;
// ``tests/tokens.spec.js`` fails when the two disagree.

/** Height of the app header strip, in px. */
export const HEADER_HEIGHT_PX = 48

/** Default sidebar width, in px — also the localStorage fallback. */
export const SIDEBAR_DEFAULT_WIDTH_PX = 280

/** Drag clamp for the sidebar resizer, in px. */
export const SIDEBAR_MIN_WIDTH_PX = 200
export const SIDEBAR_MAX_WIDTH_PX = 640

/** Height of the bottom hub status strip, in px. */
export const STATUS_STRIP_HEIGHT_PX = 24

/**
 * Upper bound on the scale ``GraphCanvas.fitToWindow`` may compute.
 *
 * "Fit" is aspect-fit: ``min(host.w / bbox.w, host.h / bbox.h)``. That
 * is the right answer while the graph is bigger than the viewport, but
 * with no ceiling a two-node scope (the common case straight after a
 * Descend) gets blown up to fill a 1280px canvas — a billboard where
 * the text is inches tall and the reader has lost every cue about how
 * big the block is relative to its siblings. Capping at 1.5x keeps
 * small graphs near their natural Graphviz size, still centred.
 *
 * Only the automatic fit is capped: the +/- buttons and the wheel run
 * through their own clamp (0.1 … 10) so a deliberate zoom is unbounded
 * by this number.
 */
export const FIT_SCALE_MAX = 1.5
