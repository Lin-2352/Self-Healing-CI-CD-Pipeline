# Old documents

These files were correct when they were written and have been replaced by newer ones. They stay here for the history of the project, and nothing in the rest of the documentation depends on them. Do not take numbers or commands from them without checking the current docs.

| file | what it was | why it moved | read instead |
|---|---|---|---|
| `PROJECT_NOTES.md` | Notes from the start of the project: the decision to serve the model from a Kaggle notebook instead of a hosted API, the documents of the first model sweep and the first serving plan. | The serve flags, context size and the 'accept about 9 tok/s' advice in it are not the production ones, and the workflow it describes was replaced. | `../PROJECT_REPORT.md`, `../architecture/kaggle-endpoint.md` |
| `SELFHEAL_E2E_RESULTS.md` | The first end-to-end baseline (2026-09-07) on a 13-node workflow with whole-file output, about 104 s from push to PR and about 12 tok/s. | Every figure in it was beaten by the later rounds, and several helper scripts it names are no longer in the repository. | `../results/GOLIVE_RESULTS.md` |
| `QWEN38_SPEED_CEILING.md` | An analysis that concluded 30 tok/s was unreachable on two T4s and recommended accepting about 9 tok/s. | Measurements with tensor split and MTP reached 22 to 27 tok/s, which refuted it. | `../results/SPEED_LEVERS_EVAL.md` |
| `QWEN38_SWEEP_RESULTS.md` | The first quantization sweep, with the serve command and speed of that time. | The model choice it made still stands, but the serve command and speeds are out of date and its raw data file is not in the repository. | `../results/MODEL_BAKEOFF.md`, `../results/SPEED_LEVERS_EVAL.md` |
