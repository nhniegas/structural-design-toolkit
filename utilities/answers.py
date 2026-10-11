"""Answers to the dialogs from a file, so a command can run with no one at the screen.

Every command asks its inputs in dialogs (``utilities/_gui_helpers.py``). With
an answers file the dialogs are not shown: each one takes its answer from the
file. Two uses:

* running a command again with the same inputs, or on several models;
* test runs in ETABS, which no longer need someone to click.

    sdt beams --record beams.json     the dialogs are shown; what you answer is saved
    sdt beams --answers beams.json    no dialog: the answers come from the file

The file is JSON::

    {
      "folder": "results",
      "unanswered": "stop",
      "answers": [
        {"ask": "SMRF (seismic) design", "answer": "Yes"},
        {"ask": "Beam bars and cover", "answer": {"Beam main bar (mm)": "25"}},
        {"ask": "Levels whose beams get", "answer": ["GF"]}
      ]
    }

``ask`` is a piece of the dialog's title or question; the first answer not
used yet whose ``ask`` is found in the dialog is taken (``"repeat": true``
lets one answer serve every dialog it matches). ``answer`` is:

* a choice: the option, the start of it, or its number from 1; ``null``
  closes the dialog, which cancels the command;
* boxes to type in: the boxes that change, ``{label: text}``; the others keep
  what the dialog offers;
* a list to pick from: the items, or ``"all"``;
* a folder or a file: its path. A relative path starts at the folder of the
  answers file.

``folder`` answers every folder question, and gives the folder of a file to
save, when no answer is listed for it. ``unanswered`` says what happens to a
dialog with no answer: ``"stop"`` (the default) ends the command with a
message that names the dialog; ``"default"`` takes what the dialog offers,
which is what pressing Enter would do.

Each dialog and the answer it got is printed, marked when it was a default,
so the run says what it was given. Warnings and the closing summary are
printed in place of their windows.
"""

from __future__ import annotations

import json
import os

ASK = object()          # no answers file: show the dialog
STOP, DEFAULT = "stop", "default"
ALL = "all"
MAX_REPEATS = 20        # the same dialog asked this often is a loop on a bad answer


class AnswersError(RuntimeError):
    """An answers file that cannot answer a dialog, or answers it wrongly."""


class _Session:
    """One answers file being used, or being recorded."""

    def __init__(self, entries: list[dict] | None = None, folder: str | None = None,
                 unanswered: str = STOP, base: str = "", recording: str | None = None,
                 source: str = ""):
        self.entries = [dict(entry) for entry in entries or []]
        self.used = [False] * len(self.entries)
        self.folder = folder
        self.unanswered = unanswered
        self.base = base
        self.recording = recording
        self.source = source
        self.transcript: list[str] = []
        self.summaries: list[str] = []
        self.warnings: list[str] = []
        self.asked: dict[str, int] = {}


_session: _Session | None = None


# =============================================================================
# START AND STOP
# =============================================================================
def load(path: str) -> None:
    """Answer the dialogs from the file ``path`` until ``stop``."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as error:
        raise AnswersError(f"The answers file cannot be read: {error}") from None
    except ValueError as error:
        raise AnswersError(f"The answers file {path} is not valid JSON: {error}") from None
    use(data, os.path.dirname(os.path.abspath(path)), os.path.basename(path))


def use(data: dict | list, base: str = "", source: str = "answers") -> None:
    """Answer the dialogs from ``data`` (the content of an answers file).

    ``base`` is the folder that relative paths start at.
    """
    global _session
    if isinstance(data, list):
        data = {"answers": data}
    if not isinstance(data, dict):
        raise AnswersError("An answers file holds an object with \"answers\", or a list of them.")
    entries = data.get("answers") or []
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or "ask" not in entry or "answer" not in entry:
            raise AnswersError(f"Answer {number} of {source} needs \"ask\" and \"answer\".")
    unanswered = str(data.get("unanswered", STOP)).strip().lower()
    if unanswered not in (STOP, DEFAULT):
        raise AnswersError(f"\"unanswered\" is \"{STOP}\" or \"{DEFAULT}\", not {unanswered!r}.")
    _session = _Session(entries, data.get("folder"), unanswered, base, source=source)


def record(path: str) -> None:
    """Show the dialogs as usual and save what is answered to ``path``."""
    global _session
    _session = _Session(recording=os.path.abspath(path), source=os.path.basename(path))
    _write()


def stop() -> list[str]:
    """Go back to the dialogs. Returns the ``ask`` of the answers that were not used."""
    global _session
    left = unused()
    _session = None
    return left


def replaying() -> bool:
    """Whether the dialogs are being answered from a file."""
    return _session is not None and _session.recording is None


def recording() -> bool:
    return _session is not None and _session.recording is not None


def unused() -> list[str]:
    if _session is None or _session.recording:
        return []
    return [str(entry["ask"]) for entry, used in zip(_session.entries, _session.used)
            if not used and not entry.get("repeat")]


def transcript() -> list[str]:
    """Every dialog of the run with the answer it got."""
    return list(_session.transcript) if _session is not None else []


def summaries() -> list[str]:
    """The closing summaries printed in place of their windows."""
    return list(_session.summaries) if _session is not None else []


def warnings() -> list[str]:
    """The warnings printed in place of their windows."""
    return list(_session.warnings) if _session is not None else []


# =============================================================================
# FINDING AN ANSWER
# =============================================================================
def _plain(text) -> str:
    return " ".join(str(text).split()).casefold()


def _short(text, limit: int = 90) -> str:
    line = next((part.strip() for part in str(text).split("\n") if part.strip()), "")
    return line if len(line) <= limit else line[:limit - 3].rstrip() + "..."


def _key(title: str, prompt: str) -> str:
    """What a recorded answer is found by: the title and the first line of the question."""
    title, first = str(title).strip(), _short(prompt)
    return f"{title}: {first}" if title and first else (title or first)


def _find(title: str, prompt: str):
    """(answer, True) of the first unused entry that fits the dialog, or (None, False)."""
    session = _session
    dialog = _plain(f"{title}: {prompt}")
    name = _key(title, prompt)
    session.asked[name] = session.asked.get(name, 0) + 1
    if session.asked[name] > MAX_REPEATS:
        raise AnswersError(
            f"The dialog \"{name}\" was asked {MAX_REPEATS} times: the answer it gets is not "
            f"accepted by the command. Correct it in {session.source}.")
    for index, entry in enumerate(session.entries):
        if session.used[index] and not entry.get("repeat"):
            continue
        if _plain(entry["ask"]) in dialog:
            session.used[index] = True
            return entry["answer"], True
    return None, False


def _no_answer(title: str, prompt: str, detail: str = "") -> AnswersError:
    return AnswersError(
        f"No answer in {_session.source} for the dialog \"{_key(title, prompt)}\"."
        + (f" {detail}" if detail else "")
        + " Add one, or set \"unanswered\": \"default\" to take what the dialogs offer.")


def _say(title: str, prompt: str, answer, default: bool = False) -> None:
    shown = "closed (cancel)" if answer is None else (
        json.dumps(answer, ensure_ascii=False) if isinstance(answer, (dict, list)) else str(answer))
    line = f"[answers] {_key(title, prompt)} -> {shown}" + (" (default)" if default else "")
    _session.transcript.append(line)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)


def _path(value) -> str:
    """A path of the file: variables expanded, relative paths from the file's folder."""
    path = os.path.expandvars(os.path.expanduser(str(value)))
    if not os.path.isabs(path):
        path = os.path.join(_session.base or os.getcwd(), path)
    return os.path.normpath(path)


def _write() -> None:
    session = _session
    data = {"unanswered": STOP, "answers": session.entries}
    os.makedirs(os.path.dirname(session.recording), exist_ok=True)
    with open(session.recording, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def keep(title: str, prompt: str, answer) -> None:
    """While recording: save the answer a dialog was given. Nothing otherwise."""
    if not recording():
        return
    _session.entries.append({"ask": _key(title, prompt), "answer": answer})
    _write()   # after every dialog, so a run that stops keeps what was answered


# =============================================================================
# ONE FUNCTION FOR EACH KIND OF DIALOG
# =============================================================================
def option(title: str, prompt: str, options: list[str], default_index: int = 0):
    """The answer to a choice: the option, None (closed), or ``ASK`` (show the dialog)."""
    if not replaying():
        return ASK
    answer, found = _find(title, prompt)
    if not found:
        if _session.unanswered != DEFAULT:
            raise _no_answer(title, prompt, "Its choices: " + " | ".join(options) + ".")
        chosen = options[min(max(default_index, 0), len(options) - 1)]
        _say(title, prompt, chosen, default=True)
        return chosen
    if answer is None:
        _say(title, prompt, None)
        return None
    chosen = _match_option(answer, options)
    if chosen is None:
        raise AnswersError(
            f"The answer {answer!r} to \"{_key(title, prompt)}\" is not one of its choices "
            "(or fits more than one): " + " | ".join(options) + ".")
    _say(title, prompt, chosen)
    return chosen


def _match_option(answer, options: list[str]) -> str | None:
    """The option an answer means: its number from 1, the option itself, or the
    only option that starts with it, or else the only one that contains it."""
    if isinstance(answer, bool):
        return None
    if isinstance(answer, int):
        return options[answer - 1] if 1 <= answer <= len(options) else None
    wanted = _plain(answer)
    for test in (lambda o: _plain(o) == wanted, lambda o: _plain(o).startswith(wanted),
                 lambda o: wanted in _plain(o)):
        fits = [option for option in options if test(option)]
        if len(fits) == 1:
            return fits[0]
        if fits:
            return None
    return None


def values(title: str, prompt: str, labels: list[str], defaults: dict[str, str] | None = None):
    """The answer to a dialog of boxes: {label: text}, None (closed), or ``ASK``."""
    if not replaying():
        return ASK
    out = {label: str((defaults or {}).get(label, "")) for label in labels}
    answer, found = _find(title, prompt)
    if not found:
        if _session.unanswered != DEFAULT:
            raise _no_answer(title, prompt, "Its boxes: " + " | ".join(labels) + ".")
        _say(title, prompt, out, default=True)
        return out
    if answer is None:
        _say(title, prompt, None)
        return None
    if not isinstance(answer, dict):
        if len(labels) != 1:
            raise AnswersError(f"The answer to \"{_key(title, prompt)}\" must be an object "
                               "{box: text}; its boxes: " + " | ".join(labels) + ".")
        answer = {labels[0]: answer}
    for name, text in answer.items():
        label = _match_option(name, labels)
        if label is None:
            raise AnswersError(f"\"{_key(title, prompt)}\" has no box {name!r} (or more than "
                               "one fits); its boxes: " + " | ".join(labels) + ".")
        out[label] = text if isinstance(text, str) else f"{text:g}" if isinstance(
            text, float) else str(text)
    _say(title, prompt, {label: out[label] for label in labels
                         if out[label] != str((defaults or {}).get(label, ""))} or "as offered")
    return out


def items(title: str, available: list[str]):
    """The answer to a list to pick from: the items picked, or ``ASK``."""
    if not replaying():
        return ASK
    answer, found = _find(title, "")
    if not found:
        if _session.unanswered != DEFAULT:
            raise _no_answer(title, "", f"It lists {len(available)} items: "
                             + " | ".join(map(str, available[:12]))
                             + (" | ..." if len(available) > 12 else "") + ".")
        _say(title, "", f"all {len(available)} items", default=True)
        return list(available)
    if answer is None:
        _say(title, "", None)
        return []
    if isinstance(answer, str) and _plain(answer) == ALL:
        _say(title, "", f"all {len(available)} items")
        return list(available)
    wanted = [answer] if isinstance(answer, str) else list(answer)
    missing = [str(item) for item in wanted if item not in available]
    if missing:
        raise AnswersError(f"\"{title}\" does not list: " + ", ".join(missing[:8]) + ". It lists: "
                           + " | ".join(map(str, available[:20]))
                           + (" | ..." if len(available) > 20 else "") + ".")
    _say(title, "", wanted)
    return wanted


def folder(title: str):
    """The answer to a folder question: its path, "" (closed), or ``ASK``."""
    if not replaying():
        return ASK
    answer, found = _find(title, "")
    if not found:
        if _session.folder is None:
            raise _no_answer(title, "", "Give its path, or a \"folder\" for every folder question.")
        answer = _session.folder    # the "folder" of the file is an answer, not a default
    if answer is None:
        _say(title, "", None)
        return ""
    path = _path(answer)
    _say(title, "", path)
    return path


def save_path(title: str, default_name: str, extension: str):
    """The answer to a file to save: its path, "" (closed), or ``ASK``."""
    if not replaying():
        return ASK
    answer, found = _find(title, "")
    by_default = False
    if not found:
        if _session.folder is None:
            raise _no_answer(title, "", "Give its path, or a \"folder\" to save it in under the "
                             f"name the dialog offers ({default_name}{extension}).")
        answer, by_default = os.path.join(str(_session.folder), default_name), True
    if answer is None:
        _say(title, "", None)
        return ""
    path = _path(answer)
    if extension and not path.lower().endswith(extension.lower()):
        path += extension
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _say(title, "", path, default=by_default)
    return path


def open_path(title: str):
    """The answer to a file to open: its path, "" (closed), or ``ASK``."""
    if not replaying():
        return ASK
    answer, found = _find(title, "")
    if not found:
        raise _no_answer(title, "", "Give the path of the file to open.")
    if answer is None:
        _say(title, "", None)
        return ""
    path = _path(answer)
    if not os.path.isfile(path):
        raise AnswersError(f"The file answered to \"{title}\" does not exist: {path}")
    _say(title, "", path)
    return path


def warning(message: str, title: str) -> bool:
    """With an answers file a warning is printed, not shown in a window. True when it was."""
    if not replaying():
        return False
    _session.warnings.append(f"{title}: {message}")
    print(f"[answers] warning - {title}: {message}", flush=True)
    return True


def summary(text: str, title: str) -> bool:
    """With an answers file the closing summary is printed, not shown in a window."""
    if not replaying():
        return False
    _session.summaries.append(text)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)
    return True
