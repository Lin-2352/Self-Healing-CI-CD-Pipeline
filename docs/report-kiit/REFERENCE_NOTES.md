# Notes on the reference report

The layout of this report follows a read-only Overleaf project, "Splitora Project Documentation" (a final-year
report in the KIIT format, 77 PDF pages), which was opened in the browser on 2026-11-16. What follows is what was
read from its `main.tex` and from its compiled PDF. Screenshots of its pages are in `reference/`, and side-by-side
images of the same kinds of page from this report are in `compare/`.

## What `main.tex` sets

| item | reference value | used here |
|---|---|---|
| class | `report`, 12 pt, A4 | same |
| margins | left and right 1.9 cm, top 2.5 cm, bottom 1.9 cm, headheight 15 pt, headsep 0.6 cm, `includefoot` | same |
| spacing | `\onehalfspacing`, `\tolerance=10000`, `\hbadness=10000`, `\emergencystretch=3em`, `\sloppy` | same |
| packages | graphicx, hyperref, fancyhdr, titlesec, setspace, tabularx, eso-pic, microtype (expansion and protrusion off), listings, xcolor, booktabs, float, seqsplit, tikz, pgfplots | same, plus longtable, array, enumitem, lmodern |
| links | `colorlinks`, black internal links, blue URLs | same |
| border | 1.5 pt rectangle, 0.7 cm inside the paper edge, on every page (eso-pic + tikz overlay) | same code |
| header | project name, bold, right; 0.4 pt rule | same |
| footer | school and university text left, page number right; 0.4 pt rule | same, with placeholders |
| front matter | title page, certificate, declaration, acknowledgement, abstract, project contributors, table of contents, list of figures, list of tables; Roman page numbers | same order |
| body | 12 chapters and an appendix, Arabic page numbers | same; chapter list in `main.tex` |
| end matter | bibliography (`thebibliography`, IEEE-like entries), plagiarism report page | same |

## What the PDF looks like

- The title page is centred: report title, "on", the project name in quotes, "Submitted to", the university, the
  degree and branch in capitals, "BY", a two-column list of names and roll numbers, the guide, the logo, the school
  and institute in capitals, the month and year.
- Chapter titles use the standard `report` style (a "Chapter N" line, then the title). Sections are bold and numbered.
- Tables use `booktabs` rules and a caption below; diagrams are drawn in TikZ with rounded boxes (green for start
  and end, lavender for processing steps) and a circle for the process in data flow diagrams; code listings sit in a
  light grey frame.
- A table of contents line for each section, with dotted leaders.

## Where this report differs on purpose

- The reference has 16 bibliography entries and no `\cite` in the text; this report cites in the text and checks
  each scholarly source in Google Scholar (`CITATIONS.md`).
- The reference uses several inflated words; this report does not copy its wording, only its layout.
- Tables here are `longtable`s with the caption below, so that a long table never leaves a half-empty page.
- Names, roll numbers and everything personal are placeholders (`PLACEHOLDERS.md`).
