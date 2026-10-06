"""Several processes for the member designs: same rows, same values, same order."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from design import parallel  # noqa: E402


@pytest.fixture(autouse=True)
def _one_process_after():
    yield
    parallel.enable(False)


def test_parts_are_consecutive_and_hold_every_member_once():
    names = [f"B{i:03d}" for i in range(103)]
    parts = parallel.chunks(names, 4)
    assert len(parts) == 16 and [n for part in parts for n in part] == names
    assert max(map(len, parts)) - min(map(len, parts)) <= 1
    assert parallel.chunks(names[:3], 4) == [["B000"], ["B001"], ["B002"]]
    assert parallel.chunks([], 4) == []


def test_one_process_unless_enabled_and_the_model_is_large(monkeypatch):
    monkeypatch.delenv("SDT_WORKERS", raising=False)
    assert parallel.workers_for(5000) == 1                 # a script that imports the design
    parallel.enable()
    assert parallel.workers_for(parallel.MIN_MEMBERS - 1) == 1
    assert 1 <= parallel.workers_for(5000) <= parallel.MAX_WORKERS
    monkeypatch.setenv("SDT_WORKERS", "3")
    assert parallel.workers_for(5000) == 3
    monkeypatch.setenv("SDT_WORKERS", "1")
    assert parallel.workers_for(5000) == 1                 # switched off by the user
    monkeypatch.setenv("SDT_WORKERS", "many")
    assert parallel.workers_for(5000) == 1


def _beams(count: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    props, forces = [], []
    for index in range(count):
        name, span, load = f"B{index:02d}", 5000.0 + 400.0 * index, 40.0 + 6.0 * index
        props.append({"UniqueName": name, "Story": "L2", "SectProp": "B300x600",
                      "SupportStatus": "Supported Both Ends", "Width": 300.0, "Depth": 600.0,
                      "f'c": 28.0, "fy": 414.0, "fyw": 414.0, "dm": 25.0, "ds": 10.0,
                      "dw": 16.0, "cc": 40.0})
        length = span / 1000.0
        for combo, factor in (("GRAV", 1.0), ("HEAVY", 1.3)):
            for step in range(13):
                x = length * step / 12
                forces.append({"UniqueName": name, "Combo": combo, "Station": x * 1000.0,
                               "V2": factor * load * (length / 2 - x),
                               "M3": factor * load * x * (length - x) / 2, "T": 0.0})
    return pd.DataFrame(props), pd.DataFrame(forces)


def test_several_processes_give_the_rows_of_one_process(monkeypatch):
    from design import beam_designer_aci318 as beam

    props, forces = _beams(7)
    props = props.iloc[::-1].reset_index(drop=True)        # not in the order of the results
    one = beam.execute_beam_design(props, forces, False, "GRAV")
    monkeypatch.setattr(parallel, "MIN_MEMBERS", 1)
    monkeypatch.setenv("SDT_WORKERS", "2")
    parallel.enable()
    started = []
    monkeypatch.setattr(parallel, "pool", lambda workers, real=parallel.pool:
                        started.append(workers) or real(workers))
    seen = []
    several = beam.execute_beam_design(props, forces, False, "GRAV", progress=seen.append)
    assert started == [2]                                   # the processes were used
    pd.testing.assert_frame_equal(one, several)
    assert seen and "7 of 7 beams designed" in seen[-1]


def test_one_process_takes_over_when_the_processes_fail(monkeypatch):
    from design import beam_designer_aci318 as beam

    props, forces = _beams(3)
    one = beam.execute_beam_design(props, forces, False, "GRAV")
    monkeypatch.setattr(parallel, "MIN_MEMBERS", 1)
    monkeypatch.setenv("SDT_WORKERS", "2")
    parallel.enable()

    def broken(workers):
        raise OSError("no processes on this machine")

    monkeypatch.setattr(parallel, "pool", broken)
    pd.testing.assert_frame_equal(one, beam.execute_beam_design(props, forces, False, "GRAV"))
