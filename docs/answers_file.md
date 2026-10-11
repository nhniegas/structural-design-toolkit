# Running a Command from an Answers File

Every command asks its inputs in dialogs. With an answers file the dialogs are not shown: each one takes its answer from the file, and the command runs with no one at the screen. The calculation is the same: the file only replaces the dialogs, and gives the command the same values they would return.

Use it to run a command again with the same inputs, to run it on several models, or to leave a long run to itself.

## How to use it

```powershell
sdt beams --record beams.json     # the dialogs are shown; what you answer is saved
sdt beams --answers beams.json    # no dialog: the answers come from the file
```

Both work with every command. The easiest way to get a file is to record one, then change what you need in it.

With `--answers`:

- each dialog is printed with the answer it got, so the run says what it was given;
- warnings and the closing summary are printed in the terminal, not shown in a window;
- no progress window opens;
- answers of the file that no dialog used are listed at the end.

## The file

```json
{
  "folder": "results",
  "unanswered": "stop",
  "answers": [
    {"ask": "SMRF (seismic) design", "answer": "Yes"},
    {"ask": "Beam bars and cover", "answer": {"Beam main bar (mm)": "25"}},
    {"ask": "Levels whose beams get", "answer": ["GF"]}
  ]
}
```

| Key | Meaning |
|---|---|
| `answers` | The answers, in the order the dialogs come |
| `ask` | A piece of the dialog's title or question. The first answer not used yet whose `ask` is found in the dialog is taken. Capitals and spacing do not matter |
| `answer` | What to answer; see the next table |
| `"repeat": true` | In an answer: it serves every dialog it fits, not only one |
| `folder` | Answers every folder question that has no answer of its own. A file to save with no answer goes there, under the name its dialog offers |
| `unanswered` | What happens to a dialog with no answer: `"stop"` (the default) or `"default"` |

| Kind of dialog | `answer` |
|---|---|
| A choice | The option, the start of it, a piece only one option has, or its number from 1. `null` closes the dialog, which cancels the command |
| Boxes to type in | The boxes that change: `{"Beam main bar (mm)": "25"}`. The others keep what the dialog offers. A box is named in full or by a piece only one box has |
| A list to pick from | The items, or `"all"` |
| A folder, a file to open, a file to save | Its path. A relative path starts at the folder of the answers file |

## When a dialog has no answer

- **`"unanswered": "stop"`** ends the command with a message that names the dialog and its choices. Nothing is assumed. This is the default, and what `--record` writes.
- **`"unanswered": "default"`** takes what the dialog offers, which is what pressing Enter would do. Each such answer is printed with `(default)`. What a dialog offers is often what you answered last time on this machine, so the same file can give another run on another machine. Use it to try a command; give every answer for a run you rely on.

A choice that fits no option, or more than one, a box that does not exist, an item that is not in the list, a file that is not there: each stops the command with a message that names what the dialog has. An answer that the command keeps refusing (it asks the same dialog again and again) also stops it.

## What it does not do

- It does not start ETABS or open a model: the model is open in ETABS, as for any command.
- It answers one command. The menu of `sdt` takes no answers file.
- Dialogs that ETABS itself shows are not answered.

For test runs in ETABS that start it, run several commands and compare the results, see [ETABS test runs](etabs_test_runs.md).

## Tests

`tests/test_answers.py`: every kind of dialog, the answers that are refused, recording and playing back, and `sdt wind` run from a file.
