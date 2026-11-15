## The GitHub Actions Side

Everything on the GitHub side is one workflow file, `.github/workflows/demo-ci.yml`, named "Demo CI", plus the small Node application it tests in `demo-app/`. The workflow does two jobs: it tests the code, and when the tests fail because of a code bug it wakes the healer. This chapter goes through the file from top to bottom.

### Triggers

```yaml
on:
  push:
    branches:
      - main
    paths: ["demo-app/**"]
  pull_request:
    branches:
      - main
    paths: ["demo-app/**"]
```

(source: .github/workflows/demo-ci.yml)

The workflow starts in two cases. A push to `main` starts it, and so does a pull request that targets `main`. In both cases the changed files must include something under `demo-app/`. The path filter keeps documentation commits, report files and the rest of the repository from running the tests.

The path filter has a side effect. A change that touches only files outside `demo-app/` does not start CI at all, so a failing test could not be reported for it. That does not matter for the demo, whose bugs live in the app, but it would matter for a real repository, where the filter would have to cover every folder that can break the build.

The `pull_request` trigger was added so that the fix pull requests opened by the healer are tested too. The reason is in the last section of this chapter.

### Jobs

There are two jobs, `build-and-test` and `notify-n8n-on-failure`. The second one names the first in `needs`, so it waits for it and can read its outputs. Both run on `ubuntu-latest`.

### The build-and-test job

The job's default working directory is `demo-app`. Its steps are as follows (source: .github/workflows/demo-ci.yml).

1. Check out the code with `actions/checkout@v4`.
2. Install Node.js 20 with `actions/setup-node@v4`.
3. Run `npm install`.
4. Run the tests (step id `tests`) and classify the result.
5. Run `npm run build --if-present`.

The fourth step is the one that matters for healing. It runs `npm test` with `set +e`, so the script survives a failure and can inspect the exit code:

```yaml
- name: Run tests
  id: tests
  run: |
    set +e
    npm test
    code=$?
    set -e
    if [ "$code" -eq 0 ]; then
      kind=none
    elif [ "$code" -eq 75 ]; then
      kind=infra
    else
      kind=code
    fi
    echo "failure_kind=$kind" >> "$GITHUB_OUTPUT"
    exit "$code"
```

(shortened; source: .github/workflows/demo-ci.yml, step "Run tests")

The result is published as a job output, `failure_kind`, which the second job reads. The step then exits with the original code, so the job still turns red when the tests fail. The exit code 75 is `EX_TEMPFAIL`, the conventional "try again later" code (source: demo-app/test.js, header comment).

Two situations leave `failure_kind` without the value `code`, and both are consequences of how the step is written, not of anything the workflow says. If `npm install` fails, the tests step never runs, so the output is empty. If the tests pass and only the build step fails, the output is `none`. Either way the notify job's condition is false and the healer is not called. The first case is intended, since a failed install is an environment matter. The second is a gap: a build failure after passing tests is not healed. In this demo the build script only runs `node index.js`, which prints one line (source: demo-app/package.json, demo-app/index.js).

### The notify job

#### When it runs

```yaml
if: >-
  failure() && needs.build-and-test.outputs.failure_kind == 'code' &&
  (github.event_name == 'push' || startsWith(github.head_ref, 'ai-fix-'))
permissions:
  contents: read
  pull-requests: write
  issues: write
env:
  MAX_ATTEMPTS: 3
```

(source: .github/workflows/demo-ci.yml)

The condition has three parts. `failure()` makes the job run only when an earlier job failed. The `failure_kind` check stops it for infrastructure problems: the file's own comment says that `failure()` alone would also fire for a bound port, DNS or a full runner disk, and that the model would then invent a patch for a problem that is not in the source. The third part limits the event. A push to `main` always qualifies. A pull request qualifies only when its head branch starts with `ai-fix-`, which is the prefix the healer uses. A human's pull request therefore never wakes the healer.

The job asks for `pull-requests: write` and `issues: write`. The file does not say why, but its last step comments on a pull request, which is the likely reason.

`MAX_ATTEMPTS` is 3. That is the total number of model attempts per heal PR: the first heal plus two retries (source: comment in .github/workflows/demo-ci.yml).

#### Step 1: decide

The step with id `decide` produces four outputs: `branch`, `commit`, `attempt` and `action`.

On a push the answer is fixed: the branch is `GITHUB_REF_NAME`, the commit is `GITHUB_SHA`, `attempt` is 0 and `action` is `heal`.

On a pull request the step looks at the head commit of the PR, not at `github.sha`. For a pull request event, `github.sha` points at a merge commit that GitHub makes, and that commit does not carry the healer's message. The step fetches the head commit's message through the API and reads the trailer:

```bash
attempt=$(printf '%s\n' "$msg" | sed -n 's/^Heal-Attempt: *\([0-9][0-9]*\) *$/\1/p' | tail -n 1)
```

(source: .github/workflows/demo-ci.yml, step "Decide")

The `Heal-Attempt: N` line is added by the healer to every commit it pushes, and the first heal is numbered 1 (source: tools/nodes/extract_ai_response.js). The step then picks one of three actions:

| Trailer found | Action | Meaning |
|---|---|---|
| None | `none` | A human pushed to the heal branch and owns it now. The step prints a notice and nothing else happens |
| N is at least `MAX_ATTEMPTS` | `giveup` | The cap is reached |
| Otherwise | `heal` | Call the healer again with `attempt=N` |

With the cap at 3, the commit numbering works out as follows. The first heal commit carries `Heal-Attempt: 1`. When CI fails on it, the notify job reads 1, which is below 3, and calls the healer, which pushes a commit marked 2. A second failure reads 2 and produces a commit marked 3. A third failure reads 3 and gives up. That is three model attempts in total.

The counter sits in the commit message, so re-running the workflow cannot reset it (source: comment in .github/workflows/demo-ci.yml).

#### Step 2: trigger the webhook

This step runs when `action` is `heal`. It posts to the address in the repository secret `N8N_WEBHOOK_URL`, with the shared secret in the header `X-Webhook-Secret`:

```yaml
curl -sS --fail --max-time 20 -X POST "${{ secrets.N8N_WEBHOOK_URL }}" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: ${{ secrets.N8N_WEBHOOK_SECRET }}" \
  -d '{ "run_id": "...", "repo": "...", "branch": "...", "commit": "...",
        "actor": "...", "failure_kind": "...", "attempt": "...",
        "pr_number": "..." }' \
  || echo "::warning::n8n webhook unreachable - heal not triggered"
```

(shortened; each value is a `${{ }}` expression in the real file; source: .github/workflows/demo-ci.yml)

The fields and what n8n does with them are listed in Chapter 4. Three details stand out. The request has a 20 second limit (`--max-time 20`). `--fail` makes curl report an HTTP error as a failure. And the trailing `|| echo "::warning::..."` turns any failure of the call into a warning annotation, so the step itself still succeeds.

The warning exists because the webhook address is a tunnel that is only up while the healer is deployed. Without the fallback, a stopped healer would turn every broken commit red a second time, for a reason unrelated to the commit. The go-live notes record a test with the healer down: the build job failed and the notify job ended as success, with the warning shown in the annotations (source: docs/results/GOLIVE_RESULTS.md, Round 2).

#### Step 3: give up at the cap

When `action` is `giveup`, the last step posts a comment on the pull request with `gh pr comment`. The text says how many attempts were made, that the pipeline has stopped and that the case needs a human (source: .github/workflows/demo-ci.yml). This is the only place where the cap is enforced. The n8n workflow does not check it (source: docs/architecture/n8n-workflow.md).

### The demo application

The application is small on purpose. It stands in for a real project, and the pipeline would work the same way on one (source: demo-app/index.js, comment).

| File | Role |
|---|---|
| `demo-app/index.js` | Entry point. It prints one line and nothing else |
| `demo-app/src/app.js` | An Express app with three routes: `GET /health`, `GET /greet/:name` and `GET /sum?a=&b=` |
| `demo-app/src/format.js` | Two helpers, `formatGreeting` and `formatSum`, used by the routes |
| `demo-app/test.js` | The smoke test: starts its own Express server on `PORT` (default 5000), requests `/` and expects status 200 |
| `demo-app/test/api.test.js` | The API test: loads `src/app.js`, listens on `API_PORT` (default 5055) and checks four requests |
| `demo-app/package.json` | The one dependency is `express` `^4.19.2`. The `test` script is `node test.js && node test/api.test.js` |

(source: the files listed)

The four API checks are `/health` returning `{"status":"ok"}`, `/greet/Ada` returning `Hello, Ada!`, `/sum?a=2&b=3` returning a sum of 5, and `/sum?a=x&b=3` returning status 400 with an error message (source: demo-app/test/api.test.js).

Both tests follow the same exit-code contract. A wrong status or body prints a line starting with `CODE_FAILURE` and exits with 1. If the server cannot start, the `error` event on the server prints a line starting with `INFRA_FAILURE` and exits with 75. For the smoke test, a failed `listen` such as `EADDRINUSE` goes to the infrastructure path, while a server that listens but does not answer itself counts as a code failure (source: demo-app/test.js, comments). I ran `npm test` with port 5000 already occupied and got exit code 75, which is the value the workflow turns into `infra`.

Because the script joins the two tests with `&&`, a failing smoke test stops the run and the API test never starts. The exit code of the whole `npm test` is the one from the failing command.

The log lines matter beyond the exit code. When the healer later downloads the job log, the first `##[error]` line and the text above it are what the model reads, so a message that starts with `CODE_FAILURE:` and names the route or the stack trace gives it something concrete (source: tools/nodes/build_ai_prompt.js, `trimCiLog`).

### Why the heal PR gets its own CI run

In the first live round the workflow ran only on pushes to `main`. The fix pull requests were not tested at all, and the team checked them by running the file by hand (source: docs/results/GOLIVE_RESULTS.md, "Still not true"). That left two holes. A fix that parsed but was wrong would sit in an open PR with no red mark. And there was no signal that could start a retry.

Adding the `pull_request` trigger closed both holes. After the change, 5 of 5 heal PRs ran CI and all 5 were green (source: docs/results/GOLIVE_RESULTS.md, Round 2). A red result on a heal PR is now the event that wakes the healer again, and the notify job's condition and the `Heal-Attempt` trailer keep that loop under control.

The `ai-fix-` prefix test has a second purpose, which is to stop the healer from feeding on its own mistakes without limit. A scratch pull request with a broken `test.js` was used to test this gate: the build job failed and the notify job was skipped, because the branch did not start with `ai-fix-` (source: docs/results/GOLIVE_RESULTS.md, Round 2, run 35802293458). Separate scratch runs with trailers 3, 1 and none produced the give-up comment, a webhook call and nothing, in that order (source: docs/results/GOLIVE_RESULTS.md, Round 3, "Gate tests without GPU").
