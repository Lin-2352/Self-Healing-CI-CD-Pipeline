# Placeholders

Every value that has to be filled in later is written `\ph{...}` in the LaTeX source and prints in bold
square brackets in the PDF. Search the source for `\ph{` to find them. The footer of every body page
uses `SCHOOL` and `UNIVERSITY SHORT NAME`; they are set in `main.tex`.

| placeholder | source files | PDF pages |
|---|---|---|
| `SCHOOL` | `frontmatter/certificate.tex`, `main.tex` | 2, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26 ... |
| `UNIVERSITY SHORT NAME` | `main.tex` | 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27 ... |
| `Acknowledgement text: thanks to the ...` | `frontmatter/acknowledgement.tex` | footer or inside text |
| `STUDENT 1 NAME` | `frontmatter/acknowledgement.tex`, `frontmatter/certificate.tex`, `frontmatter/contributors.tex`, `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 2, 3, 4, 6 |
| `STUDENT 2 NAME` | `frontmatter/acknowledgement.tex`, `frontmatter/certificate.tex`, `frontmatter/contributors.tex`, `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 2, 3, 4, 6 |
| `STUDENT 3 NAME` | `frontmatter/acknowledgement.tex`, `frontmatter/certificate.tex`, `frontmatter/contributors.tex`, `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 3, 4, 6 |
| `STUDENT 4 NAME` | `frontmatter/acknowledgement.tex`, `frontmatter/certificate.tex`, `frontmatter/contributors.tex`, `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 2, 3, 4, 6 |
| `STUDENT 5 NAME` | `frontmatter/acknowledgement.tex`, `frontmatter/certificate.tex`, `frontmatter/contributors.tex`, `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 2, 3, 4, 6 |
| `STUDENT 6 NAME` | `frontmatter/acknowledgement.tex`, `frontmatter/certificate.tex`, `frontmatter/contributors.tex`, `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 2, 3, 4, 6 |
| `UNIVERSITY` | `frontmatter/certificate.tex`, `frontmatter/title.tex` | 1, 2 |
| `CITY, STATE - PIN` | `frontmatter/certificate.tex`, `frontmatter/title.tex` | 1, 2 |
| `BRANCH` | `frontmatter/certificate.tex`, `frontmatter/title.tex` | 1, 2 |
| `DATE` | `frontmatter/certificate.tex`, `frontmatter/declaration.tex`, `frontmatter/plagiarism.tex` | 2, 3, 124 |
| `GUIDE NAME` | `frontmatter/certificate.tex`, `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 2, 3 |
| `GUIDE DESIGNATION` | `frontmatter/certificate.tex` | 2 |
| `contribution` | `frontmatter/contributors.tex` | 6 |
| `chapters written` | `frontmatter/contributors.tex` | 6 |
| `Declaration wording to be confirmed ...` | `frontmatter/declaration.tex` | footer or inside text |
| `PLACE` | `frontmatter/declaration.tex` | 3 |
| `ROLL NO 1` | `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 3 |
| `ROLL NO 2` | `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 3 |
| `ROLL NO 3` | `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 3 |
| `ROLL NO 4` | `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 3 |
| `ROLL NO 5` | `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 3 |
| `ROLL NO 6` | `frontmatter/declaration.tex`, `frontmatter/title.tex` | 1, 3 |
| `PERCENTAGE` | `frontmatter/plagiarism.tex` | 124 |
| `TOOL` | `frontmatter/plagiarism.tex` | 124 |
| `Insert the plagiarism report pages here.` | `frontmatter/plagiarism.tex` | 124 |
| `PROJECT TITLE, if different` | `frontmatter/title.tex` | 1 |
| `SCHOOL, IN CAPITALS` | `frontmatter/title.tex` | 1 |
| `INSTITUTE, IN CAPITALS` | `frontmatter/title.tex` | 1 |
| `MONTH YEAR` | `frontmatter/title.tex` | 1 |

Other text that is left for you to write and is marked in the same way: the declaration wording, the
acknowledgement, the project contributors lists, and the plagiarism report pages.
