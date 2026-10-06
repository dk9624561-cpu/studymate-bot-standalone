import socket
# Force IPv4 across all socket connections
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_getaddrinfo

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

from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from bot.config import BOT_TOKEN
from bot.handlers import start_command, button_handler, search_handler, send_batch_ids_file

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("BOT_TOKEN is not configured! Please set BOT_TOKEN in .env file or environment variables.")
        sys.exit(1)
        
    logger.info("Initializing KGS IAS Telegram Bot with resilient connection timeouts...")
    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(60.0)
        .read_timeout(60.0)
        .write_timeout(60.0)
        .pool_timeout(60.0)
        .get_updates_connect_timeout(60.0)
        .get_updates_read_timeout(60.0)
    )
    
    telegram_base_url = os.environ.get("TELEGRAM_BASE_URL")
    if telegram_base_url:
        logger.info(f"Using custom Telegram Base URL proxy: {telegram_base_url}")
        builder.base_url(telegram_base_url)
        
    telegram_proxy = os.environ.get("TELEGRAM_PROXY")
    if telegram_proxy:
        logger.info(f"Using HTTP/SOCKS proxy: {telegram_proxy}")
        builder.proxy(telegram_proxy)
        builder.get_updates_proxy(telegram_proxy)
        
    app = builder.build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("courses", start_command))
    app.add_handler(CommandHandler("ids", send_batch_ids_file))
    app.add_handler(CommandHandler("batch_ids", send_batch_ids_file))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT, search_handler))
    
    app.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=-1,
        poll_interval=1.0,
        timeout=20,
        close_loop=False
    )

if __name__ == "__main__":
    main()
