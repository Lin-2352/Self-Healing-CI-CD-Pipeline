const item = $input.first().json || {};
const blob = JSON.stringify(item);
let branchExisted = null;
try { branchExisted = $node['Branch Ready'].json.existed; } catch (e) {}

// GitHub returns the created PR object, which carries html_url.
if (item.html_url) {
  return [{ json: {
    status: 'pr_opened',
    pr_url: item.html_url,
    pr_number: item.number,
    branch_existed: branchExisted
  }, pairedItem: { item: 0 } }];
}

// Retry (CI failed on the healer's own PR): the fix was pushed to that PR's branch, which is
// ALSO the base Open Pull Request was given, so GitHub answers 422 "No commits between X and X".
// Nothing is wrong - the open PR already shows the new commit and its CI re-runs. Measured live:
// all 3 retries of go-live round 3 pushed correctly and were still marked failed by this node.
let attempt = 1;
try { attempt = Number($node['Extract AI Response'].json.attempt) || 1; } catch (e) {}
if (attempt > 1 && item.error && /No commits between|pull request already exists/i.test(blob)) {
  return [{ json: {
    status: 'retry_pushed',
    attempt,
    branch_existed: branchExisted,
    detail: 'Retry ' + attempt + ' pushed to the open heal PR; its CI re-runs on the new commit.'
  }, pairedItem: { item: 0 } }];
}

// Re-run of a commit already healed: the branch was reused and its PR is still open.
if (item.error && /pull request already exists/i.test(blob)) {
  return [{ json: {
    status: 'pr_already_open',
    branch_existed: branchExisted,
    detail: 'Branch was re-healed in place; the existing PR was left alone.'
  }, pairedItem: { item: 0 } }];
}

throw new Error('Open Pull Request failed: ' + blob.slice(0, 500));