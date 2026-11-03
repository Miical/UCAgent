// Instance paths and generate scopes (rtl-buddy-sch#209).
//
// An instance inside a generate block carries its scope in its name:
// ``instance_name`` ``g[0].u_s``, id ``top.g[0].u_s`` — slang's and
// every simulator's hierarchical name. The generate scope (``g[0]``)
// is not a node, so a dot in an id no longer always separates two
// hierarchy levels. The node's own ``instance_name`` says where its
// parent's path ends; the last dot is only the fallback for a node
// that carries none (the top, hand-built graphs).

function scopedName(node) {
  if (!node || typeof node.id !== 'string') return null
  const name = node.instance_name
  if (typeof name !== 'string' || !name) return null
  return node.id.endsWith('.' + name) ? name : null
}

/** The instance path of ``node``'s parent, or null at the top. */
export function parentPath(node) {
  const id = node && typeof node.id === 'string' ? node.id : ''
  const name = scopedName(node)
  if (name) return id.slice(0, id.length - name.length - 1)
  const cut = id.lastIndexOf('.')
  return cut < 0 ? null : id.slice(0, cut)
}

/** The name a row or box shows for ``id`` (``node`` may be missing). */
export function leafName(node, id) {
  const name = scopedName(node && node.id === id ? node : null)
  if (name) return name
  return id.slice(id.lastIndexOf('.') + 1)
}
