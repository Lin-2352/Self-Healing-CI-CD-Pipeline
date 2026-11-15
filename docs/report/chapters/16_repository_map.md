## Repository map and reproduction

### Layout

| path | contents |
|---|---|
| `README.md` | overview, layout and the shortest way to start |
| `launcher/` | double-click scripts: `start/` brings parts up, `end/` stops them (launcher/README.md) |
| `ci-heal-backend/` | the FastAPI healer, with its own tests, `.env.example`, Dockerfile and `docs/LLM_CONTRACT.md` |
| `kaggle/endpoint/` | the endpoint kernel source (`deploy_src.py`), the model registry (`models.json`) and the generated push folder |
| `kaggle/tools/` | status, push, build, watch and log tools for the Kaggle kernels |
| `kaggle/evals/` | the generated kernels of the three evaluation studies that ran on Kaggle |
| `kaggle/notebooks/` | a snapshot of the notebook as it is on Kaggle |
| `kaggle/logs/` | boot and run logs of every kernel version, by date |
| `n8n/` | `self_healing_pipeline.json`, the workflow (generated) |
| `tools/` | the workflow generator, the node bodies in `nodes/`, the node test suites in `tests/`, the real-engine rig in `n8n_rig/`, the live-run and setup scripts in `golive/`, the demo video builder in `media/` |
| `eval/` | bug cases, eval drivers, scorers and the stored results in `runs/` |
| `demo-app/` | the Express app that CI runs on, with `.github/workflows/demo-ci.yml` at the repository root |
| `docs/` | everything below |
| `run_tests.py` | one command for all local checks |

### Documentation

| path | contents |
|---|---|
| `docs/PROJECT_REPORT.md`, `PROJECT_REPORT.pdf` | this report, assembled from `docs/report/chapters/` |
| `docs/report/` | chapter sources, the Markdown to LaTeX converter, the build script and the LaTeX output |
| `docs/architecture/` | the n8n workflow node by node, and the model endpoint |
| `docs/guides/` | setup, cloud n8n, launcher, testing |
| `docs/backend/` | the seven-chapter walkthrough of `ci-heal-backend` |
| `docs/results/` | one write-up per measurement and the backend audit |
| `docs/research/` | notes on the Kaggle environment and the model |
| `docs/media/` | screenshots and the demo video |
| `docs/old/` | superseded documents, with the reason for each |

### Reproducing the checks

From the repository root, in PowerShell:

```text
python run_tests.py                 node suites, eval scorers, generator check (13 checks with --rig)
python run_tests.py --rig           adds the real-n8n engine rig
cd ci-heal-backend ; pytest ; ruff check .
python docs/report/build.py         rebuilds this report and its PDF
python tools/media/build_demo_video.py docs/media/demo_spec.json
```

The eval scripts and some tests build trees in `app/`, a separate clone of the demo repository that is not part of this repository (it is listed in `.gitignore`), and need `express` installed there. Starting the model, the workflow and the backend is described in Chapter 13.

### Evidence index

Every number in the report carries a source note. The files that hold the raw evidence are:

| evidence | location |
|---|---|
| test outputs of the last full run | `eval/runs/testall/` |
| measured model data of each study | `eval/runs/` (`kout_*`, `golive*`) |
| Kaggle boot logs and speed numbers | `kaggle/logs/endpoint/` |
| live-run screenshots | `docs/media/shots/` |
| backend audit and its fixes | `docs/results/BACKEND_AUDIT.md`, commit `4bfd37d` |
