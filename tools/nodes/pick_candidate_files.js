// Decide WHICH files the model gets to see - and so which file it can fix.
//
// Until Phase 6 the workflow fixed the first changed code file of the failing commit. That fails
// whenever the bug lives elsewhere: a helper that throws deep in a stack trace, a second changed file,
// or a latent bug in a file the commit never touched (a test-only commit exposing it).
//
// Candidates, in this order (deduplicated, capped at MAX):
//   1. the first repo files in the CI log's stack frames  - where the failure actually happened
//   2. the commit's changed code files                     - what just changed
//   3. any further stack-frame files
// Stack frames are recognised by GitHub's runner checkout path, /home/runner/work/<repo>/<repo>/<path>.
// node_modules and Node internals are never candidates: nothing there is ours to fix.
//
// One item per candidate; "Fetch File Content" runs once per item. If nothing qualifies, one
// placeholder item is emitted so the run reaches "Explain Failure" and says so, instead of stopping
// silently (n8n ends a run quietly when a node outputs no items).
const MAX = 3;
const CODE = /\.(js|jsx|ts|tsx|mjs|cjs|py|rb|go|java|rs|php|cs)$/i;

let log = '';
try { log = $('Download Job Log').first().json.data; } catch (e) { /* no log node output */ }
if (typeof log !== 'string' || !log) {
  try {
    const api = $('Fetch Failed Job Log').first().json;
    if (api && api.statusCode === 200 && typeof api.body === 'string') log = api.body;
  } catch (e) { /* keep empty */ }
}
log = typeof log === 'string' ? log : '';

const frames = [];
const FRAME = /\/home\/runner\/work\/[^\/\s]+\/[^\/\s]+\/([^\s:()'"]+)/g;
let m;
while ((m = FRAME.exec(log)) !== null) {
  const p = m[1].replace(/\/{2,}/g, '/').replace(/^\.?\//, '');
  if (!CODE.test(p) || p.startsWith('node_modules/') || p.includes('/node_modules/')) continue;
  if (!frames.includes(p)) frames.push(p);
}

let changed = [];
try {
  changed = ($('Fetch Changed Files').first().json.files || [])
    .filter(f => f.status !== 'removed' && CODE.test(f.filename))
    .map(f => f.filename);
} catch (e) { /* no changed-files output */ }

const ordered = [];
for (const p of [...frames.slice(0, 2), ...changed, ...frames.slice(2)]) {
  if (!ordered.includes(p)) ordered.push(p);
}
const picked = ordered.slice(0, MAX);

if (!picked.length) {
  return [{ json: { path: '__no_code_file__', source: 'none',
                    reason: 'no code file in the stack trace or in the commit' }, pairedItem: { item: 0 } }];
}
return picked.map(p => ({
  json: { path: p, source: frames.includes(p) ? (changed.includes(p) ? 'trace+commit' : 'trace') : 'commit' },
  pairedItem: { item: 0 }
}));
