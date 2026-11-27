import threading
import time
import unittest

from central_worker import MARKETS, _shared_market_feed, _slug


class CentralWorkerTests(unittest.TestCase):
    def test_three_market_watchlist(self):
        self.assertEqual(MARKETS, ("XAU/USD", "XAG/USD", "US500"))

    def test_slug_is_stable(self):
        self.assertEqual(_slug("XAU/USD"), "xau_usd")
        self.assertEqual(_slug("US500"), "us500")

    def test_shared_feed_downloads_once_for_concurrent_identical_requests(self):
        calls = []
        def fetch(symbol, outputsize=1600, interval="5min"):
            calls.append((symbol, outputsize, interval))
            time.sleep(0.03)
            return {"rows": [1, 2, 3]}

        shared, cache, counts = _shared_market_feed(fetch)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(shared("XAU/USD", interval="5min")))
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 8)
        self.assertEqual(counts["misses"], 1)
        self.assertEqual(counts["hits"], 7)
        self.assertEqual(len(cache), 1)


if __name__ == "__main__":
    unittest.main()
