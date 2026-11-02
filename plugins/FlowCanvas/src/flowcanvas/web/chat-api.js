/** Backend-neutral HTTP/SSE transport. Reconnection snapshots repair missed events. */
export async function request(path, body) {
  const response = await fetch('/api/chat' + path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入');
  return data;
}
/** Subscribe to one conversation; closing this connection never cancels its turn. */
export function subscribe(id, update, connection) {
  const stream = new EventSource('/api/chat/sessions/' + id + '/events');
  stream.onmessage = event => update(JSON.parse(event.data));
  stream.onopen = () => connection(true);
  stream.onerror = () => connection(false);
  return () => stream.close();
}
