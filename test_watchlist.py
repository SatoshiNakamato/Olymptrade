import unittest
from watchlist import ALLOWED_SYMBOLS, select_symbols


class WatchlistTests(unittest.TestCase):
    def test_default_is_exactly_three_approved_markets(self):
        self.assertEqual(select_symbols(None), ["XAU/USD", "XAG/USD", "US500"])
        self.assertEqual(ALLOWED_SYMBOLS, ("XAU/USD", "XAG/USD", "US500"))

    def test_environment_override_cannot_add_unapproved_markets(self):
        self.assertEqual(
            select_symbols("EUR/USD,BTC/USD,XAU/USD,XAG/USD,US500"),
            ["XAU/USD", "XAG/USD", "US500"],
        )

    def test_explicit_selection_is_filtered_and_deduplicated(self):
        self.assertEqual(
            select_symbols(["xau/usd", "BTC/USD", "US500", "XAU/USD"]),
            ["XAU/USD", "US500"],
        )

    def test_empty_or_unsupported_selection_returns_empty(self):
        self.assertEqual(select_symbols("EUR/USD,BTC/USD"), [])
        self.assertEqual(select_symbols(""), [])


if __name__ == "__main__":
    unittest.main()
