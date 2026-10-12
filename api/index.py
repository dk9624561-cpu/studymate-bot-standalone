import os
import json
import time
import requests
from requests.adapters import HTTPAdapter
from flask import Flask, jsonify, request

app = Flask(__name__)

# Common browser request headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "X-Requested-With": "XMLHttpRequest"
}

# High-performance HTTP session
_SESSION = requests.Session()
_ADAPTER = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)

# Credentials & Upstream endpoints
KGS_PHONE = os.getenv("KGS_PHONE", "7544898925")
KGS_PASSWORD = os.getenv("KGS_PASSWORD", "75448989")
_CURRENT_KGS_TOKEN = os.getenv("KGS_TOKEN", "147419804|EOm8dRWhsJkHRS4Oatok5NBYIRwug62TpTx2DOIS14fefc33")

# Official KGS Courses API Endpoints
KGS_COURSES_URL = "https://khanglobalstudies.com/api/courses"
KGS_V1_COURSES_URL = "https://api.khanglobalstudies.com/v1/courses"
STUDYMATE_CLASSROOM_URL = "https://study-mate.in/api/classroom/{}"
STUDYMATE_LESSON_URL = "https://study-mate.in/api/lesson/{}"
STUDYMATE_VIDEO_URL = "https://study-mate.in/api/video/{}"

# In-memory fast cache
_MEM_CACHE = {}
_BATCHES_CACHE = []

# Fallback path for pre-bundled batches
FALLBACK_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "batches_fallback.json")

def get_valid_kgs_token():
    """Return active KGS token or automatically authenticate live session."""
    global _CURRENT_KGS_TOKEN
    if _CURRENT_KGS_TOKEN:
        return _CURRENT_KGS_TOKEN

    if KGS_PHONE and KGS_PASSWORD:
        for login_url in ["https://khanglobalstudies.com/api/login", "https://api.khanglobalstudies.com/cms/login"]:
            try:
                res = _SESSION.post(login_url, json={"phone": KGS_PHONE, "password": KGS_PASSWORD}, headers=HEADERS, timeout=8)
                if res.status_code == 200:
                    data = res.json()
                    t = data.get("token")
                    if t:
                        _CURRENT_KGS_TOKEN = t
                        return _CURRENT_KGS_TOKEN
            except Exception as e:
                print(f"KGS auto-login error at {login_url}: {e}")
    return _CURRENT_KGS_TOKEN or ""

def get_fallback_batches():
    """Load pre-bundled fallback dataset if GitHub raw is unavailable."""
    if os.path.exists(FALLBACK_PATH):
        try:
            with open(FALLBACK_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("Error loading fallback batches:", e)
    return []

def format_video_response(raw_video_data):
    """Normalize video data into the exact StudyMate schema."""
    if not isinstance(raw_video_data, dict):
        return {"hd_video_url": "", "video_url": "", "pdfs": []}

    # If it is inside "video" key (KGS API format)
    v = raw_video_data.get("video", raw_video_data)

    hd_url = v.get("hd_video_url") or v.get("video_url") or ""
    vid_url = v.get("video_url") or v.get("hd_video_url") or ""

    # Check mp4s or qualities array if direct url is empty
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

@app.after_request
def add_cors_headers(response):
    """Enable Cross-Origin Resource Sharing (CORS) for web apps and bots."""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Requested-With, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "service": "StudyMate Resilient Vercel Serverless API",
        "version": "2.0.0",
        "endpoints": {
            "video": "/api/video/<video_id>",
            "lesson": "/api/lesson/<topic_id>",
            "classroom": "/api/classroom/<course_id>",
            "batches": "/api/batches"
        }
    })

def fetch_official_kgs_batches():
    """Fetch batches directly from official Khan Global Studies API with local archive merge."""
    all_batches = []
    seen_ids = set()

    for page in range(1, 10):
        try:
            url = f"{KGS_COURSES_URL}?page={page}"
            res = _SESSION.get(url, headers=HEADERS, timeout=6)
            if res.status_code != 200:
                break
            data = res.json()
            items = data.get("data", [])
            if not items:
                break
            for c in items:
                cid = c.get("id")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    img = c.get("image") or {}
                    thumb = img.get("thumb") or img.get("small") or img.get("medium") or img.get("large") if isinstance(img, dict) else ""
                    price_val = c.get("price")
                    price_str = f"₹{price_val}" if price_val and str(price_val) != "0" else ("Free" if c.get("is_free") else "Paid")
                    all_batches.append({
                        "id": cid,
                        "title": (c.get("title") or "").strip(),
                        "start_at": c.get("start_at") or c.get("registration_at") or "N/A",
                        "end_at": c.get("end_at") or "Lifetime",
                        "price": price_str,
                        "image_thumb": thumb or "",
                        "image_large": thumb or ""
                    })
            if page >= data.get("last_page", 1):
                break
        except Exception as e:
            print(f"Error fetching official KGS courses page {page}: {e}")
            break

    fallback = get_fallback_batches()
    for b in fallback:
        bid = b.get("id")
        if bid and bid not in seen_ids:
            seen_ids.add(bid)
            all_batches.append(b)

    return all_batches

@app.route('/api/batches', methods=['GET'])
def get_batches():
    global _BATCHES_CACHE
    if _BATCHES_CACHE:
        return jsonify(_BATCHES_CACHE)

    batches = fetch_official_kgs_batches()
    if batches:
        _BATCHES_CACHE = batches
        return jsonify(_BATCHES_CACHE)

    _BATCHES_CACHE = get_fallback_batches()
    return jsonify(_BATCHES_CACHE)


@app.route('/api/classroom/<int:course_id>', methods=['GET'])
def get_classroom(course_id):
    cache_key = f"classroom_{course_id}"
    if cache_key in _MEM_CACHE:
        return jsonify(_MEM_CACHE[cache_key])

    # Tier 1: StudyMate classroom API
    try:
        res = _SESSION.get(STUDYMATE_CLASSROOM_URL.format(course_id), headers=HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data and data.get("classroom"):
                _MEM_CACHE[cache_key] = data
                return jsonify(data)
    except Exception as e:
        print(f"StudyMate classroom error for course {course_id}: {e}")

    # Tier 2: KGS Live Course topics
    try:
        token = get_valid_kgs_token()
        h = dict(HEADERS)
        if token:
            h["Authorization"] = f"Bearer {token}"
        res = _SESSION.get(f"https://khanglobalstudies.com/api/courses/{course_id}", headers=h, timeout=10)
        if res.status_code == 200:
            cdata = res.json()
            topics = []
            for item in cdata.get("subjects", []):
                for topic in item.get("topics", []):
                    topics.append({
                        "id": topic.get("id"),
                        "title": topic.get("name") or topic.get("title")
                    })
            if topics:
                res_data = {"classroom": topics}
                _MEM_CACHE[cache_key] = res_data
                return jsonify(res_data)
    except Exception as e:
        print(f"KGS courses error for course {course_id}: {e}")

    return jsonify({"classroom": []})

@app.route('/api/lesson/<int:topic_id>', methods=['GET'])
def get_lesson(topic_id):
    cache_key = f"lesson_{topic_id}"
    if cache_key in _MEM_CACHE:
        return jsonify(_MEM_CACHE[cache_key])

    token = get_valid_kgs_token()
    
    # Tier 1: Direct KGS Authenticated Lesson API
    if token:
        for ep in [
            f"https://khanglobalstudies.com/api/lessons/{topic_id}",
            f"https://api.khanglobalstudies.com/cms/lessons/{topic_id}"
        ]:
            try:
                auth_headers = dict(HEADERS)
                auth_headers["Authorization"] = f"Bearer {token}"
                auth_headers["Origin"] = "https://khanglobalstudies.com"
                auth_headers["Referer"] = "https://khanglobalstudies.com/"
                res = _SESSION.get(ep, headers=auth_headers, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, dict) and not data.get("error") and data.get("status") is not False:
                        _MEM_CACHE[cache_key] = data
                        return jsonify(data)
            except Exception as e:
                print(f"KGS lesson fetch error at {ep}: {e}")

    # Tier 2: StudyMate Lesson API
    try:
        res = _SESSION.get(STUDYMATE_LESSON_URL.format(topic_id), headers=HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict) and not data.get("error"):
                _MEM_CACHE[cache_key] = data
                return jsonify(data)
    except Exception as e:
        print(f"StudyMate lesson API error for topic {topic_id}: {e}")

    return jsonify({"videos": [], "notes": []})

@app.route('/api/video/<int:video_id>', methods=['GET'])
def get_video(video_id):
    cache_key = f"video_{video_id}"
    if cache_key in _MEM_CACHE:
        return jsonify(_MEM_CACHE[cache_key])

    token = get_valid_kgs_token()

    # Tier 1: Direct KGS Authenticated Video API
    if token:
        for ep in [
            f"https://khanglobalstudies.com/api/videos/{video_id}",
            f"https://api.khanglobalstudies.com/cms/videos/{video_id}"
        ]:
            try:
                auth_headers = dict(HEADERS)
                auth_headers["Authorization"] = f"Bearer {token}"
                auth_headers["Origin"] = "https://khanglobalstudies.com"
                auth_headers["Referer"] = "https://khanglobalstudies.com/"
                res = _SESSION.get(ep, headers=auth_headers, timeout=8)
                if res.status_code == 200:
                    raw_data = res.json()
                    if isinstance(raw_data, dict) and (raw_data.get("video") or raw_data.get("video_url") or raw_data.get("hd_video_url")):
                        formatted = format_video_response(raw_data)
                        _MEM_CACHE[cache_key] = formatted
                        return jsonify(formatted)
            except Exception as e:
                print(f"KGS direct video fetch error at {ep}: {e}")

    # Tier 2: StudyMate Video API Proxy
    try:
        res = _SESSION.get(STUDYMATE_VIDEO_URL.format(video_id), headers=HEADERS, timeout=8)
        if res.status_code == 200:
            raw_data = res.json()
            if isinstance(raw_data, dict) and (raw_data.get("video_url") or raw_data.get("hd_video_url") or raw_data.get("pdfs")):
                formatted = format_video_response(raw_data)
                _MEM_CACHE[cache_key] = formatted
                return jsonify(formatted)
    except Exception as e:
        print(f"StudyMate video API error for {video_id}: {e}")

    # Tier 3: Search in cached lessons for this video ID
    for key, val in list(_MEM_CACHE.items()):
        if key.startswith("lesson_") and isinstance(val, dict):
            for v in val.get("videos", []):
                if v.get("id") == video_id or str(v.get("id")) == str(video_id):
                    formatted = format_video_response(v)
                    _MEM_CACHE[cache_key] = formatted
                    return jsonify(formatted)

    return jsonify({"hd_video_url": "", "video_url": "", "pdfs": []})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 3000))
    app.run(host='0.0.0.0', port=port, debug=False)
