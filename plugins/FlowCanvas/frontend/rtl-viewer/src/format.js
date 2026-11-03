// Shared formatting helpers for the AXI performance overlay / views.

// Human-readable bandwidth for AXI throughput.
//
// The profiler's axi-perf.json reports throughput in BITS per second
// (`read_bps` / `write_bps` — the field is *_bps, bits not bytes). Bus and
// interconnect bandwidth is conventionally reported in BYTES per second with
// DECIMAL (SI) prefixes — MB/s = 1e6 B/s, GB/s = 1e9 B/s. (Binary prefixes
// MiB/GiB are for capacity, not rates.) So convert bits -> bytes (/8) and
// scale by 1000, not 1024.
//
// Used by the hier + block-flow bundle-pin badges (overlays/axi_perf.js) and
// the AXI Performance tab / node detail panels, so all three agree.
export function formatBandwidth(bps) {
  if (!bps || bps <= 0) return '0 B/s'
  let v = bps / 8
  const units = ['B/s', 'kB/s', 'MB/s', 'GB/s', 'TB/s']
  let i = 0
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000
    i++
  }
  return `${v.toFixed(2)} ${units[i]}`
}
