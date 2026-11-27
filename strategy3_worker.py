"""Dedicated Voroa entry point for Strategy 3 only.

Runs Strategy 3 concurrently across the approved three markets, with isolated
state separate from the Strategies 1+2 central worker.
"""
import json
from pathlib import Path

import central_worker as scanner

# Reuse the proven one-process / three-market scheduler, but give this worker
# its own strategy list and persistent state namespace.


def import_legacy_strategy3_state():
    """Preserve existing Strategy 3 plans when moving out of the old shared worker."""
    legacy_root = scanner.ROOT / ".voroa_agents"
    legacy_flat = scanner.ROOT / ".strategy3_state.json"
    for symbol in scanner.MARKETS:
        destination = scanner._market_state_dir(symbol) / ".strategy3_state.json"
        if destination.exists():
            continue

        slug = scanner._slug(symbol)
        candidates = [
            legacy_root / slug / ".strategy3_state.json",
            legacy_flat,
        ]
        for source in candidates:
            try:
                payload = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            state = payload.get(symbol) if isinstance(payload, dict) else None
            if not isinstance(state, dict):
                continue
            scanner._atomic_json(destination, {symbol: state})
            scanner.log(f"STRATEGY3_STATE_IMPORTED market={symbol} source={source.name}")
            break


def configure_worker():
    scanner.STRATEGY_MODULES = ("strategy3",)
    scanner.STATE_FILES = (".strategy3_state.json",)
    scanner.STATE_ROOT = scanner.ROOT / ".voroa_strategy3"
    scanner.WORKER_NAME = "STRATEGY3_SCANNER"


def main():
    configure_worker()
    scanner.os.chdir(scanner.ROOT)
    scanner.STATE_ROOT.mkdir(parents=True, exist_ok=True)
    import_legacy_strategy3_state()
    scanner.main()


if __name__ == "__main__":
    main()
