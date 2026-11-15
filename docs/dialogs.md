# The Dialogs

Since v0.18.0 every command asks its questions in **one window**, and ends with a summary window. The windows have the light look of ETABS: white, light grey bands and borders, black Segoe UI text, and the blue of ETABS for the `SDT` mark, the choice made and the button that confirms. The program's icon has the same blue.

Code: `utilities/form_window.py` (the one window) and `utilities/_gui_helpers.py` (the questions, the other windows and the colours). What a command asks, and what it does with the answers, is described in the guide of each command; this page is about how the windows work.

## The command window

When a command starts, one window opens with all its questions, one under the other in a page that scrolls. They are grouped under headings (for example "Beam Design", "Beam Design - Deflection"), in the order the command asks them.

- **It fills in by itself.** Each question shows what it offers: the answer you last gave, the program's default, or the folder you used before. The window opens with every question that can be answered already answered, so you only read it through and change what you want.
- **What still needs you is outlined in blue.** A question with nothing to offer waits: a list with nothing selected yet, a folder you never chose, a box with no value. The line at the bottom says which one ("Waiting for: ...").
- **Answers that depend on others follow them.** Changing an answer asks the questions after it again, with the new answer: a question that no longer applies goes away, one that now applies appears, and a list offers what the new answer allows. For example, with SMRF set to No the question about girders under 4d is not asked. Every other answer you gave is kept. The window may pause a moment while the command reads the model again.
- **Values it does not accept** are said in red under the questions (for example "The zone factor and Ct must be numbers."). The bottom line says the command cannot go on; change the value and it goes on.
- **Run.** When every question is answered, the bottom line says "Check the inputs above, then Run" and the **Run** button turns blue. The model is not changed, and no result is written, before Run (the answers to remember for next time and the setup inputs of the model may be saved).
- **Cancel** (or closing the window) stops the command; nothing has been changed.

After Run the window closes and the command works, with its progress window. A question that can only be asked once the work has started, from what it found, is asked in a window of its own, as before. For example:

| Command | Asked after Run |
|---|---|
| `sdt model` | the framing questions, once the grids, columns and walls are in the model |
| `sdt check` | the seismic system, when the model does not say which one it is |
| `sdt compatibility` | the members to put in the group GRAVITY ONLY, and the live load factor (they need the analysis) |
| `sdt beams`, `sdt design` | the questions after "Tag them now", when members are tagged first |

### The inputs

| Input | How it is used |
|---|---|
| A choice | Click an option. With more than ten options it is a drop-down list |
| Values | One box for each value, with what is offered already typed. A change counts when you press Enter or leave the box |
| A choice for each item | One drop-down list per item (for example the role of each load pattern) |
| Two lists | What is available on the left, what is selected on the right, with their counts. **Add**, **Remove**, **All** or a double click move items; **Apply** gives the selection to the command |
| A folder or a file | The path, and **Browse...** for the Windows picker. A folder chosen once is offered the next time |

### One dialog after the other

`SDT_DIALOGS=1` in the environment keeps the dialogs of earlier versions, one window per question:

```powershell
$env:SDT_DIALOGS = "1"; sdt beams
```

## The other windows

| Window | What it does | Keys |
|---|---|---|
| A choice, values, two lists | The dialogs of one question, with `SDT_DIALOGS=1` or for a question asked after Run | Enter confirms; Escape cancels |
| Progress | The task in the title band, a moving bar, and the step being done under it. It cannot be closed while the command works | |
| Summary | The closing summary of a command in a fixed-width font: what needs attention in red, "Nothing needs attention" in green, notes in grey. The text can be selected | **Copy** puts the whole summary on the clipboard; Enter or Escape closes |
| Warning | A message with a blue sign | Enter or Escape closes |

**File and folder pickers** are those of Windows, so they look like the rest of your system. They carry the program's icon.

## With an answers file

A command run with `--answers file.json` opens no window: each question is printed with the answer it got, and the summary and the warnings are printed in the terminal. `--record file.json` opens the command window as usual and saves the answers it ran with when you press Run. See [Running a command from an answers file](answers_file.md).

## For the source code

- A command asks through the helpers of `_gui_helpers.py` (`select_option`, `enter_values`, `choose_for_each`, `DualListboxSelector`, the folder and file pickers) as before. When it runs from `main.py` without an answers file, `form_window.run` gives each of those calls to the form session, which shows it as a section and returns its answer.
- A changed answer raises `form_window.Restart`, and the command runs again from its start with the answers kept: the questions before its Run point must only read (the model, the settings), never change the model or write a file.
- Each command marks the end of its questions with `ready_to_run()`; the form waits there for Run. `tests/test_form_window.py` checks that every command has one.
- The colours are the constants at the top of `utilities/_gui_helpers.py` (`BG`, `PANEL`, `ACCENT`, `SELECT`, ...). Change them there and every window follows. The icon is drawn by `tools/make_icon.py` in the same blue.
- `sdt composite`, `sdt steel` and `sdt pilecap` open their own window: the inputs, a drawing that follows them, and the results (`utilities/design_window.py`; see the [steel and composite guide](steel_design_documentation.md#the-design-window)). It has the same colours.
