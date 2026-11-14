const SYSTEM_PROMPT = "You are an expert DevOps engineer and Node.js developer. Analyze the CI/CD failure logs and the source code, identify the bug, and provide a complete fix.\nConstraints: return ONLY a raw valid JSON object. No markdown, no code fences, no conversational text. The branch name must be unique and specific to the fix (e.g. ai-fix-express-init); never use main or master.\nedits must be one or more SEARCH/REPLACE blocks in exactly this form:\n<<<<<<< SEARCH\n(lines copied byte-for-byte from the current file)\n=======\n(the replacement lines)\n>>>>>>> REPLACE\nThe SEARCH text must appear EXACTLY ONCE in the file and must match character for character, including indentation. Keep each block as small as possible. Do NOT output the whole file.\nJSON schema: {\"branch_name\": string, \"file_path\": string, \"edits\": string, \"pr_title\": string, \"pr_body\": string}\npr_body: at most 2 short sentences.";

// Assemble one prompt from: webhook payload + failed job/steps + changed files + raw source.
const hook  = $('GitHub Failure Webhook').first().json.body;
const jobs  = $('Fetch Job Logs').first().json;
const cmp   = $('Fetch Changed Files').first().json;

// Guard: never ask the model to "fix" an environment problem.
// The workflow already gates on failure_kind == 'code', but the webhook is a
// public URL - anything that reaches here without that marker gets refused, so a
// port clash or a runner hiccup can't turn into a bogus PR.
const kind = (hook.failure_kind || '').toLowerCase();
if (kind && kind !== 'code') {
  throw new Error(
    `Refusing to run: failure_kind="${hook.failure_kind}" is not a code bug. ` +
    `Nothing in the source to fix, so no model call and no PR.`
  );
}

// Retry: CI failed on the healer's OWN PR and main.yml woke it again (capped there - see the
// notify job). `attempt` = heal commits already on this branch, read from the head commit's
// `Heal-Attempt: N` trailer. The file below is fetched at that head commit, so it already
// contains the previous fix: the model sees what it did and what still fails.
const attempt = Math.max(0, parseInt(hook.attempt, 10) || 0);
const isRetry = attempt > 0 && /^ai-fix-/.test(String(hook.branch || ''));

// which job failed, and which steps inside it
const failedJob = (jobs.jobs || []).find(
  j => j.conclusion === 'failure' || j.status === 'failure'
) || {};
const failedSteps = (failedJob.steps || []).filter(s => s.conclusion === 'failure');
const stepDetails = failedSteps.length
  ? failedSteps.map(s => `- step "${s.name}": ${s.conclusion} (started ${s.started_at}, completed ${s.completed_at})`).join('\n')
  : '(no per-step detail available)';

// The failing job's log, trimmed to the part that explains the failure. Without it the model
// sees only step names and source - every eval had the log, production did not.
// "Fetch Failed Job Log" returns GitHub's 302 WITHOUT following it (following would re-send the
// PAT to the blob host); "Download Job Log" fetches the signed Location with no credential
// (text -> json.data). If the API ever answers 200 directly, its body is the log. Any failure
// (404, expired log, no Location) lands here as "(log unavailable)".
const LOG_MAX_LINES = 40, LOG_MAX_CHARS = 3000;
function trimCiLog(text) {
  if (typeof text !== 'string' || !text.trim()) return '(log unavailable)';
  const lines = text.split(/\r?\n/).map(l => l
    .replace(/^\d{4}-\d\d-\d\dT[\d:.]+Z ?/, '')             // runner timestamp prefix
    .replace(/\x1b\[[0-9;]*m/g, '')                          // ANSI colour codes
    .replace(/\/home\/runner\/work\/[^\/\s]+\/[^\/\s]+\//g, '')); // checkout path -> repo-relative
  const err = lines.findIndex(l => l.startsWith('##[error]'));
  if (err < 0) return '(log unavailable)';
  let start = 0;
  for (let i = err - 1; i >= 0; i--) if (lines[i].startsWith('##[endgroup]')) { start = i + 1; break; }
  let region = lines.slice(start, err + 1)
    .map(l => l.startsWith('##[error]') ? l.slice('##[error]'.length) : l)
    .filter(l => !l.startsWith('##['));
  while (region.length && !region[0].trim()) region.shift();
  region = region.slice(-LOG_MAX_LINES);
  let out = region.join('\n');
  if (out.length > LOG_MAX_CHARS) out = out.slice(-LOG_MAX_CHARS);
  return out.trim() || '(log unavailable)';
}
let logRaw = null;
try { logRaw = $('Download Job Log').first().json.data; } catch (e) { /* node absent/errored */ }
if (typeof logRaw !== 'string' || !logRaw) {
  try {
    const api = $('Fetch Failed Job Log').first().json;
    if (api && api.statusCode === 200 && typeof api.body === 'string') logRaw = api.body;
  } catch (e) { /* keep null */ }
}
const ciLog = trimCiLog(logRaw);

// only look at source files we can actually fix
const CODE_EXT = ['js','jsx','ts','tsx','py','rb','go','java','rs','php','cs','mjs','cjs'];
const files = (cmp.files || []).filter(f => {
  const ext = (f.filename.split('.').pop() || '').toLowerCase();
  return f.status !== 'removed' && CODE_EXT.includes(ext);
});

// The candidate files ("Pick Candidate Files" chose them; "Fetch File Content" ran once per file,
// through GitHub's JSON contents API, so every item carries its own path + base64 content).
// Items that failed (404, a directory) are skipped. A plain-text item is the legacy single-file
// shape (raw content of the first changed file) and is still accepted.
const fetched = [];
for (const it of $('Fetch File Content').all()) {
  const j = it.json;
  if (j && typeof j.content === 'string' && j.path && !j.error) {
    fetched.push({ path: j.path, text: Buffer.from(j.content, 'base64').toString('utf8') });
  }
}
if (!fetched.length && files.length) {
  let raw = $('Fetch File Content').first().json;
  if (typeof raw === 'object') raw = raw.data ?? raw.body ?? '';
  raw = String(raw ?? '');
  if (raw) fetched.push({ path: files[0].filename, text: raw });
}

const LANG = { js:'javascript', jsx:'javascript', mjs:'javascript', cjs:'javascript',
               ts:'typescript', tsx:'typescript', py:'python', rb:'ruby', go:'go',
               java:'java', rs:'rust', php:'php', cs:'csharp' };

const sections = fetched.map(f => {
  const ext  = (f.path.split('.').pop() || '').toLowerCase();
  const lang = LANG[ext] || '';
  return `File: ${f.path}\n\`\`\`${lang}\n${f.text}\n\`\`\``;
}).join('\n\n');
// One file: exactly the header every earlier eval measured. Several: tell the model it must choose.
const filesHeader = fetched.length > 1
  ? `--- source files (${fetched.length} candidates; fix exactly ONE file, and set file_path to that file) ---`
  : `--- changed source files ---`;

const prompt = [
  `Repository: ${hook.repo}`,
  `Branch: ${hook.branch}`,
  `Commit: ${hook.commit}`,
  `Triggered by: ${hook.actor}`,
  `Failure kind: ${hook.failure_kind || 'code (assumed)'}`,
  `Failed job: ${failedJob.name || '(unknown)'}`,
  ...(isRetry ? [
    `Heal attempt: ${attempt + 1}. The file below ALREADY contains the previous automated fix ` +
    `(attempt ${attempt}), and CI on this fix branch still fails with the log below. ` +
    `Fix what is still broken; keep the previous change unless it is itself wrong.`
  ] : []),
  ``,
  `--- failed steps ---`,
  stepDetails,
  ``,
  `--- CI failure log ---`,
  ciLog,
  ``,
  filesHeader,
  sections || '(no code files detected in this commit)',
  ``,
  `Return ONLY the JSON object described in the system prompt.`
].join('\n');

// Build the whole model request here so n8n never has to interpolate a
// multi-line string into a JSON body (that breaks escaping).
const request_body = {
  model: 'local',
  messages: [
    { role: 'system', content: SYSTEM_PROMPT },
    { role: 'user',   content: prompt }
  ],
  temperature: 0.1,
  max_tokens: 4096,
  // Streamed so a long call never goes quiet for Cloudflare's 120 s origin timeout (a thinking
  // retry died at 125.9 s with 524 before this). "Assemble Model Reply" rebuilds the usual shape.
  stream: true,
  cache_prompt: true,   // reuse the KV of the unchanged system-prompt prefix across heals
  // Thinking ONLY on retries (SHIPPED_QUALITY_EVAL.md): off, the first pass heals 15/15 control and
  // 12/18 hard calls in ~9 s. On, it healed 18/18 hard - including callback_arity and multi_hunk,
  // which nothing else ever fixed - but at ~37 s median. A retry only happens when CI already
  // rejected the fast fix, which is exactly where the extra time buys something. The server caps
  // thinking with --reasoning-budget 1024 (tools/deploy_src.py).
  chat_template_kwargs: { enable_thinking: isRetry }
};

return [{ json: {
  prompt,
  request_body,
  ci_log: ciLog,
  attempt,
  is_retry: isRetry,
  repo: hook.repo,
  commit: hook.commit,
  branch: hook.branch,
  // default target when the model names none; Extract checks whatever it names against these
  file_path: fetched.length ? fetched[0].path : (files.length ? files[0].filename : null),
  candidate_files: fetched.map(f => f.path).join(',')
}, pairedItem: { item: 0 } }];