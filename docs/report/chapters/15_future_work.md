## Future work

The items below follow from the limits in Chapter 14, in the order we would do them.

1. **Close the open backend findings** that can lose a delivery or corrupt state: the SQLite lock held across awaits (move intake off the event loop, or use Postgres), the stuck `processing` rows, the rollback that loses a pull-request record and the unbounded rate-limit sleeps (docs/results/BACKEND_AUDIT.md).
2. **Give every workflow node an error path.** The seven nodes without one should continue as data into the existing failure comment, so that a GitHub refusal produces a message and not a silent red run. Add the attempt cap inside n8n as well, so that it does not depend on the notify job alone.
3. **A real fallback model.** Bind the OpenAI node to a credential that works for webhook-triggered runs, or replace it with a second self-hosted endpoint, and then record a heal that the fallback actually produced. Until then the fallback is wiring, not capability.
4. **Make the endpoint key random per boot.** The notebook could generate a key at start and post it to the Set Model URL webhook together with the address, so that no secret is committed and a stale address is useless.
5. **Multi-file bugs.** The caller gap needs a retrieval step that is more than the stack trace and the changed files: for example the files that import or call the changed function. The evaluation harness already has the cases for it (eval/cases/multifile_cases.json).
6. **Judge the fix, not only the tests.** A second model call or a simple check could compare the proposed edit with the deleted or changed lines in the breaking commit and flag a fix that removes behaviour the commit did not touch, as in the recorded run.
7. **More than one repository and language.** Run the same measurements on a second demo application and on a Python project, to see which of the numbers in Chapter 9 depend on the demo.
8. **Cover build and install failures.** Extend the notify job's classification beyond failing tests, with the same refusal for infrastructure problems.
9. **Merge the work into the main branch** of the repository once the open items above are decided. The branches `harsh/kaggle-backend-integration` and `harsh/launcher-and-demo-app` hold everything described in this report.
