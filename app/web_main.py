"""`python -m app.web_main <uvicorn's arguments>`: the web's container
command — uvicorn's own command line, unchanged, after the web's log file
is opened (O1 of the 2026-11-21 review, app/log_file.py): its stdout and
stderr are written through to `<log_dir>/web.log` BEFORE uvicorn configures
its logging, so uvicorn's handlers (bound to the streams at that moment)
write through it as well as every print of the app."""
from __future__ import annotations

import signal
import sys


def _stopped_while_loading(signum, _frame) -> None:
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    # PID 1 answers SIGTERM while uvicorn and the app load (the kernel gives
    # PID 1 no default action); uvicorn installs its own once it serves
    signal.signal(signal.SIGTERM, _stopped_while_loading)
    from . import log_file
    log_file.tee_std("web")
    import uvicorn
    sys.argv = ["uvicorn", *(sys.argv[1:] if argv is None else argv)]
    return uvicorn.main()


if __name__ == "__main__":
    sys.exit(main())
