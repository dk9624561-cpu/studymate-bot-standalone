import socket
# Force IPv4 across all socket connections (fixes Hugging Face / cloud IPv6 black-hole issue)
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_getaddrinfo

import os
import sys
import types
import threading
import time
import logging

def setup_bot_path():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)

    # 1. Check if bot is in a subfolder (e.g. studymate-bot-standalone or studymate_hf_deploy)
    for item in os.listdir(BASE_DIR):
        sub = os.path.join(BASE_DIR, item)
        if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "bot")):
            if sub not in sys.path:
                sys.path.insert(0, sub)
                break

    # 2. Check if bot package can be imported
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("run_all")

def run_api_server():
    try:
        logger.info("Starting Resilient API Proxy Server...")
        import api_server
        port = int(os.environ.get("PORT", 7860))
        api_server.app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"API Server encountered an error: {e}")

def run_telegram_bot():
    while True:
        try:
            logger.info("Starting Telegram Bot...")
            import main
            main.main()
        except Exception as e:
            logger.error(f"Telegram Bot encountered an error: {e}. Retrying connection in 5 seconds...", exc_info=True)
            time.sleep(5)

if __name__ == "__main__":
    logger.info("Initializing 24/7 StudyMate Service Suite (API + Bot)...")
    
    # Thread 1: Local API Server
    api_thread = threading.Thread(target=run_api_server, daemon=True)
    api_thread.start()
    
    # Give API server 2 seconds to initialize
    time.sleep(2)
    
    # Main Thread: Telegram Bot
    run_telegram_bot()
