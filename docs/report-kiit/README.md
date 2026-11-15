# KIIT-format project report

A LaTeX project report for the self-healing CI/CD pipeline, in the layout of the reference report described in
`REFERENCE_NOTES.md`. The finished PDF is [`PROJECT_REPORT_KIIT.pdf`](PROJECT_REPORT_KIIT.pdf); the source is
`main.tex` and the files it includes.

## Build

```text
python docs/report-kiit/build.py --check     generate chapters, compile with latexmk, check the log and every page
python docs/report-kiit/make_placeholders.py list the placeholders and the pages they print on
python docs/report-kiit/tests/test_check_layout.py
```

The report is compiled with XeLaTeX (`% !TEX program = xelatex` at the top of `main.tex`, also honoured by Overleaf). The main font is Times New Roman where it is installed, and TeX Gyre Termes (a metric-compatible Times clone) otherwise.

`build.py` fails on any LaTeX error, an overfull box wider than 1 pt, an undefined reference or citation, or a
float that is too large. `check_layout.py` renders each page with `pdftoppm` and reports text within 3 mm of the
page border, a missing border, holes in the middle of a page, and marks in the side margins. MiKTeX and
Strawberry Perl are expected on `D:`.

## Layout of this folder

| path | contents |
|---|---|
| `main.tex` | preamble, page border, header and footer, the order of everything |
| `frontmatter/` | title page, certificate, declaration, acknowledgement, abstract, contributors, plagiarism page |
| `chapters/` | chapters 1 to 12 and the appendix. Files starting with `% GENERATED` are made from the Markdown chapters in `docs/report/chapters/` by `build_chapters.py`; the others are written by hand |
| `figures/` | TikZ diagrams (`fig_*.tex`) and the screenshots used in Chapter 9 |
| `images/` | the university logo (source in `assets/SOURCES.md`) |
| `bibliography.tex`, `CITATIONS.md` | the reference list and the Google Scholar record behind each scholarly entry |
| `PLACEHOLDERS.md` | every value to fill in, with the pages it prints on |
| `REFERENCE_NOTES.md`, `reference/`, `compare/` | what was read from the reference report, its page images, and side-by-side images with this report |
| `build.py`, `build_chapters.py`, `md2tex.py`, `make_tree.py`, `make_placeholders.py`, `check_layout.py`, `tests/` | tooling |
| `output/` | LaTeX build output (not committed) |

## Editing

Change the Markdown chapters in `docs/report/chapters/` for generated chapters, or the `.tex` files directly for
hand-written ones, then run `build.py --check`. Fill in the placeholders in `frontmatter/*.tex` and in `main.tex`
(footer); search for `\ph{`.
