const item = $input.first().json || {};
const blob = JSON.stringify(item);

// No error key => Create Branch returned 201 and the branch is new.
if (!item.error) {
  return [{ json: { branch_ok: true, existed: false }, pairedItem: { item: 0 } }];
}

// The only failure we tolerate: this exact commit was healed before, so the branch is already
// there. Re-healing it is fine - Push Fix File updates the file in place.
if (/Reference already exists/i.test(blob)) {
  return [{ json: { branch_ok: true, existed: true }, pairedItem: { item: 0 } }];
}

// Anything else (401 bad token, 404 wrong repo, rate limit) must fail loudly rather than
// letting the run continue and report a success it did not achieve.
throw new Error('Create Branch failed and it is not a benign "already exists": ' + blob.slice(0, 500));