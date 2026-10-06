import requests
from requests.adapters import HTTPAdapter
import logging
import json
import os
from bot.config import BATCHES_URL, CLASSROOM_URL, LESSON_URL, VIDEO_URL
from bot.cache_manager import get_cache, set_cache

logger = logging.getLogger(__name__)

# Common browser request headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "X-Requested-With": "XMLHttpRequest"
}

# High-concurrency persistent session with connection pooling
_SESSION = requests.Session()
_ADAPTER = HTTPAdapter(pool_connections=60, pool_maxsize=60, max_retries=1)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)

# Global cache in memory for batch data
_BATCH_CACHE = []

FALLBACK_BATCHES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "batches_fallback.json")

def load_fallback_batches():
    """Load local fallback JSON file for batches."""
    if os.path.exists(FALLBACK_BATCHES_PATH):
        try:
            with open(FALLBACK_BATCHES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading fallback batches file: {e}")
    return []

def fetch_batches():
    """Fetch 1000+ course batches with multi-tier failover (Live API -> SQLite Cache -> JSON Fallback)."""
    global _BATCH_CACHE
    if _BATCH_CACHE:
        return _BATCH_CACHE

    cache_key = "batches_list"
    try:
        res = _SESSION.get(BATCHES_URL, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            data = res.json()
            if data:
                _BATCH_CACHE = data
                set_cache(cache_key, data)
                logger.info(f"Fetched {len(data)} batches from live API and cached successfully.")
                return _BATCH_CACHE
    except Exception as e:
        logger.warning(f"Live API for batches failed ({e}). Attempting failover...")

    # Tier 2: Check SQLite cache
    cached_data = get_cache(cache_key)
    if cached_data:
        logger.info(f"Loaded {len(cached_data)} batches from local SQLite cache.")
        _BATCH_CACHE = cached_data
        return _BATCH_CACHE

    # Tier 3: Check fallback JSON file
    fallback_data = load_fallback_batches()
    if fallback_data:
        logger.info(f"Loaded {len(fallback_data)} batches from pre-bundled fallback dataset.")
        _BATCH_CACHE = fallback_data
        return _BATCH_CACHE

    return []

def fetch_topics(course_id):
    """Fetch classroom topics for a course with SQLite cache failover."""
    cache_key = f"classroom_{course_id}"
    
    # 1. Quick check cache first for instant response
    cached_data = get_cache(cache_key)
    if cached_data is not None and isinstance(cached_data, list) and len(cached_data) > 0:
        return cached_data

    url = CLASSROOM_URL.format(course_id)
    try:
        res = _SESSION.get(url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            data = res.json()
            classroom_list = data.get("classroom", [])
            if classroom_list:
                set_cache(cache_key, classroom_list)
                return classroom_list
    except Exception as e:
        logger.warning(f"Live API for classroom {course_id} failed ({e}).")

    if cached_data is not None:
        return cached_data

    return []

def fetch_lectures(topic_id):
    """Fetch video lectures and PDF notes for a topic with SQLite cache failover."""
    cache_key = f"lesson_{topic_id}"
    cached_data = get_cache(cache_key)
    if cached_data is not None and isinstance(cached_data, dict):
        return cached_data.get("videos", []), cached_data.get("notes", [])

    url = LESSON_URL.format(topic_id)
    try:
        res = _SESSION.get(url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            data = res.json()
            set_cache(cache_key, data)
            return data.get("videos", []), data.get("notes", [])
    except Exception as e:
        logger.warning(f"Live API for lesson {topic_id} failed ({e}).")

    return [], []

def fetch_video_details(video_id):
    """Fetch direct stream and PDF links for a lecture with retries & SQLite cache failover."""
    cache_key = f"video_{video_id}"
    cached_data = get_cache(cache_key)
    if cached_data is not None and isinstance(cached_data, dict) and cached_data:
        return cached_data

    url = VIDEO_URL.format(video_id)
    import time
    for attempt in range(3):
        try:
            res = _SESSION.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data:
                    set_cache(cache_key, data)
                    return data
        except Exception as e:
            if attempt == 2:
                logger.warning(f"Live API for video {video_id} failed after attempts ({e}).")
            time.sleep(0.2)

    return {}
