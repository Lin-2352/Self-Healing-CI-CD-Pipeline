# Docs

- [Backend](backend/BACKEND_EXPLAINED.md): a guide to the `ci-heal-backend` experiment, written for readers from any programming background. Start there; it answers which environment to use and links seven chapters: basics, environments and Docker, how it works, a file-by-file code tour, a settings/endpoints/database reference, project status and running it, and a glossary.
- [Setup](SETUP.md): installing and running the n8n healer against the demo repository.
- [Results](results/): one write-up per measurement. Start with `GOLIVE_RESULTS.md` for the live runs, `SHIPPED_QUALITY_EVAL.md` for fix quality with and without the CI log, `MULTIFILE_EVAL.md` for bugs that span files, `MODEL_BAKEOFF.md` for the choice of model and `BACKEND_ON_KAGGLE.md` for ci-heal-backend running on the Kaggle endpoint.
- [Research](research/): Kaggle hardware facts, the quant sweeps and the speed ceiling on 2×T4.
- [Project notes](PROJECT_NOTES.md): the original plan for the n8n healer.
