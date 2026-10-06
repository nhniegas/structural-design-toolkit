"""Several processes for the member designs.

The design of one member does not depend on the others, so the members can
be shared between the cores of the machine. The results are put back in the
order one process gives them and are the same numbers: each member is
designed by the same code on the same forces.

Off unless ``enable()`` was called, which the ``sdt`` program does when it
starts. A script that imports the design functions keeps one process: on
Windows every new process runs the main script again, and only a script
written for that (``if __name__ == "__main__":``) is safe.

``SDT_WORKERS`` sets the number of processes; 1 switches them off.
"""

from __future__ import annotations

import atexit
import os
from concurrent.futures import ProcessPoolExecutor

MAX_WORKERS = 8          # more processes than this gain little and each holds its own data
MIN_MEMBERS = 200        # below this the start of the processes costs more than it saves
CHUNKS_PER_WORKER = 4    # members are handed out in parts this much smaller than a share

_enabled = False
_pool: ProcessPoolExecutor | None = None
_pool_size = 0


def enable(on: bool = True) -> None:
    """Allow several processes (the ``sdt`` program calls this when it starts)."""
    global _enabled
    _enabled = bool(on)
    if not on:
        shutdown()


def workers_for(members: int) -> int:
    """How many processes to design ``members`` members with (1 = this process only)."""
    if not _enabled or members < MIN_MEMBERS:
        return 1
    asked = os.environ.get("SDT_WORKERS", "").strip()
    if asked:
        try:
            return max(1, min(int(asked), 64))
        except ValueError:
            return 1
    cores = os.cpu_count() or 1
    return max(1, min(MAX_WORKERS, cores - 1))


def pool(workers: int) -> ProcessPoolExecutor:
    """The processes, started once and kept for the next design (a design loop
    designs many times; starting them takes a few seconds)."""
    global _pool, _pool_size
    if _pool is None or _pool_size != workers:
        shutdown()
        _pool = ProcessPoolExecutor(max_workers=workers)
        _pool_size = workers
    return _pool


def shutdown() -> None:
    global _pool, _pool_size
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
    _pool, _pool_size = None, 0


def chunks(names: list, workers: int) -> list[list]:
    """``names`` in consecutive parts, in their order: about ``CHUNKS_PER_WORKER``
    parts for each process, so that a part of heavy members does not hold the rest up."""
    count = max(1, min(len(names), workers * CHUNKS_PER_WORKER))
    size, extra = divmod(len(names), count)
    out, start = [], 0
    for index in range(count):
        end = start + size + (1 if index < extra else 0)
        if end > start:
            out.append(names[start:end])
        start = end
    return out


atexit.register(shutdown)
