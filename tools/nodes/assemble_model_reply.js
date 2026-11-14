// Rebuild the model's STREAMED reply into the non-stream shape the rest of the workflow reads:
//   { choices: [{ message: { content, reasoning_content }, finish_reason }], usage, timings }
//
// Why streaming: the endpoint sits behind a Cloudflare quick tunnel, which cuts any request whose
// origin stays silent for 120 s (HTTP 524). Without streaming nothing is sent until generation ends,
// so a long thinking retry died at 125.9 s (live run, n8n execution #33). Streamed, tokens flow the
// whole time and the length of the call no longer matters.
//
// "Qwen3.8 Analyze" returns the raw SSE body as text (json.data). A plain JSON reply (non-stream, or
// an older server) passes through unchanged. A failed call arrives as an error item and is rethrown
// with its original message, so the failure branch can say what actually went wrong.
const item = $input.first().json || {};

if (item.error) {
  const e = item.error;
  const msg = typeof e === 'string' ? e : (e.message || JSON.stringify(e));
  const desc = (e && e.description) ? ' - ' + String(e.description).slice(0, 200) : '';
  throw new Error('Model call failed: ' + String(msg).slice(0, 300) + desc);
}

// Already a finished completion object: nothing to assemble.
if (Array.isArray(item.choices)) {
  return [{ json: item, pairedItem: { item: 0 } }];
}

const raw = typeof item.data === 'string' ? item.data : '';
if (!raw.trim()) throw new Error('Model call returned an empty body');

let content = '', reasoning = '', finish = null, timings = null, usage = null, done = false, chunks = 0;
for (const line of raw.split(/\r?\n/)) {
  if (!line.startsWith('data:')) continue;
  const payload = line.slice(5).trim();
  if (payload === '[DONE]') { done = true; break; }
  let ev;
  try { ev = JSON.parse(payload); } catch (e) { continue; }
  if (ev.error) throw new Error('Model server error mid-stream: ' + JSON.stringify(ev.error).slice(0, 300));
  chunks++;
  const c = (ev.choices || [])[0] || {};
  const d = c.delta || {};
  if (typeof d.content === 'string') content += d.content;
  if (typeof d.reasoning_content === 'string') reasoning += d.reasoning_content;
  if (c.finish_reason) finish = c.finish_reason;
  if (ev.timings) timings = ev.timings;
  if (ev.usage) usage = ev.usage;
}

// A stream that stops without [DONE] and without a finish_reason was cut off (tunnel drop, server
// crash). Report it rather than hand a half-written patch to the applier.
if (!done && !finish) {
  throw new Error('Model stream ended early after ' + chunks + ' chunks (no finish_reason, no [DONE])');
}
if (!chunks) throw new Error('Model stream carried no data events: ' + raw.slice(0, 200));

return [{ json: {
  choices: [{ index: 0, finish_reason: finish,
              message: { role: 'assistant', content, reasoning_content: reasoning } }],
  usage,
  timings,
  streamed: true,
  chunks
}, pairedItem: { item: 0 } }];
