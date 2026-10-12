import os
import sys
import re
import html
import io
import time
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import zoneinfo

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("StudyMateTelebot")

# Add base directory to sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import telebot
from telebot import types

from bot.config import BOT_TOKEN, ITEMS_PER_PAGE, UPLOAD_CHANNEL
from bot.api import fetch_batches, fetch_topics, fetch_lectures, fetch_video_details, get_batch_details, fetch_course_demo_videos

if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
    logger.error("BOT_TOKEN is not configured! Please set BOT_TOKEN in .env or environment.")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Support custom Telegram Base URL (Cloudflare Worker Reverse Proxy) to bypass Hugging Face network blocking
telegram_base_url = os.environ.get("TELEGRAM_BASE_URL")
if telegram_base_url:
    clean_url = telegram_base_url.rstrip("/")
    if "{0}" not in clean_url:
        if clean_url.endswith("/bot"):
            clean_url = clean_url + "/{0}/{1}"
        else:
            clean_url = clean_url + "/bot{0}/{1}"
    telebot.apihelper.API_URL = clean_url
    logger.info(f"Custom Telegram API_URL active: {telebot.apihelper.API_URL}")

# Support HTTP/SOCKS proxy
telegram_proxy = os.environ.get("TELEGRAM_PROXY")
if telegram_proxy:
    telebot.apihelper.proxy = {'http': telegram_proxy, 'https': telegram_proxy}
    logger.info(f"Custom Proxy active: {telegram_proxy}")

# Channel storage configuration
CHANNEL_CONFIG_FILE = os.path.join(BASE_DIR, "data", "channel_config.json")

def load_channel_config():
    if os.path.exists(CHANNEL_CONFIG_FILE):
        try:
            with open(CHANNEL_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading channel config: {e}")
    if UPLOAD_CHANNEL:
        return {"channel_id": str(UPLOAD_CHANNEL).strip(), "channel_title": "Default Channel", "channel_username": ""}
    return {}

def save_channel_config(channel_id, channel_title="", channel_username=""):
    os.makedirs(os.path.dirname(CHANNEL_CONFIG_FILE), exist_ok=True)
    data = {
        "channel_id": str(channel_id).strip(),
        "channel_title": str(channel_title or ""),
        "channel_username": str(channel_username or ""),
        "updated_at": datetime.now().isoformat()
    }
    with open(CHANNEL_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data

def clear_channel_config():
    if os.path.exists(CHANNEL_CONFIG_FILE):
        try:
            os.remove(CHANNEL_CONFIG_FILE)
        except Exception:
            pass

def clean_item_title(raw_title):
    t = str(raw_title or "").strip()
    cleaned = re.sub(r'^\d{1,4}\s*[\.\-\)]\s*', '', t).strip()
    return cleaned if cleaned else t

def send_document_to_destinations(chat_id, file_content_or_bytes, filename, caption=None):
    """
    Sends document:
    - If target channel is configured: uploads ONLY to target channel (NOT user chat).
    - If no target channel is set: falls back to user chat.
    Returns: (sent_msg, sent_to_channel_bool, channel_cfg_dict)
    """
    if isinstance(file_content_or_bytes, str):
        content_bytes = file_content_or_bytes.encode("utf-8")
    elif isinstance(file_content_or_bytes, io.BytesIO):
        content_bytes = file_content_or_bytes.getvalue()
    elif isinstance(file_content_or_bytes, bytes):
        content_bytes = file_content_or_bytes
    else:
        content_bytes = b""

    cfg = load_channel_config()
    target_channel = cfg.get("channel_id")

    if target_channel:
        # Deliver ONLY to target channel
        sent_msg = None
        for attempt in range(2):
            try:
                ch_io = io.BytesIO(content_bytes)
                sent_msg = bot.send_document(target_channel, ch_io, caption=caption, visible_file_name=filename, timeout=60)
                logger.info(f"Document {filename} uploaded ONLY to target channel {target_channel}")
                break
            except Exception as ce:
                logger.error(f"Attempt {attempt+1} error uploading to channel {target_channel}: {ce}")
                if attempt == 0:
                    time.sleep(2)
                else:
                    raise ce
        return sent_msg, True, cfg
    else:
        # Fallback to user chat if no target channel is configured
        user_io = io.BytesIO(content_bytes)
        sent_msg = bot.send_document(chat_id, user_io, caption=caption, visible_file_name=filename, timeout=60)
        return sent_msg, False, {}

def parse_and_validate_channel(channel_input):
    clean_target = str(channel_input).strip()
    if clean_target.startswith("https://t.me/"):
        clean_target = "@" + clean_target.replace("https://t.me/", "").strip("/").split("/")[0]

    try:
        chat = bot.get_chat(clean_target)
    except Exception as e:
        return None, f"Channel nahi mila ya bot ke paas access nahi hai: {e}"

    if chat.type not in ["channel", "supergroup", "group"]:
        return None, "Yeh ek valid Channel ya Group nahi hai."

    try:
        me = bot.get_me()
        member = bot.get_chat_member(chat.id, me.id)
        if member.status not in ["administrator", "creator"]:
            return chat, "WARNING_NOT_ADMIN"
    except Exception as e:
        logger.warning(f"Could not verify admin status: {e}")

    return chat, None

# Global state for range processing & interactive prompts
ACTIVE_RANGE_TASKS = {}     # chat_id -> threading.Event()
AWAITING_RANGE_INPUT = {}   # chat_id -> bool
AWAITING_CHANNEL_INPUT = {} # chat_id -> bool

def safe_edit_text(bot, chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        return bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
    except Exception as e:
        err_msg = str(e)
        if "message is not modified" in err_msg.lower():
            return None
        plain = re.sub(r'<[^>]+>', '', text)
        try:
            return bot.edit_message_text(plain, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup, parse_mode=None, disable_web_page_preview=True)
        except Exception:
            return None

def extract_batch_id(text: str) -> str:
    text = text.strip()
    if text.isdigit():
        return text
    m = re.search(r'^(?:course|batch|id|c|#|\/batch|\/id|\/c)?\s*(?:id)?\s*[:=\-#]?\s*(\d{1,6})$', text, re.IGNORECASE)
    if m and m.group(1):
        return m.group(1)
    digits = re.findall(r'\b\d{1,6}\b', text)
    if len(digits) == 1:
        common_words = {'course', 'batch', 'id', 'open', 'bhejo', 'karo', 'do', 'ka', 'ki', 'ke', 'please', 'pls', 'send', 'show', 'courswe', 'dekho'}
        words = set(re.findall(r'[a-zA-Z]+', text.lower()))
        if words.issubset(common_words):
            return digits[0]
    return None

def extract_batch_range(text: str):
    text = text.strip()
    clean = re.sub(r'^(?:\/)?(?:batch|course|set)?\s*(?:id)?\s*range\s*[:=\-]?\s*', '', text, flags=re.IGNORECASE).strip()
    clean = re.sub(r'^(?:batch|course|id)\s*[:=\-]?\s*', '', clean, flags=re.IGNORECASE).strip()
    m = re.search(r'^(\d{1,6})\s*(?:-|–|—|\.\.|to|se|tak|:|,|\s+)\s*(\d{1,6})$', clean, re.IGNORECASE)
    if m:
        try:
            start_id = int(m.group(1))
            end_id = int(m.group(2))
            return min(start_id, end_id), max(start_id, end_id)
        except ValueError:
            pass
    return None

def get_welcome_content(user_first_name="Student"):
    cfg = load_channel_config()
    if cfg.get("channel_username"):
        ch_status = f"📢 Upload Channel: <b>@{cfg['channel_username']}</b>"
    elif cfg.get("channel_id"):
        ch_status = f"📢 Upload Channel: <b>{html.escape(cfg.get('channel_title', 'Configured'))}</b>"
    else:
        ch_status = "📢 Upload Channel: <i>Not Set</i> (Click below to set)"

    welcome_text = (
        f"👋 <b>Welcome to KGS IAS Bot, {html.escape(user_first_name)}!</b>\n\n"
        "📚 <b>Features:</b>\n"
        "• Browse 1000+ Khan Sir & KGS Courses\n"
        "• Send any Batch ID (e.g. <code>406</code> or <code>1237</code>) for single batch\n"
        "• Send <b>Batch Range</b> (e.g. <code>100-120</code>) to extract all TXT files one-by-one\n"
        "• Extract TXT Links with continuous index (1 to last)\n"
        f"• {ch_status}\n\n"
        "👇 Send any Batch ID, Range (e.g. <code>100-120</code>), or keyword to search!"
    )
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📚 Browse All Courses", callback_data="page_courses_0"),
        types.InlineKeyboardButton("📦 Batch Range Extraction (/range)", callback_data="btn_ask_range"),
        types.InlineKeyboardButton("📢 Upload Channel Setup (/channel)", callback_data="btn_channel_menu"),
        types.InlineKeyboardButton("📋 Download All Batch IDs File", callback_data="download_batch_ids")
    )
    return welcome_text, markup

@bot.message_handler(commands=['start'])
def handle_start(message):
    name = message.from_user.first_name if message.from_user else "Student"
    text, markup = get_welcome_content(name)
    bot.reply_to(message, text, reply_markup=markup, parse_mode="HTML")

@bot.message_handler(commands=['courses'])
def handle_courses_cmd(message):
    send_courses_page(message.chat.id, page=0)

@bot.message_handler(commands=['ids', 'batch_ids'])
def handle_ids_cmd(message):
    send_batch_ids_file(message.chat.id)

@bot.message_handler(commands=['range', 'batchrange', 'setrange', 'batch_range'])
def handle_range_cmd(message):
    text = message.text.strip()
    rng = extract_batch_range(text)
    if rng:
        start_range_extraction(message.chat.id, rng[0], rng[1])
        return

    parts = text.split()
    if len(parts) >= 2:
        arg_str = " ".join(parts[1:])
        rng = extract_batch_range(arg_str)
        if rng:
            start_range_extraction(message.chat.id, rng[0], rng[1])
            return

    AWAITING_RANGE_INPUT[message.chat.id] = True
    msg_text = (
        "🔢 <b>Batch ID Range Extraction</b>\n\n"
        "Kripya Batch ID ki range send karein.\n\n"
        "<b>Examples:</b>\n"
        "• <code>100-120</code>\n"
        "• <code>100 to 120</code>\n"
        "• <code>400 - 450</code>\n"
        "• <code>1200 1237</code>\n\n"
        "👉 Bot is range ke sabhi batch ka TXT file bna ke ek-ek karke send karega!\n"
        "<i>(Cancel karne ke liye /cancel bhejein)</i>"
    )
    bot.reply_to(message, msg_text)

def send_channel_status(chat_id, message_id=None):
    cfg = load_channel_config()
    target_id = cfg.get("channel_id")
    markup = types.InlineKeyboardMarkup(row_width=2)
    if target_id:
        title = cfg.get("channel_title") or "Configured Channel"
        uname = f"@{cfg.get('channel_username')}" if cfg.get("channel_username") else "N/A"
        text = (
            "📢 <b>Current Upload Channel:</b>\n\n"
            f"📌 <b>Channel:</b> {html.escape(title)}\n"
            f"🆔 <b>ID:</b> <code>{target_id}</code>\n"
            f"🔗 <b>Username:</b> {uname}\n\n"
            "✅ Sabhi extracted TXT batch files is channel me automatically upload ho rahi hain."
        )
        markup.add(
            types.InlineKeyboardButton("🧪 Test Post", callback_data="btn_test_channel"),
            types.InlineKeyboardButton("❌ Remove Channel", callback_data="btn_unset_channel")
        )
        markup.add(
            types.InlineKeyboardButton("🔄 Change Channel", callback_data="btn_set_channel"),
            types.InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")
        )
    else:
        text = (
            "📢 <b>Upload Channel Setup:</b>\n\n"
            "ℹ️ Abhi koi upload channel set <b>nahi</b> hai.\n\n"
            "Channel set karne ke liye <b>'Set Upload Channel'</b> button dabayein ya <code>/setchannel</code> command use karein.\n\n"
            "Channel set hone ke baad single & range batch extraction ki sabhi TXT files automatically aapke channel me deliver hongi!"
        )
        markup.add(types.InlineKeyboardButton("➕ Set Upload Channel", callback_data="btn_set_channel"))
        markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home"))

    if message_id:
        safe_edit_text(bot, chat_id, message_id, text, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)

@bot.message_handler(commands=['setchannel', 'set_channel'])
def handle_setchannel_cmd(message):
    text = message.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) > 1:
        target = parts[1].strip()
        chat_obj, err = parse_and_validate_channel(target)
        if not chat_obj:
            bot.reply_to(message, f"❌ <b>Error:</b> {err}\n\nKripya sahi channel ID ya @username bhejein.")
            return

        save_channel_config(chat_obj.id, chat_obj.title or "Channel", chat_obj.username or "")
        warn_msg = ""
        if err == "WARNING_NOT_ADMIN":
            warn_msg = "\n\n⚠️ <i>Note: Bot channel me Admin nahi dikh raha hai. Kripya bot ko channel me Administrator banakar 'Post Messages' permission zaroor dein!</i>"

        uname = f"@{chat_obj.username}" if chat_obj.username else f"ID: {chat_obj.id}"
        bot.reply_to(
            message,
            f"✅ <b>Upload Channel Set Ho Gaya!</b>\n\n"
            f"📢 <b>Channel:</b> {html.escape(chat_obj.title or 'Channel')}\n"
            f"🆔 <b>ID:</b> <code>{chat_obj.id}</code>\n"
            f"🔗 <b>Username / Target:</b> {uname}{warn_msg}\n\n"
            f"👉 Ab sabhi single aur range batch extraction TXT files is channel me automatically upload hongi!"
        )
        return

    AWAITING_CHANNEL_INPUT[message.chat.id] = True
    bot.reply_to(
        message,
        "📢 <b>Set Upload Channel</b>\n\n"
        "Kripya channel ka <b>@username</b> ya <b>ID</b> (jaise <code>-1001234567890</code>) bhejein,\n"
        "ya apne channel se koi bhi message yahan <b>Forward</b> karein!\n\n"
        "⚠️ <i>Bot ko pehle channel me Admin banakar 'Post Messages' permission zaroor dein!</i>\n\n"
        "<i>(Cancel karne ke liye /cancel bhejein)</i>"
    )

@bot.message_handler(commands=['channel', 'getchannel'])
def handle_channel_cmd(message):
    send_channel_status(message.chat.id)

@bot.message_handler(commands=['unsetchannel', 'clearchannel'])
def handle_unsetchannel_cmd(message):
    clear_channel_config()
    bot.reply_to(message, "✅ Upload Channel hata diya gaya hai. Ab files sirf private chat me send hongi.")

@bot.message_handler(commands=['stop', 'cancel'])
def handle_stop_cmd(message):
    chat_id = message.chat.id
    if chat_id in AWAITING_RANGE_INPUT:
        AWAITING_RANGE_INPUT.pop(chat_id, None)
        bot.reply_to(message, "❌ Range input cancelled.")
        return

    if chat_id in AWAITING_CHANNEL_INPUT:
        AWAITING_CHANNEL_INPUT.pop(chat_id, None)
        bot.reply_to(message, "❌ Channel setup cancelled.")
        return

    if chat_id in ACTIVE_RANGE_TASKS:
        ACTIVE_RANGE_TASKS[chat_id].set()
        bot.reply_to(message, "🛑 Stopping batch range extraction... Please wait.")
    else:
        bot.reply_to(message, "ℹ️ Koi active range extraction nahi chal rahi hai.")


def send_courses_page(chat_id, message_id=None, page=0, search_query=""):
    batches = fetch_batches()
    if search_query:
        batches = [b for b in batches if search_query.lower() in b.get("title", "").lower()]
    
    total = len(batches)
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, total)
    current_items = batches[start_idx:end_idx]

    if not current_items:
        text = f"❌ No courses found matching <b>'{html.escape(search_query)}'</b>."
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0"))
        if message_id:
            safe_edit_text(bot, chat_id, message_id, text, reply_markup=markup)
        else:
            bot.send_message(chat_id, text, reply_markup=markup)
        return

    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    text = f"📚 <b>KGS IAS Courses</b> (Page {page + 1} of {total_pages}):\nTotal: {total} courses\n\nSelect a course below:"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for b in current_items:
        title = b.get("title", "Untitled Course")
        b_id = b.get("id")
        if len(title) > 30:
            title = title[:27] + "..."
        markup.add(types.InlineKeyboardButton(f"🎓 [{b_id}] {title}", callback_data=f"c_{b_id}"))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Prev", callback_data=f"page_courses_{page-1}"))
    if end_idx < total:
        nav_buttons.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"page_courses_{page+1}"))
    if nav_buttons:
        markup.row(*nav_buttons)

    markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home"))

    if message_id:
        safe_edit_text(bot, chat_id, message_id, text, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)

def build_batch_metadata(course_id, topics, all_videos=None, all_notes=None):
    course_id = str(course_id).strip()
    batch_obj = get_batch_details(course_id)

    title = batch_obj.get("title") if batch_obj else None
    if not title or title.strip() == f"Batch {course_id}":
        title = f"Batch {course_id}"
    else:
        title = title.strip()

    start_at = batch_obj.get("start_at") or "N/A" if batch_obj else "N/A"
    end_at = batch_obj.get("end_at") or "Lifetime" if batch_obj else "Lifetime"
    price = batch_obj.get("price") or "Paid" if batch_obj else "Paid"
    thumb_url = batch_obj.get("image_thumb") or batch_obj.get("image_large") or "https://i.postimg.cc/x1M0YN5Z/sunny.jpg" if batch_obj else "https://i.postimg.cc/x1M0YN5Z/sunny.jpg"

    total_topics = len(topics) if topics else 0
    if all_videos is not None:
        total_videos = len(all_videos)
    else:
        total_videos = sum(int(t.get("videos", 0) or 0) for t in topics) if topics else 0

    if all_notes is not None:
        total_pdfs = len(all_notes)
    else:
        total_pdfs = sum(int(t.get("notes", 0) or 0) for t in topics) if topics else 0

    return {
        "title": title,
        "course_id": course_id,
        "start_at": start_at,
        "end_at": end_at,
        "price": price,
        "thumb_url": thumb_url,
        "total_topics": total_topics,
        "total_videos": total_videos,
        "total_pdfs": total_pdfs,
    }

def format_txt_header(meta, custom_title=None):
    divider = "=" * 90
    title_line = custom_title if custom_title else "✅ KGS IAS Batch Extraction!"
    total_v = meta.get('total_videos', 0)
    total_p = meta.get('total_pdfs', 0)
    v_range_str = f" (Index: 001 to {total_v:03d})" if total_v > 0 else ""
    p_range_str = f" (Index: 001 to {total_p:03d})" if total_p > 0 else ""

    cfg = load_channel_config()
    ch_info = []
    if cfg.get("channel_username"):
        ch_info = [f"📢 Uploaded Channel: @{cfg['channel_username']}"]
    elif cfg.get("channel_title"):
        ch_info = [f"📢 Uploaded Channel: {cfg['channel_title']} ({cfg.get('channel_id', '')})"]

    header_lines = [
        divider,
        f"{title_line}",
        "",
        f"📚 Batch Name: {meta['title']}",
        "",
        "📌 App Name: KGS IAS",
        f"🆔 Batch ID: {meta['course_id']}",
        f"💰 Price: {meta['price']}",
        "💳 Purchased: ❌ NO",
        f"📅 Start Date: {meta['start_at']}",
        f"⏳ Validity: {meta['start_at']} to {meta['end_at']}",
        f"🖼️ Thumbnail: {meta['thumb_url']}",
    ]
    if ch_info:
        header_lines.extend(ch_info)
    header_lines.extend([
        "",
        "📊 Content Summary:",
        f"• 📁 Total Topics: {meta['total_topics']} Subjects",
        f"• 📽️ Total Videos: {total_v} Videos{v_range_str}",
        f"• 📘 Total PDFs: {total_p} PDFs{p_range_str}",
        divider,
        ""
    ])
    return header_lines

def format_telegram_caption(meta, custom_title=None):
    safe_title = html.escape(meta['title'])
    title_line = custom_title if custom_title else "<b>✅ KGS IAS Batch Extraction!</b>"

    cfg = load_channel_config()
    channel_line = ""
    if cfg.get("channel_username"):
        channel_line = f"\n📢 <b>Upload Channel:</b> @{cfg['channel_username']}"
    elif cfg.get("channel_title"):
        channel_line = f"\n📢 <b>Upload Channel:</b> {html.escape(cfg['channel_title'])}"

    total_v = meta.get('total_videos', 0)
    total_p = meta.get('total_pdfs', 0)
    v_idx_info = f" (001 - {total_v:03d})" if total_v > 0 else ""
    p_idx_info = f" (001 - {total_p:03d})" if total_p > 0 else ""

    return (
        f"{title_line}\n\n"
        f"📚 <b>Batch Name:</b> <i>{safe_title}</i>\n\n"
        "<blockquote>📌 <b>App Name:</b> KGS IAS\n"
        f"🆔 <b>Batch ID:</b> <code>{meta['course_id']}</code>\n"
        f"💰 <b>Price:</b> {meta['price']}\n"
        "💳 <b>Purchased:</b> ❌ NO\n"
        f"📅 <b>Start Date:</b> {meta['start_at']}\n"
        f"⏳ <b>Validity:</b> {meta['start_at']} to {meta['end_at']}\n"
        f"🖼️ <b>Thumbnail:</b> <a href=\"{meta['thumb_url']}\">Click Here to View</a>{channel_line}</blockquote>\n\n"
        "📊 <b>Content Summary:</b>\n"
        f"<blockquote>• 📁 <b>Total Topics:</b> {meta['total_topics']} Subjects\n"
        f"• 📽️ <b>Total Videos:</b> {total_v} Videos{v_idx_info}\n"
        f"• 📘 <b>Total PDFs:</b> {total_p} PDFs{p_idx_info}</blockquote>"
    )

def send_batch_extraction_card(chat_id, course_id, message_id=None, batch_obj=None):
    course_id = str(course_id).strip()
    if not batch_obj:
        batch_obj = get_batch_details(course_id)

    topics = fetch_topics(course_id)
    if not batch_obj and not topics:
        err_text = (
            f"❌ <b>Batch ID <code>{course_id}</code> Not Found!</b>\n\n"
            "Kripya sahi Batch ID bhejein (jaise <code>406</code>, <code>1237</code>) ya /courses type karke list dekhein."
        )
        if message_id:
            safe_edit_text(bot, chat_id, message_id, err_text)
        else:
            bot.send_message(chat_id, err_text)
        return

    meta = build_batch_metadata(course_id, topics)
    text = format_telegram_caption(meta)

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(f"📽️ Video Classes ({meta['total_videos']})", callback_data=f"vclasses_{course_id}"),
        types.InlineKeyboardButton(f"📄 PDF Notes ({meta['total_pdfs']})", callback_data=f"pdfnotes_{course_id}")
    )
    markup.add(types.InlineKeyboardButton("🎬 Download All Videos (Full Batch)", callback_data=f"dl_all_v_{course_id}"))
    markup.add(types.InlineKeyboardButton("📚 Download All PDFs (Full Batch)", callback_data=f"dl_all_pdf_{course_id}"))
    markup.add(types.InlineKeyboardButton("📄 Extract TXT Links (All Content)", callback_data=f"extract_txt_{course_id}"))
    markup.add(types.InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu_home"))

    if message_id:
        safe_edit_text(bot, chat_id, message_id, text, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)

def send_topics_list(chat_id, course_id, message_id=None):
    topics = fetch_topics(course_id)
    if not topics:
        text = f"⚠️ No topics available for Course ID <code>{course_id}</code>."
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0"))
        if message_id:
            safe_edit_text(bot, chat_id, message_id, text, reply_markup=markup)
        else:
            bot.send_message(chat_id, text, reply_markup=markup)
        return

    text = f"📖 <b>Topics (Batch ID: {course_id}):</b>\nTotal: {len(topics)} topics found\n\nSelect a topic:"
    markup = types.InlineKeyboardMarkup(row_width=1)
    for t in topics:
        name = t.get("name", "Topic")
        v_count = t.get("videos", 0)
        if len(name) > 36:
            name = name[:33] + "..."
        markup.add(types.InlineKeyboardButton(f"📁 {name} ({v_count} 🎥)", callback_data=f"t_{t['id']}"))

    markup.add(types.InlineKeyboardButton("⬅️ Back to Batch Details", callback_data=f"c_{course_id}"))
    markup.add(types.InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0"))

    if message_id:
        safe_edit_text(bot, chat_id, message_id, text, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)

def send_lectures_list(chat_id, topic_id, message_id=None):
    videos, notes = fetch_lectures(topic_id)
    if not videos and not notes:
        text = "⚠️ No lectures or notes found in this topic."
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0"))
        if message_id:
            safe_edit_text(bot, chat_id, message_id, text, reply_markup=markup)
        else:
            bot.send_message(chat_id, text, reply_markup=markup)
        return

    text = f"🎥 <b>Lectures & Notes:</b> ({len(videos)} Videos, {len(notes)} PDFs)\nSelect an item to get links:"
    markup = types.InlineKeyboardMarkup(row_width=1)
    for v in videos:
        name = v.get("name", "Lecture")
        if len(name) > 36:
            name = name[:33] + "..."
        markup.add(types.InlineKeyboardButton(f"▶️ {name}", callback_data=f"v_{v['id']}"))
    for n in notes:
        name = n.get("name", "Note")
        if len(name) > 36:
            name = name[:33] + "..."
        markup.add(types.InlineKeyboardButton(f"📄 {name}", callback_data=f"v_{n['id']}"))

    markup.add(types.InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0"))

    if message_id:
        safe_edit_text(bot, chat_id, message_id, text, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)

def send_lecture_details(chat_id, video_id, message_id=None):
    data = fetch_video_details(video_id)
    hd_url = data.get("hd_video_url")
    sd_url = data.get("video_url")
    pdfs = data.get("pdfs", [])

    lines = ["🎬 <b>Lecture Direct Links:</b>", ""]
    if hd_url:
        lines.append(f"🔗 <b>HD Video Stream:</b>\n<code>{hd_url}</code>\n")
    if sd_url and sd_url != hd_url:
        lines.append(f"🔗 <b>SD Video Stream:</b>\n<code>{sd_url}</code>\n")
    if pdfs:
        lines.append("📄 <b>Class PDF Notes:</b>")
        for p in pdfs:
            p_title = html.escape(p.get('title', 'PDF Note'))
            p_url = p.get('url', '')
            lines.append(f"• <a href=\"{p_url}\">{p_title}</a>")

    if not hd_url and not sd_url and not pdfs:
        lines.append("❌ Could not retrieve links for this lecture.")

    text = "\n".join(lines)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0"))

    if message_id:
        safe_edit_text(bot, chat_id, message_id, text, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)

def fetch_details_with_progress(bot_obj, chat_id, status_msg_id, all_ids, item_label="Links"):
    total = len(all_ids)
    video_details_map = {}
    if not total:
        return video_details_map

    completed_count = 0
    last_update_time = [0.0]

    def fetch_one(xid):
        nonlocal completed_count
        det = fetch_video_details(xid)
        completed_count += 1
        now = time.time()
        if now - last_update_time[0] >= 2.0 or completed_count == total:
            last_update_time[0] = now
            percent = int((completed_count / total) * 100)
            filled = int(10 * completed_count // total)
            bar = "█" * filled + "░" * (10 - filled)
            try:
                safe_edit_text(
                    bot_obj,
                    chat_id,
                    status_msg_id,
                    f"⏳ <b>Extracting Real {item_label}...</b>\n\n"
                    f"📊 <b>Progress:</b> [{bar}] {completed_count}/{total} ({percent}%)\n"
                    f"<i>Please wait, fetching direct stream & PDF URLs...</i>"
                )
            except Exception:
                pass
        return xid, det

    with ThreadPoolExecutor(max_workers=25) as ex:
        results = list(ex.map(fetch_one, all_ids))
        for xid, det in results:
            video_details_map[xid] = det

    return video_details_map

def map_demo_videos_to_topics(topics, demo_videos):
    """Maps demo/foundation/orientation videos to the most relevant topic or creates a dedicated topic."""
    topic_map = {t.get('id'): [] for t in topics} if topics else {}
    unmatched = []

    keyword_map = [
        ("econ", "Economics"),
        ("polity", "Polity"),
        ("government", "Polity"),
        ("constitution", "Polity"),
        ("hist", "History"),
        ("stone age", "History"),
        ("biol", "Biology"),
        ("cell", "Biology"),
        ("phys", "Physics"),
        ("chem", "Chemistry"),
        ("math", "Mathematics"),
        ("number system", "Mathematics"),
        ("geog", "Geography"),
        ("engl", "English"),
        ("computer", "Computer"),
        ("current", "Current Affairs"),
        ("descript", "Descriptive"),
        ("essay", "Essay"),
        ("art", "Art & Culture"),
    ]

    for v in demo_videos:
        v_title = (v.get("name") or "").lower()
        matched = False

        # Match by subject keyword
        for kw, subj in keyword_map:
            if kw in v_title:
                for t in topics:
                    if subj.lower() in (t.get("name") or "").lower():
                        topic_map[t['id']].append(v)
                        matched = True
                        break
            if matched:
                break

        # Match by teacher name in topic title
        if not matched:
            for t in topics:
                t_words = [w for w in re.findall(r'\b[A-Za-z]{4,}\b', t.get("name", "")) if w.lower() not in ["sir", "mam", "batch", "class", "optional", "foundation"]]
                for tw in t_words:
                    if tw.lower() in v_title:
                        topic_map[t['id']].append(v)
                        matched = True
                        break
                if matched:
                    break

        if not matched:
            unmatched.append(v)

    return topic_map, unmatched

def generate_batch_txt(course_id, progress_callback=None):
    course_id = str(course_id).strip()
    topics = fetch_topics(course_id)
    if not topics:
        batch_details = get_batch_details(course_id)
        if batch_details and isinstance(batch_details.get("classroom"), list) and batch_details.get("classroom"):
            topics = list(batch_details["classroom"])

    topic_lectures = {}
    all_videos = []
    all_notes = []

    def fetch_topic_content(t):
        t_id = t.get("id")
        v, n = fetch_lectures(t_id)
        return t_id, v, n

    if topics:
        with ThreadPoolExecutor(max_workers=15) as ex:
            for t_id, v, n in ex.map(fetch_topic_content, topics):
                v_sorted = sorted(v, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
                n_sorted = sorted(n, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
                topic_lectures[t_id] = (v_sorted, n_sorted)
                all_videos.extend(v_sorted)
                all_notes.extend(n_sorted)

    # Failover: If no videos could be retrieved via topic lesson endpoints (e.g. 401 error)
    if not all_videos:
        demo_videos = fetch_course_demo_videos(course_id)
        if demo_videos:
            if not topics:
                topics = [{"id": 999999, "name": "Available Foundation & Demo Lectures", "videos": len(demo_videos), "notes": 0}]
            topic_mapping, unmatched = map_demo_videos_to_topics(topics, demo_videos)
            for t in topics:
                t_id = t.get("id")
                mapped_v = topic_mapping.get(t_id, [])
                if mapped_v:
                    v_sorted = sorted(mapped_v, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
                    cur_v, cur_n = topic_lectures.get(t_id, ([], []))
                    topic_lectures[t_id] = (v_sorted, cur_n)
                    all_videos.extend(v_sorted)
            if unmatched:
                unmatched_topic = {"id": 999999, "name": "Orientation, Foundation & Demo Lectures", "videos": len(unmatched), "notes": 0}
                topics.append(unmatched_topic)
                u_sorted = sorted(unmatched, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
                topic_lectures[999999] = (u_sorted, [])
                all_videos.extend(u_sorted)

    if not topics and not all_videos:
        return None, None, None, f"No content found for Batch ID {course_id}"

    all_fetch_ids = [v["id"] for v in all_videos if v.get("id")] + [n["id"] for n in all_notes if n.get("id")]

    total = len(all_fetch_ids)
    video_details_map = {}
    if total:
        completed_count = 0
        last_update_time = [0.0]

        def fetch_one(xid):
            nonlocal completed_count
            det = fetch_video_details(xid)
            completed_count += 1
            now = time.time()
            if progress_callback and (now - last_update_time[0] >= 1.5 or completed_count == total):
                last_update_time[0] = now
                try:
                    progress_callback(completed_count, total)
                except Exception:
                    pass
            return xid, det

        with ThreadPoolExecutor(max_workers=30) as ex:
            results = list(ex.map(fetch_one, all_fetch_ids))
            for xid, det in results:
                video_details_map[xid] = det

    attached_pdfs_count = 0
    for v in all_videos:
        v_det = video_details_map.get(v.get("id"), {})
        v_pdfs = v_det.get("pdfs") or v.get("pdfs") or []
        for p in v_pdfs:
            if p.get("url") and p.get("url") != "N/A":
                attached_pdfs_count += 1

    meta = build_batch_metadata(course_id, topics, all_videos=all_videos, all_notes=all_notes)
    if not all_notes and attached_pdfs_count > 0:
        meta["total_pdfs"] = attached_pdfs_count

    divider_115 = "=" * 115
    sub_divider_115 = "-" * 115

    lines = format_txt_header(meta, custom_title="✅ KGS IAS Batch Extraction! (All Content Links)")

    ist = zoneinfo.ZoneInfo("Asia/Kolkata")
    def get_date(item, stream_url=None):
        pub = item.get("published_at")
        if pub:
            try:
                if "T" in str(pub):
                    dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
                    return dt.astimezone(ist).strftime("%Y-%m-%d")
                return str(pub)[:10]
            except Exception:
                return str(pub)[:10]
        if stream_url:
            m = re.search(r'kgss-(\d{9,10})', stream_url)
            if m:
                try:
                    return datetime.fromtimestamp(int(m.group(1)), tz=ist).strftime("%Y-%m-%d")
                except Exception:
                    pass
        return None

    def clean_yt(u):
        m1 = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', u)
        if m1:
            return f"https://www.youtube.com/watch?v={m1.group(1)}"
        m2 = re.search(r'youtu\.be/([a-zA-Z0-9_-]+)', u)
        if m2:
            return f"https://www.youtube.com/watch?v={m2.group(1)}"
        return u

    total_v = meta.get('total_videos', 0)
    total_p = meta.get('total_pdfs', 0)
    v_pad = max(3, len(str(total_v))) if total_v else 3
    n_pad = max(3, len(str(total_p))) if total_p else 3

    global_v_idx = 0
    global_n_idx = 0

    for t_idx, topic in enumerate(topics, 1):
        t_id = topic.get("id")
        t_name = topic.get("name", f"Topic {t_id}")
        videos, notes = topic_lectures.get(t_id, ([], []))

        lines.append(divider_115)
        lines.append(f"📁 [{t_idx:02d}] {t_name} (Topic ID: {t_id})")
        lines.append(f"📊 Summary: {len(videos)} Lectures | {len(notes)} PDF Notes")
        lines.append(sub_divider_115)

        if videos:
            lines.append(f"🎥 [LECTURES / CLASSES - TOPIC TOTAL: {len(videos)}]:")
            for v_idx, v in enumerate(videos, 1):
                global_v_idx += 1
                v_id = v.get("id")
                v_name = v.get("name", "Video Lecture")
                v_details = video_details_map.get(v_id, {})
                hd_url = v_details.get("hd_video_url") or ""
                sd_url = v_details.get("video_url") or v.get("video_url") or ""

                hd_is_ak = "akamaized.net" in hd_url
                sd_is_ak = "akamaized.net" in sd_url
                hd_is_yt = "youtu" in hd_url
                sd_is_yt = "youtu" in sd_url

                player_link = None
                stream_link = None

                if hd_is_ak and sd_is_ak:
                    if hd_url != sd_url:
                        player_link = hd_url
                        stream_link = sd_url
                    else:
                        stream_link = sd_url
                elif hd_is_ak and not sd_is_ak:
                    stream_link = hd_url
                elif sd_is_ak and not hd_is_ak:
                    stream_link = sd_url
                elif hd_is_yt or sd_is_yt:
                    yt_u = sd_url if sd_is_yt else hd_url
                    stream_link = clean_yt(yt_u)
                else:
                    stream_link = sd_url or hd_url or None

                v_date = get_date(v, stream_link or player_link)

                clean_name = clean_item_title(v_name)
                lines.append(f"   {global_v_idx:0{v_pad}d}. {clean_name}")
                if v_date:
                    lines.append(f"       📅 Date: {v_date}")
                if player_link:
                    lines.append(f"       ▶ HD / Player Link: {player_link}")
                if stream_link and stream_link != "N/A":
                    lines.append(f"       🎬 Video Stream Link: {stream_link}")

                v_pdfs = v_details.get("pdfs") or v.get("pdfs") or []
                if v_pdfs:
                    for p in v_pdfs:
                        p_url = p.get("url")
                        if p_url and p_url != "N/A":
                            p_title = clean_item_title(p.get("title") or "Lecture PDF")
                            lines.append(f"       📄 Lecture PDF ({p_title}): {p_url}")
                lines.append("")
        else:
            lines.append("🎥 [LECTURES / CLASSES - TOTAL 0]:")
            lines.append("   (No lecture videos available in this topic)")

        lines.append(sub_divider_115)
        if notes:
            lines.append(f"📄 [STUDY NOTES / PDFs - TOPIC TOTAL: {len(notes)}]:")
            for n_idx, n in enumerate(notes, 1):
                global_n_idx += 1
                n_id = n.get("id")
                n_name = n.get("name", "PDF Note")
                n_details = video_details_map.get(n_id, {})
                download_url = n_details.get("video_url")
                if not download_url and n_details.get("pdfs"):
                    download_url = n_details.get("pdfs")[0].get("url")
                if not download_url:
                    download_url = f"https://study-mate.in/api/video/{n_id}"

                n_date = get_date(n)
                clean_n_name = clean_item_title(n_name)
                lines.append(f"   {global_n_idx:0{n_pad}d}. {clean_n_name}")
                if n_date:
                    lines.append(f"       📅 Date: {n_date}")
                if "study-mate.in" in download_url:
                    lines.append(f"       🔗 View Online: {download_url}")
                else:
                    lines.append(f"       📥 Direct PDF Download: {download_url}")
                lines.append("")
        else:
            lines.append("📄 [STUDY NOTES / PDFs - TOTAL 0]:")
            lines.append("   (No separate notes available in this topic)")

        lines.append("")
        lines.append("")
        lines.append("")

    file_content = "\n".join(lines)
    clean_title = re.sub(r'[^a-zA-Z0-9_\-]', '_', meta.get('title', '')).strip('_')[:35]
    if clean_title and clean_title != f"Batch_{course_id}":
        filename = f"Batch_{course_id}_{clean_title}_All_Links.txt"
    else:
        filename = f"Batch_{course_id}_All_Links.txt"
    return file_content, filename, meta, None

def handle_extract_txt(chat_id, course_id):
    status_msg = bot.send_message(
        chat_id,
        f"⏳ <b>Extracting Real Streaming & PDF Links for Batch ID <code>{course_id}</code>...</b>\n<i>Please wait, initializing...</i>"
    )

    def on_prog(done, total):
        percent = int((done / total) * 100)
        filled = int(10 * done // total)
        bar = "█" * filled + "░" * (10 - filled)
        try:
            safe_edit_text(
                bot,
                chat_id,
                status_msg.message_id,
                f"⏳ <b>Extracting Real Lectures & PDFs for Batch ID <code>{course_id}</code>...</b>\n\n"
                f"📊 <b>Progress:</b> [{bar}] {done}/{total} ({percent}%)\n"
                f"<i>Please wait, fetching direct stream & PDF URLs...</i>"
            )
        except Exception:
            pass

    file_content, filename, meta, err = generate_batch_txt(course_id, progress_callback=on_prog)
    if not file_content:
        safe_edit_text(bot, chat_id, status_msg.message_id, f"❌ {err or 'Failed to extract content'}")
        return

    caption = format_telegram_caption(meta, custom_title="📄 <b>✅ KGS IAS Batch Extraction! (All Links TXT)</b>")

    try:
        sent_msg, sent_to_channel, ch_cfg = send_document_to_destinations(chat_id, file_content, filename, caption=caption)
        if sent_to_channel:
            ch_name = f"@{ch_cfg.get('channel_username')}" if ch_cfg.get('channel_username') else (ch_cfg.get('channel_title') or str(ch_cfg.get('channel_id')))
            msg_link = ""
            if ch_cfg.get('channel_username') and getattr(sent_msg, 'message_id', None):
                msg_link = f'\n👉 <a href="https://t.me/{ch_cfg["channel_username"]}/{sent_msg.message_id}">View Post in Channel</a>'

            safe_edit_text(
                bot,
                chat_id,
                status_msg.message_id,
                f"✅ <b>TXT File Successfully Uploaded to Channel!</b>\n\n"
                f"📚 <b>Batch:</b> <i>{html.escape(meta.get('title', ''))}</i>\n"
                f"🆔 <b>Batch ID:</b> <code>{course_id}</code>\n"
                f"📄 <b>File:</b> <code>{filename}</code>\n"
                f"📢 <b>Target Channel:</b> <b>{html.escape(ch_name)}</b>{msg_link}"
            )
        else:
            try:
                bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Error sending extracted txt: {e}")
        safe_edit_text(bot, chat_id, status_msg.message_id, f"❌ Error sending file: {e}")

def start_range_extraction(chat_id, min_id, max_id):
    if chat_id in ACTIVE_RANGE_TASKS and not ACTIVE_RANGE_TASKS[chat_id].is_set():
        bot.send_message(
            chat_id,
            "⚠️ <b>Ek range extraction pehle se run ho rahi hai!</b>\n\n"
            "Use cancel/stop karne ke liye /stop send karein."
        )
        return

    all_batches = fetch_batches()
    matched_batches = [
        b for b in all_batches
        if b.get("id") is not None and str(b.get("id")).isdigit() and min_id <= int(b["id"]) <= max_id
    ]
    matched_batches.sort(key=lambda b: int(b["id"]))

    if not matched_batches:
        diff = max_id - min_id + 1
        if diff <= 100:
            matched_batches = [{"id": i, "title": f"Batch {i}"} for i in range(min_id, max_id + 1)]
        else:
            bot.send_message(
                chat_id,
                f"❌ <b>Batch list me <code>{min_id}</code> se <code>{max_id}</code> ke beech koi batch nahi mila!</b>\n\n"
                "Kripya sahi range bhejein ya /ids command se sabhi batch IDs ki list dekhein."
            )
            return

    MAX_LIMIT = 150
    if len(matched_batches) > MAX_LIMIT:
        bot.send_message(
            chat_id,
            f"⚠️ <b>Range Limit Exceeded ({len(matched_batches)} Batches)!</b>\n\n"
            f"Telegram rate limits aur stability ke liye ek baar me maximum <b>{MAX_LIMIT}</b> batches allowed hain.\n\n"
            f"👉 Kripya chhota range de, jaise: <code>{min_id}-{min_id + MAX_LIMIT - 1}</code>"
        )
        return

    stop_event = threading.Event()
    ACTIVE_RANGE_TASKS[chat_id] = stop_event

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🛑 Stop Extraction", callback_data=f"stop_range_{chat_id}"))

    # Sample batch preview in initial message
    preview_items = [f"• <code>[{b.get('id')}]</code> {html.escape(str(b.get('title', ''))[:30])}" for b in matched_batches[:4]]
    preview_text = "\n".join(preview_items)
    if len(matched_batches) > 4:
        preview_text += f"\n<i>...aur {len(matched_batches) - 4} anya batches</i>"

    status_msg = bot.send_message(
        chat_id,
        f"🚀 <b>Batch Range Extraction Started!</b>\n\n"
        f"🔢 <b>Range:</b> <code>{min_id} - {max_id}</code>\n"
        f"📋 <b>Total Batches:</b> <b>{len(matched_batches)}</b>\n\n"
        f"<b>Batches Found:</b>\n{preview_text}\n\n"
        f"<i>Sabhi batches ka TXT file create karke ek-ek karke send hoga...</i>",
        reply_markup=markup
    )

    def worker():
        success_count = 0
        skipped_count = 0
        total_batches = len(matched_batches)

        for idx, b in enumerate(matched_batches, 1):
            if stop_event.is_set():
                break

            cid = str(b.get("id"))
            b_title = b.get("title", f"Batch {cid}")
            if len(b_title) > 30:
                b_title = b_title[:27] + "..."

            percent = int((idx / total_batches) * 100)
            filled = int(10 * idx // total_batches)
            bar = "█" * filled + "░" * (10 - filled)
            try:
                safe_edit_text(
                    bot,
                    chat_id,
                    status_msg.message_id,
                    f"⏳ <b>Batch Range Extraction in Progress...</b>\n\n"
                    f"📊 <b>Overall Progress:</b> [{bar}] {idx}/{total_batches} ({percent}%)\n"
                    f"🎯 <b>Processing:</b> Batch <code>[{cid}]</code> <i>{html.escape(b_title)}</i>\n"
                    f"✅ <b>Sent:</b> {success_count} | ⚠️ <b>Skipped:</b> {skipped_count}\n\n"
                    f"<i>TXT link file taiyar ho rahi hai...</i>",
                    reply_markup=markup
                )
            except Exception:
                pass

            def on_batch_prog(done, total):
                if total > 0 and (done % 30 == 0 or done == total):
                    try:
                        cfg = load_channel_config()
                        ch_name = f"@{cfg.get('channel_username')}" if cfg.get('channel_username') else (cfg.get('channel_title') or "Target Channel")
                        safe_edit_text(
                            bot,
                            chat_id,
                            status_msg.message_id,
                            f"⏳ <b>Batch Range Extraction in Progress...</b>\n\n"
                            f"📊 <b>Overall Progress:</b> [{bar}] {idx}/{total_batches} ({percent}%)\n"
                            f"🎯 <b>Processing:</b> Batch <code>[{cid}]</code> <i>{html.escape(b_title)}</i>\n"
                            f"📢 <b>Target Channel:</b> {html.escape(ch_name)}\n"
                            f"📥 <i>Fetching links: {done}/{total} items...</i>\n"
                            f"✅ <b>Uploaded to Channel:</b> {success_count} | ⚠️ <b>Skipped:</b> {skipped_count}",
                            reply_markup=markup
                        )
                    except Exception:
                        pass

            file_content, filename, meta, err = generate_batch_txt(cid, progress_callback=on_batch_prog)

            if stop_event.is_set():
                break

            if not file_content:
                skipped_count += 1
                logger.info(f"Batch {cid} skipped: {err}")
                time.sleep(0.5)
                continue

            custom_title = f"📄 <b>✅ [{idx}/{total_batches}] Batch Extraction: {cid}</b>"
            caption = format_telegram_caption(meta, custom_title=custom_title)

            try:
                sent_msg, sent_to_channel, ch_cfg = send_document_to_destinations(chat_id, file_content, filename, caption=caption)
                success_count += 1
            except Exception as e:
                err_str = str(e).lower()
                logger.error(f"Error sending batch {cid} in range: {e}")
                if "flood" in err_str:
                    wait_s = 15
                    m_wait = re.search(r'retry after (\d+)', err_str)
                    if m_wait:
                        wait_s = int(m_wait.group(1)) + 2
                    logger.warning(f"Flood wait {wait_s}s on batch {cid}")
                    time.sleep(wait_s)
                    try:
                        sent_msg, sent_to_channel, ch_cfg = send_document_to_destinations(chat_id, file_content, filename, caption=caption)
                        success_count += 1
                    except Exception:
                        skipped_count += 1
                else:
                    skipped_count += 1

            # Polite delay between documents to respect Telegram rate limits
            time.sleep(2.0)

        ACTIVE_RANGE_TASKS.pop(chat_id, None)

        cfg = load_channel_config()
        ch_name = f"@{cfg.get('channel_username')}" if cfg.get('channel_username') else (cfg.get('channel_title') or "Target Channel")

        if stop_event.is_set():
            result_msg = (
                f"🛑 <b>Range Extraction Stopped!</b>\n\n"
                f"🔢 <b>Range:</b> <code>{min_id} - {max_id}</code>\n"
                f"📢 <b>Target Channel:</b> {html.escape(ch_name)}\n"
                f"✅ <b>Successfully Uploaded to Channel:</b> <b>{success_count}</b> files\n"
                f"⚠️ <b>Skipped / Empty:</b> <b>{skipped_count}</b>\n\n"
                f"<i>Extraction user dwara rok di gayi.</i>"
            )
        else:
            result_msg = (
                f"🎉 <b>Batch Range Extraction Completed!</b>\n\n"
                f"🔢 <b>Range:</b> <code>{min_id} - {max_id}</code>\n"
                f"📢 <b>Target Channel:</b> {html.escape(ch_name)}\n"
                f"📊 <b>Total Batches:</b> <b>{total_batches}</b>\n"
                f"✅ <b>Successfully Uploaded to Channel:</b> <b>{success_count}</b> files\n"
                f"⚠️ <b>Skipped / Empty:</b> <b>{skipped_count}</b>"
            )

        try:
            safe_edit_text(bot, chat_id, status_msg.message_id, result_msg)
        except Exception:
            bot.send_message(chat_id, result_msg)

    threading.Thread(target=worker, daemon=True).start()

def handle_dl_all_videos(chat_id, course_id):
    status_msg = bot.send_message(
        chat_id,
        f"🎬 <b>Preparing Video Downloader File for Batch ID <code>{course_id}</code>...</b>\n<i>Please wait...</i>"
    )
    topics = fetch_topics(course_id)
    if not topics:
        batch_details = get_batch_details(course_id)
        if batch_details and isinstance(batch_details.get("classroom"), list) and batch_details.get("classroom"):
            topics = list(batch_details["classroom"])

    all_videos = []
    topic_lectures = {}
    if topics:
        for topic in topics:
            t_id = topic.get("id")
            videos, _ = fetch_lectures(t_id)
            v_sorted = sorted(videos, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
            topic_lectures[t_id] = v_sorted
            all_videos.extend(v_sorted)

    if not all_videos:
        demo_videos = fetch_course_demo_videos(course_id)
        if demo_videos:
            if not topics:
                topics = [{"id": 999999, "name": "Available Foundation & Demo Lectures", "videos": len(demo_videos), "notes": 0}]
            topic_mapping, unmatched = map_demo_videos_to_topics(topics, demo_videos)
            for t in topics:
                t_id = t.get("id")
                mapped_v = topic_mapping.get(t_id, [])
                if mapped_v:
                    v_sorted = sorted(mapped_v, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
                    topic_lectures[t_id] = v_sorted
                    all_videos.extend(v_sorted)
            if unmatched:
                topics.append({"id": 999999, "name": "Orientation, Foundation & Demo Lectures", "videos": len(unmatched), "notes": 0})
                u_sorted = sorted(unmatched, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
                topic_lectures[999999] = u_sorted
                all_videos.extend(u_sorted)

    if not all_videos:
        safe_edit_text(bot, chat_id, status_msg.message_id, f"❌ No videos found for Batch ID <code>{course_id}</code>.")
        return

    v_ids = [v["id"] for v in all_videos if v.get("id")]
    video_details_map = fetch_details_with_progress(bot, chat_id, status_msg.message_id, v_ids, item_label="Video Stream Links")

    meta = build_batch_metadata(course_id, topics, all_videos=all_videos, all_notes=[])
    lines = format_txt_header(meta, custom_title="🎬 KGS IAS - 1DM / IDM VIDEO DOWNLOAD LIST")
    lines.extend([
        "# INSTRUCTIONS:",
        "# 1. Android: Open 1DM / 1DM+ -> Menu -> Import Links from text file.",
        "# 2. PC: Import in IDM / Free Download Manager or use yt-dlp.",
        "# 3. VLC: Media -> Open Network Stream -> Paste link to play directly.",
        "",
        "-" * 90,
        ""
    ])
    v_pad = max(3, len(str(len(all_videos)))) if all_videos else 3
    global_v_idx = 0
    for topic in topics:
        t_id = topic.get("id")
        t_name = topic.get("name", f"Topic {t_id}")
        videos = topic_lectures.get(t_id, [])
        if not videos:
            continue
        lines.append(f"\n### TOPIC: {t_name} (ID: {t_id})\n")
        for v in videos:
            global_v_idx += 1
            v_id = v.get("id")
            v_name = v.get("name", "Video Lecture")
            v_details = video_details_map.get(v_id, {})
            stream_url = v_details.get("hd_video_url") or v_details.get("video_url") or f"https://study-mate.in/api/video/{v_id}"
            clean_name = clean_item_title(v_name)
            lines.append(f"{global_v_idx:0{v_pad}d}. {clean_name}")
            lines.append(f"{stream_url}\n")

    file_content = "\n".join(lines)
    filename = f"Batch_{course_id}_1DM_Video_Download.txt"
    caption = format_telegram_caption(meta, custom_title="🎬 <b>All Video Download Links Ready!</b>")

    try:
        sent_msg, sent_to_channel, ch_cfg = send_document_to_destinations(chat_id, file_content, filename, caption=caption)
        if sent_to_channel:
            ch_name = f"@{ch_cfg.get('channel_username')}" if ch_cfg.get('channel_username') else (ch_cfg.get('channel_title') or str(ch_cfg.get('channel_id')))
            safe_edit_text(
                bot,
                chat_id,
                status_msg.message_id,
                f"🎬 <b>Video Downloader TXT Uploaded to Channel!</b>\n\n"
                f"📚 <b>Batch:</b> <i>{html.escape(meta.get('title', ''))}</i>\n"
                f"🆔 <b>Batch ID:</b> <code>{course_id}</code>\n"
                f"📄 <b>File:</b> <code>{filename}</code>\n"
                f"📢 <b>Target Channel:</b> <b>{html.escape(ch_name)}</b>"
            )
        else:
            try:
                bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass
    except Exception as e:
        safe_edit_text(bot, chat_id, status_msg.message_id, f"❌ Error sending file: {e}")

def handle_dl_all_pdfs(chat_id, course_id):
    status_msg = bot.send_message(
        chat_id,
        f"📚 <b>Preparing PDF Notes File for Batch ID <code>{course_id}</code>...</b>\n<i>Please wait...</i>"
    )
    topics = fetch_topics(course_id)
    if not topics:
        batch_details = get_batch_details(course_id)
        if batch_details and isinstance(batch_details.get("classroom"), list) and batch_details.get("classroom"):
            topics = list(batch_details["classroom"])

    topic_lectures = {}
    all_notes = []
    all_videos = []
    if topics:
        for topic in topics:
            t_id = topic.get("id")
            videos, notes = fetch_lectures(t_id)
            v_sorted = sorted(videos, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
            n_sorted = sorted(notes, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
            topic_lectures[t_id] = (v_sorted, n_sorted)
            all_notes.extend(n_sorted)
            all_videos.extend(v_sorted)

    if not all_notes and not all_videos:
        demo_videos = fetch_course_demo_videos(course_id)
        if demo_videos:
            if not topics:
                topics = [{"id": 999999, "name": "Available Foundation & Demo Lectures", "videos": len(demo_videos), "notes": 0}]
            topic_mapping, unmatched = map_demo_videos_to_topics(topics, demo_videos)
            for t in topics:
                t_id = t.get("id")
                mapped_v = topic_mapping.get(t_id, [])
                if mapped_v:
                    v_sorted = sorted(mapped_v, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
                    topic_lectures[t_id] = (v_sorted, [])
                    all_videos.extend(v_sorted)
            if unmatched:
                topics.append({"id": 999999, "name": "Orientation, Foundation & Demo Lectures", "videos": len(unmatched), "notes": 0})
                u_sorted = sorted(unmatched, key=lambda x: (x.get('published_at') or '', int(x.get('id') or 0)))
                topic_lectures[999999] = (u_sorted, [])
                all_videos.extend(u_sorted)

    all_fetch_ids = [n["id"] for n in all_notes if n.get("id")]
    # Also fetch details for demo videos to retrieve their attached PDFs
    if not all_notes and all_videos:
        all_fetch_ids = [v["id"] for v in all_videos if v.get("id")]

    video_details_map = fetch_details_with_progress(bot, chat_id, status_msg.message_id, all_fetch_ids, item_label="PDF Notes")
    if not all_notes and all_videos:
        for v in all_videos:
            v_id = v.get("id")
            v["pdfs"] = video_details_map.get(v_id, {}).get("pdfs") or []

    meta = build_batch_metadata(course_id, topics, all_videos=all_videos, all_notes=all_notes)
    lines = format_txt_header(meta, custom_title="📚 KGS IAS - ALL CLASS PDF NOTES & STUDY MATERIAL")
    total_pdf_count = 0
    p_pad = max(3, len(str(len(all_notes) + 50))) if all_notes else 3
    for topic in topics:
        t_id = topic.get("id")
        t_name = topic.get("name", f"Topic {t_id}")
        videos, notes = topic_lectures.get(t_id, ([], []))

        topic_pdfs = []
        for v in videos:
            for p in (v.get("pdfs") or []):
                p_url = p.get("url")
                if p_url:
                    topic_pdfs.append((p.get("title") or v.get("name", "Lecture Note"), p_url))
        for n in notes:
            n_id = n.get("id")
            n_name = n.get("name", "Class Note")
            n_details = video_details_map.get(n_id, {})
            download_url = n_details.get("video_url")
            if not download_url and n_details.get("pdfs"):
                download_url = n_details.get("pdfs")[0].get("url")
            if not download_url:
                download_url = f"https://study-mate.in/api/video/{n_id}"
            topic_pdfs.append((n_name, download_url))

        if topic_pdfs:
            lines.append(f"\n📁 TOPIC: {t_name} (ID: {t_id}) | PDFs: {len(topic_pdfs)}")
            lines.append("-" * 80)
            for p_title, p_url in topic_pdfs:
                total_pdf_count += 1
                clean_p_title = clean_item_title(p_title)
                lines.append(f"{total_pdf_count:0{p_pad}d}. {clean_p_title}")
                lines.append(f"   Download URL: {p_url}\n")

    meta["total_pdfs"] = total_pdf_count
    for idx_l, line_str in enumerate(lines[:20]):
        if "Total PDFs:" in line_str:
            lines[idx_l] = f"• 📘 Total PDFs: {total_pdf_count} PDFs (Index: 001 to {total_pdf_count:03d})"
            break

    file_content = "\n".join(lines)
    filename = f"Batch_{course_id}_All_PDF_Notes.txt"
    caption = format_telegram_caption(meta, custom_title="📚 <b>All PDF Notes Ready!</b>")

    try:
        sent_msg, sent_to_channel, ch_cfg = send_document_to_destinations(chat_id, file_content, filename, caption=caption)
        if sent_to_channel:
            ch_name = f"@{ch_cfg.get('channel_username')}" if ch_cfg.get('channel_username') else (ch_cfg.get('channel_title') or str(ch_cfg.get('channel_id')))
            safe_edit_text(
                bot,
                chat_id,
                status_msg.message_id,
                f"📚 <b>PDF Notes TXT Uploaded to Channel!</b>\n\n"
                f"📚 <b>Batch:</b> <i>{html.escape(meta.get('title', ''))}</i>\n"
                f"🆔 <b>Batch ID:</b> <code>{course_id}</code>\n"
                f"📄 <b>File:</b> <code>{filename}</code>\n"
                f"📢 <b>Target Channel:</b> <b>{html.escape(ch_name)}</b>"
            )
        else:
            try:
                bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass
    except Exception as e:
        safe_edit_text(bot, chat_id, status_msg.message_id, f"❌ Error sending file: {e}")

def send_batch_ids_file(chat_id):
    batches = fetch_batches()
    lines = ["--- KGS IAS ALL BATCH IDS LIST ---", ""]
    for b in batches:
        lines.append(f"Batch ID: {b.get('id')} | Title: {b.get('title')}")
    file_content = "\n".join(lines)
    file_bytes = io.BytesIO(file_content.encode('utf-8'))
    caption = "📋 Here is the complete text file containing all Batch IDs and Course titles!"
    bot.send_document(chat_id, file_bytes, caption=caption, visible_file_name="KGS_IAS_All_Batch_IDs.txt")

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    data = call.data
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if data == "menu_home":
        text, markup = get_welcome_content(call.from_user.first_name if call.from_user else "Student")
        safe_edit_text(bot, chat_id, msg_id, text, reply_markup=markup)
    elif data == "download_batch_ids":
        send_batch_ids_file(chat_id)
    elif data.startswith("page_courses_"):
        page = int(data.split("_")[-1])
        send_courses_page(chat_id, message_id=msg_id, page=page)
    elif data.startswith("c_"):
        course_id = data.split("_")[-1]
        send_batch_extraction_card(chat_id, course_id, message_id=msg_id)
    elif data.startswith("vclasses_"):
        course_id = data.split("_")[-1]
        send_topics_list(chat_id, course_id, message_id=msg_id)
    elif data.startswith("pdfnotes_"):
        course_id = data.split("_")[-1]
        send_topics_list(chat_id, course_id, message_id=msg_id)
    elif data.startswith("extract_txt_"):
        course_id = data.split("_")[-1]
        handle_extract_txt(chat_id, course_id)
    elif data == "btn_ask_range":
        AWAITING_RANGE_INPUT[chat_id] = True
        msg_text = (
            "🔢 <b>Batch ID Range Extraction</b>\n\n"
            "Kripya Batch ID ki range send karein.\n\n"
            "<b>Examples:</b>\n"
            "• <code>100-120</code>\n"
            "• <code>400 to 450</code>\n"
            "• <code>1200 1237</code>\n\n"
            "👉 Bot is range ke sabhi batch ka TXT file bna ke ek-ek karke send karega!\n"
            "<i>(Cancel karne ke liye /cancel bhejein)</i>"
        )
        bot.send_message(chat_id, msg_text)
    elif data.startswith("stop_range_"):
        target_cid = data.replace("stop_range_", "").strip()
        target_chat = int(target_cid) if target_cid.lstrip("-").isdigit() else chat_id
        if target_chat in ACTIVE_RANGE_TASKS:
            ACTIVE_RANGE_TASKS[target_chat].set()
            bot.answer_callback_query(call.id, text="🛑 Extraction roki ja rahi hai...")
        else:
            bot.answer_callback_query(call.id, text="Extraction already finished.")
    elif data == "btn_channel_menu":
        send_channel_status(chat_id, message_id=msg_id)
    elif data == "btn_set_channel":
        AWAITING_CHANNEL_INPUT[chat_id] = True
        bot.send_message(
            chat_id,
            "📢 <b>Set Upload Channel</b>\n\n"
            "Kripya channel ka <b>@username</b> ya <b>ID</b> (jaise <code>-1001234567890</code>) bhejein,\n"
            "ya channel se koi message yahan <b>Forward</b> karein!\n\n"
            "⚠️ <i>Make sure bot is added as Admin in the channel with Post Messages permission!</i>\n"
            "<i>(Cancel karne ke liye /cancel bhejein)</i>"
        )
    elif data == "btn_unset_channel":
        clear_channel_config()
        send_channel_status(chat_id, message_id=msg_id)
    elif data == "btn_test_channel":
        cfg = load_channel_config()
        target_id = cfg.get("channel_id")
        if not target_id:
            bot.answer_callback_query(call.id, text="Koi channel set nahi hai!", show_alert=True)
            return
        try:
            test_m = bot.send_message(
                target_id,
                "🧪 <b>StudyMate Bot Test Post:</b>\nChannel connection verified successfully! ✅"
            )
            bot.answer_callback_query(call.id, text="✅ Test post channel me send ho gaya!", show_alert=True)
            bot.send_message(chat_id, f"✅ Test message successfully posted to channel! (Message ID: {test_m.message_id})")
        except Exception as e:
            bot.answer_callback_query(call.id, text=f"❌ Test failed: {str(e)[:45]}", show_alert=True)
            bot.send_message(
                chat_id,
                f"❌ <b>Channel Post Error:</b> {e}\n\n"
                "Kripya check karein ki bot channel me added hai aur use Administrator (Post Messages) permissions mili hain."
            )
    elif data.startswith("dl_all_v_"):
        course_id = data.split("_")[-1]
        handle_dl_all_videos(chat_id, course_id)
    elif data.startswith("dl_all_pdf_"):
        course_id = data.split("_")[-1]
        handle_dl_all_pdfs(chat_id, course_id)
    elif data.startswith("t_"):
        topic_id = data.split("_")[-1]
        send_lectures_list(chat_id, topic_id, message_id=msg_id)
    elif data.startswith("v_"):
        video_id = data.split("_")[-1]
        send_lecture_details(chat_id, video_id, message_id=msg_id)

@bot.message_handler(func=lambda msg: True, content_types=['text', 'audio', 'document', 'photo', 'sticker', 'video', 'video_note', 'voice', 'location', 'contact'])
def handle_all_messages(message):
    text = (message.text or message.caption or "").strip()
    chat_id = message.chat.id

    # If user was prompted for channel input
    if AWAITING_CHANNEL_INPUT.get(chat_id):
        if text.lower() in ["/cancel", "cancel", "stop", "/stop"]:
            AWAITING_CHANNEL_INPUT.pop(chat_id, None)
            bot.reply_to(message, "❌ Channel setup cancel kar diya gaya.")
            return

        target_input = None
        if message.forward_from_chat:
            target_input = message.forward_from_chat.id
        elif hasattr(message, 'forward_origin') and message.forward_origin and hasattr(message.forward_origin, 'chat'):
            target_input = message.forward_origin.chat.id
        elif text:
            target_input = text

        if target_input:
            chat_obj, err = parse_and_validate_channel(target_input)
            if not chat_obj:
                bot.reply_to(
                    message,
                    f"❌ <b>Error:</b> {err}\n\n"
                    "Kripya sahi channel @username ya ID bhejein, ya channel se message forward karein.\n"
                    "<i>(Cancel karne ke liye /cancel bhejein)</i>"
                )
                return

            AWAITING_CHANNEL_INPUT.pop(chat_id, None)
            save_channel_config(chat_obj.id, chat_obj.title or "Channel", chat_obj.username or "")
            warn_msg = ""
            if err == "WARNING_NOT_ADMIN":
                warn_msg = "\n\n⚠️ <i>Note: Bot channel me Admin nahi dikh raha hai. Kripya bot ko channel me Administrator banakar 'Post Messages' permission zaroor dein!</i>"

            uname = f"@{chat_obj.username}" if chat_obj.username else f"ID: {chat_obj.id}"
            bot.reply_to(
                message,
                f"✅ <b>Upload Channel Set Ho Gaya!</b>\n\n"
                f"📢 <b>Channel:</b> {html.escape(chat_obj.title or 'Channel')}\n"
                f"🆔 <b>ID:</b> <code>{chat_obj.id}</code>\n"
                f"🔗 <b>Username / Target:</b> {uname}{warn_msg}\n\n"
                f"👉 Ab jab bhi koi Batch TXT extract hoga, wo is channel me bhi automatically upload ho jayega."
            )
            return

    if not text:
        return

    # If user was prompted for range input
    if AWAITING_RANGE_INPUT.get(chat_id):
        if text.lower() in ["/cancel", "cancel", "stop", "/stop"]:
            AWAITING_RANGE_INPUT.pop(chat_id, None)
            bot.reply_to(message, "❌ Range input cancelled.")
            return

        rng = extract_batch_range(text)
        if rng:
            AWAITING_RANGE_INPUT.pop(chat_id, None)
            start_range_extraction(chat_id, rng[0], rng[1])
            return
        else:
            bot.reply_to(
                message,
                "⚠️ Sahi format me range bhejein, jaise:\n• <code>100-120</code>\n• <code>400 to 450</code>\n\nYa cancel karne ke liye /cancel type karein."
            )
            return

    # Check if text is a batch range like "100-120" or "range 100 120"
    rng = extract_batch_range(text)
    if rng:
        start_range_extraction(chat_id, rng[0], rng[1])
        return

    # Check if text is a single batch ID
    extracted_id = extract_batch_id(text)
    if extracted_id:
        status_msg = bot.reply_to(message, f"⚡ <b>Extracting Batch {extracted_id} Details...</b>\n<i>Please wait a moment...</i>")
        send_batch_extraction_card(message.chat.id, extracted_id, message_id=status_msg.message_id)
        return

    bot.reply_to(message, f"🔍 Searching courses for <b>'{html.escape(text)}'</b>...")
    send_courses_page(message.chat.id, page=0, search_query=text)

def main():
    logger.info("Initializing KGS IAS Telegram Bot with telebot (pyTelegramBotAPI)...")
    me = bot.get_me()
    logger.info(f"KGS IAS Telegram Bot is LIVE as: @{me.username} ({me.first_name})")
    bot.infinity_polling(timeout=15, long_polling_timeout=10)

if __name__ == "__main__":
    main()
