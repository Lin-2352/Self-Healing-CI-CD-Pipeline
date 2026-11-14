// Parse the model's reply into a clean object and base64-encode the fix for the GitHub API.
const resp = $input.first().json;

// An upstream failure (model call / stream assembly) arrives as an error item because those nodes
// continue on error. Rethrow it VERBATIM: this node also continues on error, and "Fix Ready?" routes
// the error item to "Explain Failure", which quotes it in a PR/commit comment - so the original cause
// must survive, not be replaced by "Model returned no content".
if (resp && resp.error) {
  const e = resp.error;
  throw new Error(typeof e === 'string' ? e : (e.message || JSON.stringify(e)));
}

let text = resp?.choices?.[0]?.message?.content;
if (!text) throw new Error('Model returned no content: ' + JSON.stringify(resp).slice(0, 500));

// Qwen3.8 is a hybrid-reasoning model - drop any <think> trace before parsing.
text = text.replace(/<think>[\s\S]*?<\/think>/g, '');
text = text.replace(/^[\s\S]*?<\/think>/, '');       // unclosed trace
text = text.replace(/```(?:json)?/g, '').trim();     // strip stray fences

let obj;
try {
  obj = JSON.parse(text);
} catch (e) {
  const m = text.match(/\{[\s\S]*\}/);
  if (!m) throw new Error('No JSON object in model output: ' + text.slice(0, 500));
  obj = JSON.parse(m[0]);
}

if (!obj.edits) throw new Error('Model output has no edits field');

// ---------------------------------------------------------------------------
// Apply the SEARCH/REPLACE blocks to the CURRENT file.
//
// The original source is already in the graph - `Fetch File Content` requests it with
// `Accept: application/vnd.github.raw`, so it arrives as plain text. No extra HTTP call is
// needed here (Build AI Prompt reads it exactly the same way).
//
// Measured in DIFF_FORMAT_EVAL.md: this format emits 171 median tokens vs 655 for whole-file
// (3.8x) at 15/15 byte-exact. The EXACTLY-ONCE rule below is what made it byte-exact - the
// unified-diff alternative applied "cleanly" and still produced a file that did not parse.
// ---------------------------------------------------------------------------
// Several candidate files may have been shown (Phase 6). The edits apply to the ONE file the model
// names in file_path, and only if that file was actually shown to it: a model that "fixes" a file it
// never saw is guessing, and its SEARCH text could match by accident. Items come from GitHub's JSON
// contents API (path + base64 content); a plain-text item is the legacy single-file shape.
const shown = [];
for (const it of $('Fetch File Content').all()) {
  const j = it.json;
  if (j && typeof j.content === 'string' && j.path && !j.error) {
    shown.push({ path: j.path, text: Buffer.from(j.content, 'base64').toString('utf8') });
  }
}
let original;
if (shown.length) {
  const wanted = String(obj.file_path || shown[0].path).replace(/^\.?\//, '');
  const hit = shown.find(f => f.path === wanted);
  if (!hit) {
    throw new Error('file_path "' + wanted + '" is not one of the files shown to the model (' +
                    shown.map(f => f.path).join(', ') + ')');
  }
  obj.file_path = hit.path;
  original = hit.text;
} else {
  original = $('Fetch File Content').first().json;
  if (typeof original === 'object') original = original.data ?? original.body ?? '';
  original = String(original ?? '');
}
if (!original) throw new Error('Fetch File Content returned nothing; cannot apply edits');

const SR_RE = /<{5,}\s*SEARCH\s*\n([\s\S]*?)\n?={5,}\s*\n([\s\S]*?)\n?>{5,}\s*REPLACE/g;
let patched = original;
let applied = 0;
let m;
while ((m = SR_RE.exec(obj.edits)) !== null) {
  const search = m[1];
  const replace = m[2];
  if (!search) throw new Error('edit block ' + (applied + 1) + ' has an empty SEARCH');
  const at = patched.indexOf(search);
  // Reject rather than guess. A SEARCH that matches nothing means the model invented context;
  // one that matches twice means we would be patching an arbitrary occurrence.
  if (at === -1) {
    throw new Error('SEARCH text of block ' + (applied + 1) + ' is not in the file: ' +
                    JSON.stringify(search.slice(0, 120)));
  }
  if (patched.indexOf(search, at + 1) !== -1) {
    throw new Error('SEARCH text of block ' + (applied + 1) + ' is not unique (matches >1): ' +
                    JSON.stringify(search.slice(0, 120)));
  }
  patched = patched.slice(0, at) + replace + patched.slice(at + search.length);
  applied++;
}
if (applied === 0) {
  throw new Error('no well-formed SEARCH/REPLACE blocks in edits: ' +
                  JSON.stringify(String(obj.edits).slice(0, 300)));
}
if (patched === original) throw new Error('edits applied but the file is unchanged');

// Syntax gate. `node --check` does not exist inside the n8n Code sandbox, but `new Function`
// forces a full parse WITHOUT executing, which is what we need. Skipping this check is exactly
// how the diff-format eval let a non-parsing file through to a PR: CI red, PR merged-looking,
// nothing healed - worse than not healing, because it looks like it worked.
//
// SCOPE: this gates PARSE errors only. An edit that anchors somewhere valid-but-wrong produces
// a file that parses perfectly and still fails the build; no parser can see that.
// This is the only gate BEFORE the PR opens. After it opens, the workflow's pull_request trigger
// runs CI on the heal PR. A wrong-but-parsing fix turns the PR red, and that wakes the healer
// again on the same branch, up to the attempt cap in main.yml.
try {
  new Function(patched);
} catch (e) {
  throw new Error('patched file does not parse (' + e.message + '); refusing to open a PR');
}

obj.fixed_code = patched;
obj._edits_applied = applied;
if (!obj.branch_name || /^(main|master)$/i.test(obj.branch_name)) {
  obj.branch_name = 'ai-fix-' + Date.now();
}

const prev = $('Build AI Prompt').first().json;

// The model names the branch after the bug, so the SAME failure twice produces the SAME
// branch name and `Create Branch` dies with 422 "Reference already exists" - after the
// model call has already been paid for. Suffixing the commit sha separates *different*
// commits, which is the recurring-failure case that broke Phase D.
// It does NOT make the name globally unique: re-running the SAME commit lands on the same
// branch again (measured). That case is handled by `Create Branch` tolerating the 422 and
// `Branch Ready` triaging it, so the heal updates the branch in place.
const shortSha = String(prev.commit || '').slice(0, 7);
if (shortSha && !obj.branch_name.endsWith(shortSha)) {
  obj.branch_name = obj.branch_name + '-' + shortSha;
}

// Retry: the fix goes onto the SAME branch, so the open PR updates instead of a new one opening.
// Whatever the model names the branch is ignored. `Create Branch` then gets "Reference already
// exists" and `Branch Ready` treats it as the in-place path.
const attempt = Number(prev.attempt) || 0;
if (prev.is_retry && prev.branch) obj.branch_name = String(prev.branch);

// The trailer is the loop's counter: main.yml reads it off the PR head commit and stops waking
// the healer at the cap. Every heal commit carries it, the first one included (Heal-Attempt: 1).
const prTitle = obj.pr_title || 'AI fix: CI failure';
const commitMessage = prTitle + '\n\nHeal-Attempt: ' + (attempt + 1);

return [{ json: {
  branch_name: obj.branch_name,
  file_path:   obj.file_path || prev.file_path,
  fixed_code:  obj.fixed_code,
  pr_title:    prTitle,
  commit_message: commitMessage,
  attempt:     attempt + 1,
  pr_body:     obj.pr_body  || 'Automated fix generated from the CI failure logs.',
  base64_content: Buffer.from(obj.fixed_code, 'utf8').toString('base64'),
  // How many SEARCH/REPLACE blocks were applied. Surfaced so real heals can be counted:
  // the whole-file fallback is only worth building if the reject rate turns out material
  // (break-even is ~0.70), and that rate is currently unmeasured.
  edits_applied: obj._edits_applied,
  repo:   prev.repo,
  commit: prev.commit,
  branch: prev.branch
// pairedItem is correct practice, but it was NOT the cause of the empty-URL bug - that was
// the isolate/vm2 clone boundary, fixed by using $node[...] downstream. See the note on the
// `Get File SHA` node. Every field below is a PRIMITIVE on purpose: $node[...] resolves
// primitives on the host, whereas nested objects come back as clone metadata.
}, pairedItem: { item: 0 } }];