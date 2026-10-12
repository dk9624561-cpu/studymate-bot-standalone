import requests
from requests.adapters import HTTPAdapter
import logging
import json
import os
from bot.config import BATCHES_URL, CLASSROOM_URL, LESSON_URL, VIDEO_URL, KGS_TOKEN, KGS_PHONE, KGS_PASSWORD
from bot.cache_manager import get_cache, set_cache

logger = logging.getLogger(__name__)

_CURRENT_KGS_TOKEN = KGS_TOKEN

def get_valid_kgs_token():
    """Retrieve or automatically refresh active KGS student session token."""
    global _CURRENT_KGS_TOKEN
    if _CURRENT_KGS_TOKEN:
        return _CURRENT_KGS_TOKEN

    phone = os.getenv("KGS_PHONE", KGS_PHONE)
    password = os.getenv("KGS_PASSWORD", KGS_PASSWORD)
    if phone and password:
        for login_url in ["https://khanglobalstudies.com/api/login", "https://api.khanglobalstudies.com/cms/login"]:
            try:
                res = _SESSION.post(login_url, json={"phone": phone, "password": password}, headers=HEADERS, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    t = data.get("token")
                    if t:
                        _CURRENT_KGS_TOKEN = t
                        logger.info("Successfully authenticated live KGS student session!")
                        return _CURRENT_KGS_TOKEN
            except Exception as e:
                logger.error(f"Failed to auto-login to KGS at {login_url}: {e}")
    return ""

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

def get_batch_details(course_id):
    """Retrieve full batch information by course ID using multi-level failover:
    1. In-memory / cached batches list (1089 courses).
    2. Live KGS Courses API (https://khanglobalstudies.com/api/courses/{id} and v1 endpoint).
    """
    course_id_str = str(course_id).strip()
    if not course_id_str:
        return None

    # Tier 1: Check in-memory batches list
    batches = fetch_batches()
    batch_obj = next((b for b in batches if str(b.get("id")) == course_id_str), None)
    if batch_obj and batch_obj.get("title") and batch_obj.get("title") != f"Batch {course_id_str}":
        return batch_obj

    # Tier 2: Check SQLite cache for this specific course
    cache_key = f"batch_info_{course_id_str}"
    cached_info = get_cache(cache_key)
    if cached_info and isinstance(cached_info, dict) and cached_info.get("title"):
        return cached_info

    # Tier 3: Fetch directly from live KGS Courses API endpoints
    for url in [
        f"https://khanglobalstudies.com/api/courses/{course_id_str}",
        f"https://api.khanglobalstudies.com/v1/courses/{course_id_str}"
    ]:
        try:
            res = _SESSION.get(url, headers=HEADERS, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, dict) and data.get("title"):
                    img = data.get("image") or {}
                    thumb = None
                    if isinstance(img, dict):
                        thumb = img.get("thumb") or img.get("large") or img.get("medium")
                    price_val = data.get("price")
                    price_str = f"₹{price_val}" if price_val and str(price_val) != "0" else ("Free" if data.get("is_free") else "Paid")

                    info = {
                        "id": int(course_id_str) if course_id_str.isdigit() else course_id_str,
                        "title": data.get("title").strip(),
                        "start_at": data.get("start_at") or "N/A",
                        "end_at": data.get("end_at") or "Lifetime",
                        "price": price_str,
                        "image_thumb": thumb or "https://i.postimg.cc/x1M0YN5Z/sunny.jpg",
                        "image_large": thumb or "https://i.postimg.cc/x1M0YN5Z/sunny.jpg",
                    }
                    set_cache(cache_key, info)
                    logger.info(f"Resolved Course {course_id_str} live from KGS API: '{info['title']}'")
                    return info
        except Exception as e:
            logger.debug(f"Live KGS course fetch failed for {url}: {e}")

    # Fallback to existing batch_obj if any
    return batch_obj

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

def fetch_course_demo_videos(course_id):
    """Fetch all available demo/foundation/orientation video lectures directly from KGS live course API."""
    course_id_str = str(course_id).strip()
    cache_key = f"demo_videos_{course_id_str}"
    cached_data = get_cache(cache_key)
    if cached_data is not None and isinstance(cached_data, list) and len(cached_data) > 0:
        return cached_data

    for url in [
        f"https://khanglobalstudies.com/api/courses/{course_id_str}",
        f"https://api.khanglobalstudies.com/v1/courses/{course_id_str}"
    ]:
        try:
            res = _SESSION.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, dict):
                    demos = data.get("demo_videos") or []
                    if demos:
                        set_cache(cache_key, demos)
                        logger.info(f"Retrieved {len(demos)} demo videos for course {course_id_str} from {url}")
                        return demos
        except Exception as e:
            logger.debug(f"Failed to fetch demo videos for {course_id_str} from {url}: {e}")
    return []

def fetch_lectures(topic_id, token=None):
    """Fetch video lectures and PDF notes for a topic with multi-tier failover:
    1. Local SQLite cache (if valid).
    2. Direct KGS CMS API with Bearer token (if KGS_TOKEN / token provided).
    3. StudyMate Lesson API.
    """
    cache_key = f"lesson_{topic_id}"
    cached_data = get_cache(cache_key)
    if cached_data is not None and isinstance(cached_data, dict):
        if not cached_data.get("error"):
            return cached_data.get("videos", []), cached_data.get("notes", [])

    auth_token = token or get_valid_kgs_token()
    if auth_token:
        for ep_url in [
            f"https://khanglobalstudies.com/api/lessons/{topic_id}",
            f"https://api.khanglobalstudies.com/cms/lessons/{topic_id}"
        ]:
            try:
                auth_headers = dict(HEADERS)
                auth_headers["Authorization"] = f"Bearer {auth_token}"
                auth_headers["Origin"] = "https://khanglobalstudies.com"
                auth_headers["Referer"] = "https://khanglobalstudies.com/"
                res = _SESSION.get(ep_url, headers=auth_headers, timeout=12)
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, dict) and not data.get("error") and data.get("status") is not False:
                        set_cache(cache_key, data)
                        return data.get("videos", []), data.get("notes", [])
            except Exception as e:
                logger.debug(f"KGS lesson fetch failed for {topic_id} at {ep_url}: {e}")

    url = LESSON_URL.format(topic_id)
    try:
        res = _SESSION.get(url, headers=HEADERS, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict) and not data.get("error"):
                set_cache(cache_key, data)
                return data.get("videos", []), data.get("notes", [])
    except Exception as e:
        logger.warning(f"Live API for lesson {topic_id} failed ({e}).")

    return [], []

def format_video_response(raw_video_data):
    """Normalize video data into the exact StudyMate schema."""
    if not isinstance(raw_video_data, dict):
        return {"hd_video_url": "", "video_url": "", "pdfs": []}

    v = raw_video_data.get("video", raw_video_data)
    hd_url = v.get("hd_video_url") or v.get("video_url") or ""
    vid_url = v.get("video_url") or v.get("hd_video_url") or ""

    if not vid_url and v.get("mp4s"):
        mp4_list = v.get("mp4s")
        if isinstance(mp4_list, list) and len(mp4_list) > 0:
            vid_url = mp4_list[0].get("url", "")
            hd_url = mp4_list[-1].get("url", "")

    raw_pdfs = v.get("pdfs") or []
    pdfs = []
    if isinstance(raw_pdfs, list):
        for p in raw_pdfs:
            if isinstance(p, dict):
                pdfs.append({
                    "title": p.get("title", "Lecture PDF"),
                    "url": p.get("url", "")
                })

    return {
        "hd_video_url": hd_url,
        "video_url": vid_url,
        "pdfs": pdfs
    }

def fetch_video_details(video_id):
    """Fetch direct stream and PDF links for a lecture with multi-tier failover:
    1. SQLite cache.
    2. Direct KGS Authenticated Video API.
    3. StudyMate Video API.
    """
    cache_key = f"video_{video_id}"
    cached_data = get_cache(cache_key)
    if cached_data is not None and isinstance(cached_data, dict) and cached_data:
        return cached_data

    # Tier 1: Direct KGS Authenticated Video API
    auth_token = get_valid_kgs_token()
    if auth_token:
        for ep in [
            f"https://khanglobalstudies.com/api/videos/{video_id}",
            f"https://api.khanglobalstudies.com/cms/videos/{video_id}"
        ]:
            try:
                auth_headers = dict(HEADERS)
                auth_headers["Authorization"] = f"Bearer {auth_token}"
                auth_headers["Origin"] = "https://khanglobalstudies.com"
                auth_headers["Referer"] = "https://khanglobalstudies.com/"
                res = _SESSION.get(ep, headers=auth_headers, timeout=8)
                if res.status_code == 200:
                    raw_data = res.json()
                    if isinstance(raw_data, dict) and (raw_data.get("video") or raw_data.get("video_url") or raw_data.get("hd_video_url")):
                        formatted = format_video_response(raw_data)
                        set_cache(cache_key, formatted)
                        return formatted
            except Exception as e:
                logger.debug(f"Direct KGS video fetch failed at {ep}: {e}")

    # Tier 2: StudyMate Video API
    url = VIDEO_URL.format(video_id)
    import time
    for attempt in range(3):
        try:
            res = _SESSION.get(url, headers=HEADERS, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if data:
                    formatted = format_video_response(data)
                    set_cache(cache_key, formatted)
                    return formatted
        except Exception as e:
            if attempt == 2:
                logger.warning(f"Live API for video {video_id} failed after attempts ({e}).")
            time.sleep(0.2)

    return {"hd_video_url": "", "video_url": "", "pdfs": []}

