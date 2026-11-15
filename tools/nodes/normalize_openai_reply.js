// Fallback path: the OpenAI node answered because the Qwen endpoint failed, timed out or was cut off.
// Rebuild its reply as { choices: [{ message: { content } }] } so Assemble Model Reply and
// Extract AI Response work unchanged.
const item = $input.first().json || {};
if (item.error) {
  const e = item.error;
  const msg = typeof e === 'string' ? e : (e.message || JSON.stringify(e));
  // Say what went wrong with Qwen as well; the failure comment is the only place anyone reads it.
  let qwenWhy = 'its reply was incomplete';
  try {
    const q = $('Qwen3.8 Analyze').first().json || {};
    if (q.error) qwenWhy = typeof q.error === 'string' ? q.error : (q.error.message || JSON.stringify(q.error));
  } catch (err) { /* node output not readable here: keep the generic reason */ }
  throw new Error('Qwen failed (' + String(qwenWhy).slice(0, 300) + ') and the OpenAI fallback failed too: '
    + String(msg).slice(0, 300));
}
function textOf(x) {
  if (!x) return '';
  if (typeof x === 'string') return x;
  if (Array.isArray(x)) return x.map(textOf).join('');
  if (typeof x === 'object') {
    if (typeof x.text === 'string') return x.text;
    if (x.content !== undefined) return textOf(x.content);
    if (x.message !== undefined) return textOf(x.message);
    if (x.output !== undefined) return textOf(x.output);
  }
  return '';
}
let content = textOf(item.output) || textOf(item.message) || textOf(item.content) || textOf(item.text)
  || textOf((item.choices || [])[0]);
if (!content.trim()) throw new Error('OpenAI fallback returned no text: ' + JSON.stringify(item).slice(0, 300));
return [{ json: { choices: [{ index: 0, finish_reason: 'stop',
  message: { role: 'assistant', content, reasoning_content: '' } }],
  provider: 'openai-fallback' }, pairedItem: { item: 0 } }];
