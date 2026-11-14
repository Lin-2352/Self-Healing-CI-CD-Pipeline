// A heal ended WITHOUT a fix. Say so where people will see it, instead of leaving a silent red PR.
//
// Before this node existed, a failed heal left no trace: on a retry the PR just stayed red (the
// attempt-cap comment in main.yml only fires when a heal commit exists), and on a push to main
// nothing appeared anywhere. Live proof: callback_arity's retry hit the tunnel's 120 s timeout and
// the PR sat red with no explanation.
//
// Target: the heal PR when there is one (retries: main.yml sends pr_number), otherwise the commit
// that broke main (first heals). The HTTP node after this one picks the endpoint from `target`.
// Reads the webhook payload only - `$()` on a Code node's output is unreliable in n8n's sandbox.
const item = $input.first().json || {};
const hook = $('GitHub Failure Webhook').first().json.body || {};

let reason = item.error;
if (reason && typeof reason === 'object') reason = reason.message || JSON.stringify(reason);
reason = String(reason || 'the fix could not be produced (no error message was recorded)');
// n8n appends " [line N]" to Code-node errors each time one is rethrown; it means nothing to a reader.
reason = reason.replace(/\s*\[line \d+\]/g, '').trim();

const attempt = (parseInt(hook.attempt, 10) || 0) + 1;
const prNumber = String(hook.pr_number || '').trim();
const target = /^\d+$/.test(prNumber) ? 'pr' : 'commit';

const where = target === 'pr'
  ? 'Heal attempt ' + attempt + ' on this PR'
  : 'The automatic heal for this failing commit';
const body = [
  '**Self-healing pipeline: no fix produced.**',
  '',
  where + ' stopped before pushing a fix:',
  '',
  '> ' + reason.slice(0, 600).replace(/\n/g, '\n> '),
  '',
  target === 'pr'
    ? 'CI is still failing on this branch, and no further automatic attempt will start from this run. A human needs to look.'
    : 'No heal PR was opened. A human needs to look at this failure.'
].join('\n');

return [{ json: {
  repo: hook.repo,
  commit: hook.commit,
  pr_number: target === 'pr' ? prNumber : '',
  target,
  attempt,
  reason: reason.slice(0, 600),
  body
}, pairedItem: { item: 0 } }];
