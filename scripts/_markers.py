"""Tokens that must never appear in this public repository, held as SHA-256 digests.

Keeping the digests rather than the strings means the guard itself does not publish what
it guards. `contains_marker(text)` checks every token and every contiguous run of its
`/`- or `.`-delimited parts, case-insensitively, so a marker embedded in a longer path still matches.
"""
import hashlib
import re

_DIGESTS = frozenset({
    "3317534db2bd03ec", "8feeed3481fc45a7", "3bcad6a89cbfde01",
    "86f181c52578c7fe", "597a0f89d9b934c8",
})
_TOKEN = re.compile(r"[@\w][\w@./+-]*\w")


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _candidates(token):
    """The token and every contiguous run of its `/`- or `.`-delimited parts."""
    cuts = [0] + [i + 1 for i, char in enumerate(token) if char in "/."] + [len(token) + 1]
    for a in range(len(cuts) - 1):
        for b in range(a + 1, len(cuts)):
            yield token[cuts[a]:cuts[b] - 1]


def find_marker(text):
    """The first offending token in `text`, or None."""
    for token in _TOKEN.findall(text.lower()):
        for candidate in _candidates(token):
            if _digest(candidate) in _DIGESTS:
                return candidate
    return None
