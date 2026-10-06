import os
import sys
import types
import logging

def setup_bot_path():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)

    for item in os.listdir(BASE_DIR):
        sub = os.path.join(BASE_DIR, item)
        if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "bot")):
            if sub not in sys.path:
                sys.path.insert(0, sub)
                break

    try:
        import bot
    except ModuleNotFoundError:
        target_dir = None
        if os.path.exists(os.path.join(BASE_DIR, "handlers.py")):
            target_dir = BASE_DIR
        else:
            for root, dirs, files in os.walk(BASE_DIR):
                if "handlers.py" in files and "api.py" in files:
                    target_dir = root
                    break
        if target_dir:
            bot_mod = types.ModuleType("bot")
            bot_mod.__path__ = [target_dir]
            bot_mod.__package__ = "bot"
            sys.modules["bot"] = bot_mod

setup_bot_path()

from flask import Flask, jsonify, make_response
from bot.api import fetch_batches, fetch_topics, fetch_lectures, fetch_video_details

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("api_server")

app = Flask(__name__)

def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return response

@app.after_request
def after_request(response):
    return add_cors_headers(response)

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "online",
        "service": "StudyMate Telegram Bot & API Proxy",
        "bot": "@TESRYINN_BOT"
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "service": "StudyMate Resilient API Proxy"})

@app.route('/api/batches', methods=['GET'])
def get_batches():
    batches = fetch_batches()
    return jsonify(batches)

@app.route('/api/classroom/<int:course_id>', methods=['GET'])
def get_classroom(course_id):
    topics = fetch_topics(course_id)
    return jsonify({"classroom": topics})

@app.route('/api/lesson/<int:topic_id>', methods=['GET'])
def get_lesson(topic_id):
    videos, notes = fetch_lectures(topic_id)
    return jsonify({"videos": videos, "notes": notes})

@app.route('/api/video/<int:video_id>', methods=['GET'])
def get_video(video_id):
    details = fetch_video_details(video_id)
    return jsonify(details)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 7860))
    logger.info(f"Starting StudyMate Resilient Proxy Server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
