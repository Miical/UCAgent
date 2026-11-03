// Project-relative source paths for the detail panel.
//
// ``node.source.file`` is whatever absolute path the elaborator saw,
// which in a real checkout is four wrapped lines of
// ``/Users/…/work/proj/rtl/sub/foo.sv`` — the leading two thirds of
// which are identical for every node and carry no information. We show
// the tail and keep the absolute path in the title.
//
// HEURISTIC (in order; each step is a fallback for the one above):
//
//   1. Longest DIRECTORY prefix common to every distinct
//      ``source.file`` in the loaded graph. With two or more files
//      that is exactly the project's source root, and it is stable
//      across models because it is derived per-graph.
//   2. When step 1 yields nothing usable — a single file, or files
//      that only share ``/`` (a design assembled from two unrelated
//      trees, e.g. vendored IP under /opt) — the directory of the
//      TOP node's file. The top is the design's own code by
//      definition, so its directory is the most project-like anchor
//      available.
//   3. Neither available (no source blocks at all) → empty root,
//      which makes the renderer fall back to ``basename:line``.
//
// The result is cosmetic: it never feeds ``open_source`` (that always
// sends the absolute path) and never feeds diagnostic matching.

function splitDir(file) {
  // Directory segments of a POSIX-ish path. Windows paths arrive
  // normalised to forward slashes from the producer, so one split
  // covers both.
  const parts = String(file).split('/')
  parts.pop() // drop the basename
  return parts
}

/**
 * Longest common directory prefix of ``files``, as a string with no
 * trailing slash. Returns '' when the files share no directory
 * segment beyond the filesystem root.
 *
 * @param {string[]} files
 * @param {string|null} [fallbackFile] file whose directory is used
 *   when the common prefix is empty (heuristic step 2).
 */
export function commonSourceRoot(files, fallbackFile = null) {
  const distinct = [...new Set((files || []).filter((f) => typeof f === 'string' && f))]
  if (distinct.length > 1) {
    let prefix = splitDir(distinct[0])
    for (const file of distinct.slice(1)) {
      const parts = splitDir(file)
      let i = 0
      while (i < prefix.length && i < parts.length && prefix[i] === parts[i]) i++
      prefix = prefix.slice(0, i)
      if (prefix.length === 0) break
    }
    // ['', 'Users'] → '/Users'; [''] alone is just the filesystem
    // root and strips nothing worth stripping.
    const joined = prefix.join('/')
    if (joined && joined !== '') return joined
  }
  const anchor =
    typeof fallbackFile === 'string' && fallbackFile
      ? fallbackFile
      : distinct.length === 1
        ? distinct[0]
        : null
  if (anchor) {
    const dir = splitDir(anchor).join('/')
    if (dir) return dir
  }
  return ''
}

/**
 * Render ``file`` relative to ``root``. Falls back to the basename
 * when the file is outside the root (vendored IP, a generated file
 * under /tmp) so the panel never shows a ``../../..`` ladder.
 */
export function relativeSourcePath(file, root) {
  if (typeof file !== 'string' || !file) return ''
  if (root && file.startsWith(root + '/')) {
    const rest = file.slice(root.length + 1)
    if (rest) return rest
  }
  return file.split('/').pop() || file
}
