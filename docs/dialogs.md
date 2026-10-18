# The Dialogs

Every command asks its questions in small windows, and ends with a summary window. They share one look, that of the program's icon: a graphite window, light text, and amber for the `SDT` mark, the choice made and the button that confirms. A window of the toolkit is told at a glance from one of ETABS, over which it opens.

Code: `utilities/_gui_helpers.py`. What a dialog asks, and what it returns to the command, is described in the guide of each command; this page is about how the windows work.

## The windows

| Window | What it does | Keys |
|---|---|---|
| A choice | One question, its explanation in grey under it, and the options. The option chosen is marked in amber | Up and Down move the choice; Enter confirms; Escape cancels; a double click on an option confirms it |
| Values | One text box for each value, with what is offered already typed. The box being typed in has an amber border | Tab moves to the next box; Enter confirms; Escape cancels |
| Two lists | What is available on the left, what is selected on the right, each with its count | Shift or Ctrl with a click selects several; a double click moves an item; Enter confirms; Escape cancels |
| Progress | The task in the title band, a moving bar, and the step being done under it. It cannot be closed while the command works | |
| Summary | The closing summary of a command in a fixed-width font: what needs attention in red, "Nothing needs attention" in green, notes in grey. The text can be selected | **Copy** puts the whole summary on the clipboard; Enter or Escape closes |
| Warning | A message with an amber sign | Enter or Escape closes |

**Cancel.** Every dialog that asks has a Cancel button. Cancel, Escape and closing the window do the same thing: the command stops and changes nothing.

**Two lists with nothing selected.** Confirm then says, in red under the lists, to select at least one item, and the window stays open.

**File and folder pickers** are those of Windows, so they look like the rest of your system. They carry the program's icon.

## With an answers file

A command run with `--answers file.json` opens no window: each question is printed with the answer it got, and the summary and the warnings are printed in the terminal. See [Running a command from an answers file](answers_file.md).

## For the source code

- The colours and fonts are the constants at the top of `utilities/_gui_helpers.py` (`BG`, `AMBER`, `HEADING_FONT`, ...), the same graphite and amber as `tools/make_icon.py`. Change them there and every window follows.
- A window is made with `_window`, given its band with `_header` and its buttons with `_footer`, and shown with `_place`. A new dialog built from these has the look of the others.
- The headings use Bahnschrift, the font of the icon's letters, and the text Segoe UI; both come with Windows. Without them Tk uses its default font.
- The packaged program carries `assets/sdt.ico` for the window icons (`build_exe.ps1`).
- `sdt composite`, `sdt steel` and `sdt pilecap` open one larger window instead of dialogs: the inputs, a drawing that follows them, and the results (`utilities/design_window.py`; see the [steel and composite guide](steel_design_documentation.md#the-design-window)).
