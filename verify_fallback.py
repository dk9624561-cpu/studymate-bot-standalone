import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bot.api import fetch_batches, fetch_topics, fetch_lectures, fetch_video_details
from bot.cache_manager import get_cache

def test_api_failover():
    print("--- 1. Testing Live API Fetch & Cache Population ---")
    batches = fetch_batches()
    print(f"Batches count: {len(batches)}")
    assert len(batches) > 0, "Batches list should not be empty"

    cached = get_cache("batches_list")
    assert cached is not None and len(cached) > 0, "Batches should be cached in SQLite"
    print("[OK] Live batches fetch and SQLite cache verified!")

    print("\n--- 2. Testing Offline Failover (Simulating Live API Failure) ---")
    import bot.api
    original_url = bot.api.BATCHES_URL
    bot.api.BATCHES_URL = "https://invalid-domain-name-that-does-not-exist.xyz/batches.json"
    bot.api._BATCH_CACHE = []  # Clear memory cache

    offline_batches = fetch_batches()
    print(f"Offline batches count returned: {len(offline_batches)}")
    assert len(offline_batches) > 0, "Should fall back to SQLite or JSON fallback"
    print("[OK] Offline failover successfully returned cached batches!")

    bot.api.BATCHES_URL = original_url
    print("\nAll failover tests PASSED successfully!")

if __name__ == "__main__":
    test_api_failover()
