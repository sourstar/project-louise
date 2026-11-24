"""Each test ends with the signal handlers it began with (tests/conftest.py
`signal_handlers_are_restored`): a command run in-process that takes over
SIGTERM and never gives it back no longer decides how a later test stops.
The two tests run in file order."""
import signal

_AT_START = {"handler": None}


def test_a_command_run_in_process_takes_over_sigterm():
    _AT_START["handler"] = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_: None)          # never given back, as a CLI does
    assert signal.getsignal(signal.SIGTERM) is not _AT_START["handler"]


def test_the_next_test_begins_with_the_handler_from_before():
    assert signal.getsignal(signal.SIGTERM) is _AT_START["handler"]
