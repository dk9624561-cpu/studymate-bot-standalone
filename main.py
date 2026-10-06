import os
import sys
import re
import html
import io
import time
import logging
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

from bot.config import BOT_TOKEN, ITEMS_PER_PAGE
from bot.api import fetch_batches, fetch_topics, fetch_lectures, fetch_video_details

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

def get_welcome_content(user_first_name="Student"):
    welcome_text = (
        f"👋 <b>Welcome to KGS IAS Bot, {html.escape(user_first_name)}!</b>\n\n"
        "📚 <b>Features:</b>\n"
        "• Browse 1000+ Khan Sir & KGS Courses\n"
        "• Send any Batch ID (e.g. <code>406</code> or <code>1237</code>) for instant Batch Extraction\n"
        "• Extract TXT Links for All Videos & PDFs\n"
        "• View Topics, Video Lectures & Class Notes\n\n"
        "👇 Send any Batch ID or keyword to search!"
    )
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📚 Browse All Courses", callback_data="page_courses_0"),
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

def send_batch_extraction_card(chat_id, course_id, message_id=None, batch_obj=None):
    course_id = str(course_id).strip()
    if not batch_obj:
        batches = fetch_batches()
        batch_obj = next((b for b in batches if str(b.get("id")) == course_id), None)

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

    title = batch_obj.get("title", f"Batch {course_id}") if batch_obj else f"Batch {course_id}"
    start_at = batch_obj.get("start_at") or "N/A" if batch_obj else "N/A"
    end_at = batch_obj.get("end_at") or "Lifetime" if batch_obj else "Lifetime"
    thumb_url = batch_obj.get("image_thumb") or batch_obj.get("image_large") or "https://i.postimg.cc/x1M0YN5Z/sunny.jpg" if batch_obj else "https://i.postimg.cc/x1M0YN5Z/sunny.jpg"

    total_topics = len(topics)
    total_videos = sum(int(t.get("videos", 0) or 0) for t in topics)
    total_pdfs = sum(int(t.get("notes", 0) or 0) for t in topics)

    safe_title = html.escape(title)
    text = (
        "<b>✅ KGS IAS Batch Extraction!</b>\n\n"
        f"📚 <b>Batch Name:</b> <i>{safe_title}</i>\n\n"
        "<blockquote>📌 <b>App Name:</b> KGS IAS\n"
        f"🆔 <b>Batch ID:</b> <code>{course_id}</code>\n"
        "💰 <b>Price:</b> Paid\n"
        "💳 <b>Purchased:</b> ❌ NO\n"
        f"📅 <b>Start Date:</b> {start_at}\n"
        f"⏳ <b>Validity:</b> {start_at} to {end_at}\n"
        f"🖼️ <b>Thumbnail:</b> <a href=\"{thumb_url}\">Click Here to View</a></blockquote>\n\n"
        "📊 <b>Content Summary:</b>\n"
        f"<blockquote>• 📁 <b>Total Topics:</b> {total_topics} Subjects\n"
        f"• 📽️ <b>Total Videos:</b> {total_videos} Videos\n"
        f"• 📘 <b>Total PDFs:</b> {total_pdfs} PDFs</blockquote>"
    )

    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(f"📽️ Video Classes ({total_videos})", callback_data=f"vclasses_{course_id}"),
        types.InlineKeyboardButton(f"📄 PDF Notes ({total_pdfs})", callback_data=f"pdfnotes_{course_id}")
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

def handle_extract_txt(chat_id, course_id):
    status_msg = bot.send_message(
        chat_id,
        f"⏳ <b>Extracting Real Streaming & PDF Links for Batch ID <code>{course_id}</code>...</b>\n<i>Please wait, initializing...</i>"
    )

    topics = fetch_topics(course_id)
    if not topics:
        safe_edit_text(bot, chat_id, status_msg.message_id, f"❌ No topics found for Batch ID <code>{course_id}</code>.")
        return

    topic_lectures = {}
    all_videos = []
    all_notes = []

    for topic in topics:
        t_id = topic.get("id")
        videos, notes = fetch_lectures(t_id)
        topic_lectures[t_id] = (videos, notes)
        all_videos.extend(videos)
        all_notes.extend(notes)

    all_fetch_ids = [v["id"] for v in all_videos if v.get("id")] + [n["id"] for n in all_notes if n.get("id")]
    video_details_map = fetch_details_with_progress(bot, chat_id, status_msg.message_id, all_fetch_ids, item_label="Lectures & PDFs")

    divider_115 = "=" * 115
    sub_divider_115 = "-" * 115

    lines = [
        divider_115,
        f"                         KGS IAS BATCH {course_id} - COMPLETE LECTURES (VIDEOS) & NOTES (PDFs)",
        "                                   (100% Complete - Har Lecture Aur Note Ka Link)",
        divider_115,
        ""
    ]

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

    for t_idx, topic in enumerate(topics, 1):
        t_id = topic.get("id")
        t_name = topic.get("name", f"Topic {t_id}")
        videos, notes = topic_lectures.get(t_id, ([], []))

        lines.append(divider_115)
        lines.append(f"📁 [{t_idx:02d}] {t_name} (Topic ID: {t_id})")
        lines.append(f"📊 Summary: {len(videos)} Lectures | {len(notes)} PDF Notes")
        lines.append(sub_divider_115)

        if videos:
            lines.append(f"🎥 [LECTURES / CLASSES - TOTAL {len(videos)}]:")
            for v_idx, v in enumerate(videos, 1):
                v_id = v.get("id")
                v_name = v.get("name", "Video Lecture")
                v_details = video_details_map.get(v_id, {})
                hd_url = v_details.get("hd_video_url") or ""
                sd_url = v_details.get("video_url") or v.get("video_url") or ""

                hd_is_ak = "akamaized.net" in hd_url
                sd_is_ak = "akamaized.net" in sd_url
                hd_is_yt = "youtu" in hd_url
                sd_is_yt = "youtu" in sd_url

                def clean_yt(u):
                    m1 = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]+)', u)
                    if m1:
                        return f"https://www.youtube.com/watch?v={m1.group(1)}"
                    m2 = re.search(r'youtu\.be/([a-zA-Z0-9_-]+)', u)
                    if m2:
                        return f"https://www.youtube.com/watch?v={m2.group(1)}"
                    return u

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

                lines.append(f"   {v_idx:02d}. {v_name}")
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
                            lines.append(f"       📄 Lecture PDF: {p_url}")
                lines.append("")
        else:
            lines.append("🎥 [LECTURES / CLASSES - TOTAL 0]:")
            lines.append("   (No lecture videos available in this topic)")

        lines.append(sub_divider_115)
        if notes:
            lines.append(f"📄 [STUDY NOTES / PDFs - TOTAL {len(notes)}]:")
            for n_idx, n in enumerate(notes, 1):
                n_id = n.get("id")
                n_name = n.get("name", "PDF Note")
                n_details = video_details_map.get(n_id, {})
                download_url = n_details.get("video_url")
                if not download_url and n_details.get("pdfs"):
                    download_url = n_details.get("pdfs")[0].get("url")
                if not download_url:
                    download_url = f"https://study-mate.in/api/video/{n_id}"

                n_date = get_date(n)
                lines.append(f"   {n_idx:02d}. {n_name}")
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
    file_bytes = io.BytesIO(file_content.encode("utf-8"))
    filename = f"Batch_{course_id}_All_Links.txt"
    caption = (
        f"📄 <b>Extraction Completed!</b>\n"
        f"All real streaming & PDF URLs for <b>Batch ID {course_id}</b>.\n\n"
        f"📊 <b>Summary:</b> {len(topics)} Topics | {len(all_videos)} Videos | {len(all_notes)} PDFs"
    )

    try:
        bot.send_document(chat_id, file_bytes, caption=caption, visible_file_name=filename)
        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error sending extracted txt: {e}")
        safe_edit_text(bot, chat_id, status_msg.message_id, f"❌ Error sending file: {e}")

def handle_dl_all_videos(chat_id, course_id):
    status_msg = bot.send_message(
        chat_id,
        f"🎬 <b>Preparing Video Downloader File for Batch ID <code>{course_id}</code>...</b>\n<i>Please wait...</i>"
    )
    topics = fetch_topics(course_id)
    if not topics:
        safe_edit_text(bot, chat_id, status_msg.message_id, f"❌ No topics found for Batch ID <code>{course_id}</code>.")
        return

    all_videos = []
    topic_lectures = {}
    for topic in topics:
        t_id = topic.get("id")
        videos, _ = fetch_lectures(t_id)
        topic_lectures[t_id] = videos
        all_videos.extend(videos)

    v_ids = [v["id"] for v in all_videos if v.get("id")]
    video_details_map = fetch_details_with_progress(bot, chat_id, status_msg.message_id, v_ids, item_label="Video Stream Links")

    lines = [
        "=" * 90,
        f"KGS IAS - 1DM / IDM VIDEO DOWNLOAD LIST (BATCH {course_id})",
        f"TOTAL VIDEOS: {len(all_videos)}",
        "=" * 90,
        ""
    ]
    for topic in topics:
        t_id = topic.get("id")
        t_name = topic.get("name", f"Topic {t_id}")
        videos = topic_lectures.get(t_id, [])
        if not videos:
            continue
        lines.append(f"\n### TOPIC: {t_name} (ID: {t_id})\n")
        for v in videos:
            v_id = v.get("id")
            v_name = v.get("name", "Video Lecture")
            v_details = video_details_map.get(v_id, {})
            stream_url = v_details.get("hd_video_url") or v_details.get("video_url") or f"https://study-mate.in/api/video/{v_id}"
            lines.append(f"{v_name}")
            lines.append(f"{stream_url}\n")

    file_content = "\n".join(lines)
    file_bytes = io.BytesIO(file_content.encode("utf-8"))
    filename = f"Batch_{course_id}_1DM_Video_Download.txt"
    caption = f"🎬 <b>All Video Download Links Ready!</b>\nBatch ID: <code>{course_id}</code> | Total: {len(all_videos)} Videos"

    try:
        bot.send_document(chat_id, file_bytes, caption=caption, visible_file_name=filename)
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
        safe_edit_text(bot, chat_id, status_msg.message_id, f"❌ No topics found for Batch ID <code>{course_id}</code>.")
        return

    topic_lectures = {}
    all_notes = []
    all_videos = []
    for topic in topics:
        t_id = topic.get("id")
        videos, notes = fetch_lectures(t_id)
        topic_lectures[t_id] = (videos, notes)
        all_notes.extend(notes)
        all_videos.extend(videos)

    all_fetch_ids = [n["id"] for n in all_notes if n.get("id")]
    video_details_map = fetch_details_with_progress(bot, chat_id, status_msg.message_id, all_fetch_ids, item_label="PDF Notes")

    lines = [
        "=" * 90,
        f"KGS IAS - ALL CLASS PDF NOTES & STUDY MATERIAL (BATCH {course_id})",
        "=" * 90,
        ""
    ]
    total_pdf_count = 0
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
                lines.append(f"📄 {p_title}")
                lines.append(f"   Download URL: {p_url}\n")

    lines.insert(3, f"TOTAL PDFS : {total_pdf_count}")
    file_content = "\n".join(lines)
    file_bytes = io.BytesIO(file_content.encode("utf-8"))
    filename = f"Batch_{course_id}_All_PDF_Notes.txt"
    caption = f"📚 <b>All PDF Notes Ready!</b>\nBatch ID: <code>{course_id}</code> | Total: {total_pdf_count} PDFs"

    try:
        bot.send_document(chat_id, file_bytes, caption=caption, visible_file_name=filename)
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

@bot.message_handler(func=lambda msg: True)
def handle_all_messages(message):
    text = message.text.strip() if message.text else ""
    if not text:
        return

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
