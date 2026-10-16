"""Output streams that cannot raise on a character they cannot encode.

On Windows a redirected or piped stdout takes the legacy code page, and the first non-ASCII character in a line (the `·` of an advice row)
raised UnicodeEncodeError. A stream that is not a terminal is written as UTF-8; a terminal keeps its encoding and replaces what it cannot show.
"""
import sys


def safe_output():
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass    # a stream that cannot be reconfigured (a test harness's) is left as it is
