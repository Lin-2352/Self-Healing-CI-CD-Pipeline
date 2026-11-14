"""Drive `Heal Outcome` (the real node body): every GitHub answer it can get, each to its own verdict.

Round 3 went live with a retry path that pushed correctly and was still marked FAILED here,
because Open Pull Request (head == base on a retry) got 422 "No commits between X and X".
The replayed 422 bodies below are the exact shapes GitHub returned.

Run:  python tools/tests/test_heal_outcome.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # tools/, for heal_harness
from heal_harness import run_node, first_json          # noqa: E402

NO_COMMITS = {"error": {"message": '422 - "{\\"message\\":\\"Validation Failed\\",\\"errors\\":[{\\"resource\\":'
                                   '\\"PullRequest\\",\\"code\\":\\"custom\\",\\"message\\":\\"No commits between '
                                   'ai-fix-x-abc1234 and ai-fix-x-abc1234\\"}]}"'}}
EXISTS = {"error": {"message": '422 - "{\\"message\\":\\"Validation Failed\\",\\"errors\\":[{\\"message\\":'
                               '\\"A pull request already exists for Lin-2352:ai-fix-x-abc1234.\\"}]}"'}}
fails = 0


def case(label, item, attempt, want_status=None, want_error=None):
    global fails
    res = run_node("heal_outcome", {"input": item, "nodes": {"Branch Ready": {"existed": attempt > 1},
                                                             "Extract AI Response": {"attempt": attempt}}})
    j = first_json(res)
    msg = (res.get("error") or {}).get("message", "")
    ok = (j and j.get("status") == want_status) if want_status else (not res["ok"] and want_error in msg)
    fails += not ok
    print("  %-52s %s  <- %s" % (label, "PASS" if ok else "FAIL", (j or {}).get("status") or msg[:70]))


case("first heal, PR created", {"html_url": "https://x/pull/9", "number": 9}, 1, want_status="pr_opened")
case("retry 2, 422 No commits between (head == base)", NO_COMMITS, 2, want_status="retry_pushed")
case("retry 3, 422 PR already exists", EXISTS, 3, want_status="retry_pushed")
case("re-run of the same commit (attempt 1), PR exists", EXISTS, 1, want_status="pr_already_open")
case("attempt 1 + No commits between is a REAL failure", NO_COMMITS, 1, want_error="Open Pull Request failed")
case("401 bad token is a real failure", {"error": {"message": "401 - Bad credentials"}}, 2,
     want_error="Open Pull Request failed")
print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
