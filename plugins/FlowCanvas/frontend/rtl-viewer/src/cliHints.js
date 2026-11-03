// Copy-pasteable producer commands for the SPA's empty / disabled
// states. An empty state that only says what is missing makes the
// user go find the docs; these say what to run.
//
// Spelling is the repo's docs, not the SPA's guess:
//   README.md §"Overlays — `--overlay name=path`"
//   docs/overlays.md §5a
//   docs/axi-perf-overlay.md §"render the hierarchy with the overlay"
// The renderer is the ``rtl-buddy-view`` console script and overlays
// ride on a repeatable ``--overlay <name>=<path>`` flag. (There is no
// ``rb view render`` verb in this repo — under rtl_buddy the same
// render is driven by ``rb hier``/the hub, which passes these same
// overlay flags through.)

export const RENDER_WITH_OVERLAYS_HINT =
  'rtl-buddy-view --top <top> --filelist <files.f> ' +
  '--overlay clock=<clock_map.json> --overlay reset=<reset_map.json>'

export const RENDER_WITH_AXI_PERF_HINT =
  'rtl-buddy-view --top <top> --filelist <files.f> ' +
  '--overlay axi-perf=axi-perf.json'

// Tooltip for the disabled AXI Performance tab. One string so the tab
// and the tab's own empty state cannot drift apart.
export const AXI_PERF_HINT =
  `No axi-perf overlay in this view — re-render with \`${RENDER_WITH_AXI_PERF_HINT}\` and reload.`

// The other producer: the hub itself. The "there is no schematic"
// placeholders (rtl-buddy-view#130) print this in four places between
// them — no-active-model, both idle flavours, and the hub-gone
// overlay — and a flag that drifts in one of them is a command the
// user pastes and watches fail. ``<model_name>`` is a placeholder the
// reader substitutes, spelled the way ``rb hub start --help`` spells
// it; the angle brackets are plain text (Vue escapes them on render).
export const HUB_START_HINT = 'rb hub start --serve-viewer'

// The schematic canvas (#163 P2) lays out ``layout.elk`` in the
// browser. A view.json written by a producer from before that key
// existed has nothing to lay out — and the SPA cannot synthesise it,
// because formal pin names and declared bit widths come off the
// module table only the analyzer has. So the empty state says which
// side is stale and what to re-run, rather than "no data".
export const RENDER_FOR_SCHEMATIC_HINT =
  'rtl-buddy-view --top <top> --filelist <files.f> --format json'

export const HUB_START_WITH_MODEL_HINT = `${HUB_START_HINT} --model <model_name>`
