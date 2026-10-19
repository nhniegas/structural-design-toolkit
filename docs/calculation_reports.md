# The Calculation Reports

`sdt beams`, `sdt columns`, `sdt steel`, `sdt composite`, `sdt wind` and `sdt pilecap` each write a PDF calculation report. All six share one format, described here. What each report holds is in the guide of its command.

Code: `utilities/report_style.py` (the look), `utilities/_calc_report.py` (the beam and column reports).

## The format

**On every page**

| Part | What it shows |
|---|---|
| Header | The `SDT` mark and the title of the report; under it the design code. At the right, "Page x of y", then the model or the section the report is of, and the date. A thin amber rule closes it |
| Footer | The program version, the command that wrote the report and the file name; and the note that the report is a calculation aid |

**On the first page**

| Part | What it shows |
|---|---|
| Title block | The title, and a ruled block: the model or the section, the date, the design code, and the command and version it was prepared with |
| Design basis | The parameters of the design, as labels and values set two to a line |
| Summary of results | One line for each member, with its ratios and its result |

**In the body**

- A section title sits on a grey band with an amber mark at its left. In the beam and column reports each member has its own band.
- A table has its title in bold above it, a grey head and a light rule under each row. Notes are in grey under it.
- A result is a tag: `PASS` or `OK` in green, `FAIL` in red. A ratio is its number; the tag beside it says whether it passes.
- The text and the equations are in one sans typeface, in the graphite of the program.

**Beam and column reports** hold the intermediate and final values of each member in tables, with no equation written out for each member; on a model of a hundred members that would be a report of several hundred pages. The column report keeps the interaction surface of each column, with every combination plotted against it. Each column starts on a new page and takes two: its forces and checks on the first, and on the second its transverse reinforcement with the interaction surface at its right, then its design status. Set under the tables, the surface was pushed to the next page and left a half empty one.

**Steel, composite, wind and pile cap reports** are for one member or one building, and write their equations with the numbers. The pile cap report also has a plan of the cap and a table of every check with its clause. The steel report is set to fit one page; when it cannot, it runs to a second, numbered like the first.

## Tables that are too wide

A sentence in a cell, such as the reason a check does not apply, is wrapped to several lines. A table that is still wider than the page is scaled down to fit it. Nothing runs off the page.

## LaTeX packages

The reports are compiled with `pdflatex`. Beyond the packages of the earlier format, the new one uses `fancyhdr` (the header), `colortbl` (the grey bands in tables), and `tgheros` and `sansmath` (the typeface).

- **`sdt doctor` installs them.** Its line `LaTeX packages` says whether every package the reports use is installed. When MiKTeX lacks some, it names them and asks whether to install them now; it needs the internet and takes about a minute, once for each machine. With TeX Live it gives the `tlmgr install` command to type.
- **A package that can be done without never stops a report.** Without the typeface the report uses Helvetica; without `fancyhdr` it has plain page numbers; without `colortbl` its tables have no grey bands. When only such packages are missing, the report is compiled with MiKTeX told to install nothing, so no window opens and nothing can fail on it.
- **"Page x of y" needs no package.** The report writes the number of its last page itself. Before version 0.5.2 it used `lastpage`, and on a machine where MiKTeX could not install that package the report was not written.
- The header says "Page x of y", so a report is compiled twice.
- A package the reports cannot do without (`booktabs`, `geometry`, `xcolor` and the like, which come with a basic MiKTeX) is still left to MiKTeX to install when it is missing.

## For the source code

- `report_style.new_document(title, code, source, command, file_name)` gives a PyLaTeX document with the header, the footer and the commands of the format. A new report built on it has the look of the others.
- `report_style.title_block`, `tag` and `ratio` give the pieces. `\section` is redefined to the grey band, and the three rules of `booktabs` to the grey head and the light lines, so a report written with PyLaTeX's `Section` and `Tabular` needs no change.
- The colours are constants at the top of `report_style.py`, the same graphite and amber as the icon and the dialogs.

## Not in this format yet

A full report, with the equations of every member of a batch design written out with their numbers, is planned as a choice of the beam and column commands.
