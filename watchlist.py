"""Authoritative market whitelist shared by every scanner entry point."""

ALLOWED_SYMBOLS = ("XAU/USD", "XAG/USD", "US500")
_ALLOWED = frozenset(ALLOWED_SYMBOLS)


def select_symbols(requested=None):
    """Return only the approved markets; never trust an environment override."""
    if requested is None:
        return list(ALLOWED_SYMBOLS)
    if isinstance(requested, str):
        requested = requested.split(",")

    selected = []
    rejected = []
    for value in requested:
        symbol = str(value).strip().upper()
        if symbol in _ALLOWED:
            if symbol not in selected:
                selected.append(symbol)
        elif symbol:
            rejected.append(symbol)

    if rejected:
        print(
            "IGNORED_UNSUPPORTED_MARKETS " + ",".join(sorted(set(rejected))),
            flush=True,
        )
    return selected
