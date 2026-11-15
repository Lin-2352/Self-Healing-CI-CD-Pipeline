# Documentation

Start with the project report if you want the whole story, or pick the folder for the job at hand.

| I want to | read |
|---|---|
| understand the project from start to end | [PROJECT_REPORT.md](PROJECT_REPORT.md) (also as [PROJECT_REPORT.pdf](PROJECT_REPORT.pdf)) |
| read the report in the KIIT layout (LaTeX, with placeholders for names) | [report-kiit/PROJECT_REPORT_KIIT.pdf](report-kiit/PROJECT_REPORT_KIIT.pdf) and [report-kiit/README.md](report-kiit/README.md) |
| see a heal happen | the captioned video [media/demo.mp4](media/demo.mp4) and the screenshots in [media/shots/](media/shots/) |
| set the system up | [guides/SETUP.md](guides/SETUP.md), then [guides/cloud-n8n.md](guides/cloud-n8n.md) if n8n runs in the cloud |
| start and stop the parts | [guides/launcher.md](guides/launcher.md) and `../launcher/README.md` |
| run the tests | [guides/testing.md](guides/testing.md) |
| know how the workflow works, node by node | [architecture/n8n-workflow.md](architecture/n8n-workflow.md) |
| know how the model endpoint works | [architecture/kaggle-endpoint.md](architecture/kaggle-endpoint.md) |
| learn about the backend service | [backend/BACKEND_EXPLAINED.md](backend/BACKEND_EXPLAINED.md), written for readers from any programming background, with seven chapters |
| check a number | [results/](results/): one write-up per measurement |
| read the background research | [research/](research/): the Kaggle hardware, the quantization sweeps and what was learned about the model |
| see what was found wrong in the backend | [results/BACKEND_AUDIT.md](results/BACKEND_AUDIT.md) |
| find a document that used to be here | [old/](old/README.md): superseded notes, each with the reason and a pointer to its replacement |

## Results

| write-up | question |
|---|---|
| [GOLIVE_RESULTS.md](results/GOLIVE_RESULTS.md) | how fast and how often does the live pipeline heal, round by round |
| [SHIPPED_QUALITY_EVAL.md](results/SHIPPED_QUALITY_EVAL.md) | does the CI log in the prompt help, and what does thinking add |
| [MULTIFILE_EVAL.md](results/MULTIFILE_EVAL.md) | can it heal bugs that span files |
| [MODEL_BAKEOFF.md](results/MODEL_BAKEOFF.md) | is another Qwen3.8 build better than the one in use |
| [DIFF_FORMAT_EVAL.md](results/DIFF_FORMAT_EVAL.md) | which reply format is best: whole file, unified diff or SEARCH/REPLACE |
| [SPEED_LEVERS_EVAL.md](results/SPEED_LEVERS_EVAL.md) | what do tensor split and multi-token prediction buy |
| [SPEED_QUALITY_EVAL.md](results/SPEED_QUALITY_EVAL.md) | do the smaller or specially tuned builds lose quality |
| [BACKEND_ON_KAGGLE.md](results/BACKEND_ON_KAGGLE.md) | does ci-heal-backend work against the self-hosted model |
| [BACKEND_AUDIT.md](results/BACKEND_AUDIT.md) | what did a review of ci-heal-backend find |

## Keeping the documents right

`python tools/check_docs.py` checks links, file paths named in the text, and a few style rules. `python docs/report/build.py` rebuilds the report from `docs/report/chapters/`; edit the chapters, not the generated `PROJECT_REPORT.md`.
