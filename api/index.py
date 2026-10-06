import os
import json
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# Fallback dataset path
FALLBACK_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "batches_fallback.json")

BATCHES_URL = "https://sunnyji7256.github.io/kgs_batch_list/New_Sunny.json"
CLASSROOM_URL = "https://study-mate.in/api/classroom/{}"
LESSON_URL = "https://study-mate.in/api/lesson/{}"
VIDEO_URL = "https://study-mate.in/api/video/{}"

_BATCHES_CACHE = []

def get_fallback_batches():
    """Load local pre-bundled fallback dataset if live API fails."""
    if os.path.exists(FALLBACK_PATH):
        try:
            with open(FALLBACK_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("Error reading fallback JSON dataset:", e)
    return []

@app.after_request
def add_cors_headers(response):
    """Enable Cross-Origin Resource Sharing (CORS) for all clients."""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return response

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "service": "StudyMate Resilient Vercel Serverless API Engine",
        "version": "1.0.0"
    })

@app.route('/api/batches', methods=['GET'])
def get_batches():
    global _BATCHES_CACHE
    if _BATCHES_CACHE:
        return jsonify(_BATCHES_CACHE)
        
    try:
        res = requests.get(BATCHES_URL, timeout=8)
        if res.status_code == 200:
            _BATCHES_CACHE = res.json()
            return jsonify(_BATCHES_CACHE)
    except Exception as e:
        print("Live batches fetch failed, using fallback:", e)

    _BATCHES_CACHE = get_fallback_batches()
    return jsonify(_BATCHES_CACHE)

@app.route('/api/classroom/<int:course_id>', methods=['GET'])
def get_classroom(course_id):
    try:
        res = requests.get(CLASSROOM_URL.format(course_id), headers={"X-Requested-With": "XMLHttpRequest"}, timeout=8)
        if res.status_code == 200:
            return jsonify(res.json())
    except Exception as e:
        print(f"Classroom API error for batch {course_id}:", e)
    return jsonify({"classroom": []})

@app.route('/api/lesson/<int:topic_id>', methods=['GET'])
def get_lesson(topic_id):
    try:
        res = requests.get(LESSON_URL.format(topic_id), headers={"X-Requested-With": "XMLHttpRequest"}, timeout=8)
        if res.status_code == 200:
            return jsonify(res.json())
    except Exception as e:
        print(f"Lesson API error for topic {topic_id}:", e)
    return jsonify({"videos": [], "notes": []})

@app.route('/api/video/<int:video_id>', methods=['GET'])
def get_video(video_id):
    try:
        res = requests.get(VIDEO_URL.format(video_id), headers={"X-Requested-With": "XMLHttpRequest"}, timeout=8)
        if res.status_code == 200:
            return jsonify(res.json())
    except Exception as e:
        print(f"Video API error for video {video_id}:", e)
    return jsonify({})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 3000))
    app.run(host='0.0.0.0', port=port, debug=True)
