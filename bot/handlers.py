from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import zoneinfo
import html
import logging
import io
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import ITEMS_PER_PAGE
from bot.api import fetch_batches, fetch_topics, fetch_lectures, fetch_video_details

logger = logging.getLogger(__name__)

async def safe_edit_message(target, text, parse_mode="Markdown", reply_markup=None, disable_web_page_preview=True):
    """Safely edit message with specified parse_mode, falling back to plain text if parsing fails."""
    try:
        return await target.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=disable_web_page_preview)
    except Exception as e:
        err_msg = str(e)
        if "Message is not modified" in err_msg:
            return target
        plain_text = re.sub(r'<[^>]+>', '', text).replace("*", "").replace("_", "").replace("`", "").replace(">", "")
        try:
            return await target.edit_text(plain_text, parse_mode=None, reply_markup=reply_markup, disable_web_page_preview=disable_web_page_preview)
        except Exception as e2:
            logger.error(f"Fallback edit_text error: {e2}")
            return None

async def safe_reply_message(msg, text, parse_mode="Markdown", reply_markup=None, disable_web_page_preview=True):
    """Safely reply message with specified parse_mode, falling back to plain text if parsing fails."""
    try:
        return await msg.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=disable_web_page_preview)
    except Exception as e:
        plain_text = re.sub(r'<[^>]+>', '', text).replace("*", "").replace("_", "").replace("`", "").replace(">", "")
        try:
            return await msg.reply_text(plain_text, parse_mode=None, reply_markup=reply_markup, disable_web_page_preview=disable_web_page_preview)
        except Exception as e2:
            logger.error(f"Fallback reply_text error: {e2}")
            return None

def extract_batch_id(text: str) -> str:
    """Smart extractor for Course / Batch IDs from various user input formats."""
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

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message and main menu."""
    user_name = update.effective_user.first_name if update.effective_user else "Student"
    welcome_text = (
        f"👋 *Welcome to KGS IAS Bot, {user_name}!*\n\n"
        "📚 *Features:*\n"
        "• Browse 1000+ Khan Sir & KGS Courses\n"
        "• Send any Batch ID (e.g. `1237` or `666`) for instant Batch Extraction\n"
        "• Extract TXT Links for All Videos & PDFs\n"
        "• View Topics, Video Lectures & Class Notes\n\n"
        "👇 Send any Batch ID or keyword to search!"
    )
    keyboard = [
        [InlineKeyboardButton("📚 Browse All Courses", callback_data="page_courses_0")],
        [InlineKeyboardButton("📋 Download All Batch IDs File", callback_data="download_batch_ids")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await safe_reply_message(update.message, welcome_text, reply_markup=reply_markup)
    elif update.callback_query:
        await safe_edit_message(update.callback_query.message, welcome_text, reply_markup=reply_markup)

async def courses_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, search_query=""):
    """Display paginated courses list."""
    query = update.callback_query
    batches = fetch_batches()
    
    if search_query:
        batches = [b for b in batches if search_query.lower() in b.get("title", "").lower()]
    
    total = len(batches)
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, total)
    current_items = batches[start_idx:end_idx]
    
    if not current_items:
        text = f"❌ No courses found matching *'{search_query}'*."
        keyboard = [[InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if query:
            await safe_edit_message(query.message, text, reply_markup=reply_markup)
        elif update.message:
            await safe_reply_message(update.message, text, reply_markup=reply_markup)
        return

    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    text = f"📚 *KGS IAS Courses* (Page {page + 1} of {total_pages}):\nTotal: {total} courses\n\nSelect a course below:"
    
    keyboard = []
    for b in current_items:
        title = b.get("title", "Untitled Course")
        b_id = b.get("id")
        if len(title) > 30:
            title = title[:27] + "..."
        keyboard.append([InlineKeyboardButton(f"🎓 [{b_id}] {title}", callback_data=f"c_{b_id}")])
    
    # Pagination buttons
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page_courses_{page-1}"))
    if end_idx < total:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_courses_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)
        
    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="menu_home")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await safe_edit_message(query.message, text, reply_markup=reply_markup)
    elif update.message:
        await safe_reply_message(update.message, text, reply_markup=reply_markup)

async def send_batch_extraction_card(update: Update, context: ContextTypes.DEFAULT_TYPE, course_id, batch_obj=None, target_msg=None):
    """Display rich batch extraction card formatted with content summary and action buttons."""
    query = update.callback_query
    if query:
        await query.answer("Extracting Batch Details...")

    course_id = str(course_id).strip()

    if not batch_obj:
        batches = fetch_batches()
        batch_obj = next((b for b in batches if str(b.get("id")) == course_id), None)

    # Fetch topics to calculate content summary stats
    topics = fetch_topics(course_id)

    # If neither in batch list nor has topics on API, notify not found
    if not batch_obj and not topics:
        err_text = (
            f"❌ *Batch ID `{course_id}` Not Found!*\n\n"
            "Kripya sahi Batch ID bhejein (jaise `666`, `1237`) ya `/courses` type karke list dekhein."
        )
        if target_msg:
            await safe_edit_message(target_msg, err_text)
        elif query:
            await safe_edit_message(query.message, err_text)
        elif update.message:
            await safe_reply_message(update.message, err_text)
        return

    title = batch_obj.get("title", f"Batch {course_id}") if batch_obj else f"Batch {course_id}"
    start_at = batch_obj.get("start_at") or "N/A" if batch_obj else "N/A"
    end_at = batch_obj.get("end_at") or "Lifetime" if batch_obj else "Lifetime"
    thumb_url = batch_obj.get("image_thumb") or batch_obj.get("image_large") or "https://i.postimg.cc/x1M0YN5Z/sunny.jpg" if batch_obj else "https://i.postimg.cc/x1M0YN5Z/sunny.jpg"

    total_topics = len(topics)
    total_videos = sum(int(t.get("videos", 0) or 0) for t in topics)
    total_pdfs = sum(int(t.get("notes", 0) or 0) for t in topics)

    safe_title = html.escape(title)
    card_lines = [
        "<b>✅ KGS IAS Batch Extraction!</b>",
        "",
        f"📚 <b>Batch Name:</b> <i>{safe_title}</i>",
        "",
        "<blockquote>📌 <b>App Name:</b> KGS IAS",
        f"🆔 <b>Batch ID:</b> <code>{course_id}</code>",
        "💰 <b>Price:</b> Paid",
        "💳 <b>Purchased:</b> ❌ NO",
        f"📅 <b>Start Date:</b> {start_at}",
        f"⏳ <b>Validity:</b> {start_at} to {end_at}",
        f"🖼️ <b>Thumbnail:</b> <a href=\"{thumb_url}\">Click Here to View</a></blockquote>",
        "",
        "📊 <b>Content Summary:</b>",
        f"<blockquote>• 📁 <b>Total Topics:</b> {total_topics} Subjects",
        f"• 📽️ <b>Total Videos:</b> {total_videos} Videos",
        f"• 📘 <b>Total PDFs:</b> {total_pdfs} PDFs</blockquote>"
    ]
    text = "\n".join(card_lines)
    keyboard = [
        [
            InlineKeyboardButton(f"📽️ Video Classes ({total_videos})", callback_data=f"vclasses_{course_id}"),
            InlineKeyboardButton(f"📄 PDF Notes ({total_pdfs})", callback_data=f"pdfnotes_{course_id}")
        ],
        [InlineKeyboardButton("🎬 Download All Videos (Full Batch)", callback_data=f"dl_all_v_{course_id}")],
        [InlineKeyboardButton("📚 Download All PDFs (Full Batch)", callback_data=f"dl_all_pdf_{course_id}")],
        [InlineKeyboardButton("📄 Extract TXT Links (All Content)", callback_data=f"extract_txt_{course_id}")],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu_home")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if target_msg:
        await safe_edit_message(target_msg, text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    elif query:
        await safe_edit_message(query.message, text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
    elif update.message:
        await safe_reply_message(update.message, text, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)

async def topic_list(update: Update, context: ContextTypes.DEFAULT_TYPE, course_id, course_title=""):
    """Display topics for selected course ID."""
    query = update.callback_query
    if query:
        await query.answer("Fetching topics...")
        
    if not course_title:
        batches = fetch_batches()
        for b in batches:
            if str(b.get("id")) == str(course_id):
                course_title = b.get("title", "")
                break

    topics = fetch_topics(course_id)
    title_str = f" for *'{course_title}'*" if course_title else f" (ID: {course_id})"
        
    if not topics:
        text = f"⚠️ No topics available for Course ID `{course_id}`{title_str}."
        keyboard = [[InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if query:
            await safe_edit_message(query.message, text, reply_markup=reply_markup)
        elif update.message:
            await safe_reply_message(update.message, text, reply_markup=reply_markup)
        return
        
    text = f"📖 *Topics{title_str}:*\nBatch ID: `{course_id}` | ({len(topics)} topics found)\n\nSelect a topic:"
    keyboard = []
    for t in topics:
        name = t.get("name", "Topic")
        v_count = t.get("videos", 0)
        if len(name) > 36:
            name = name[:33] + "..."
        keyboard.append([InlineKeyboardButton(f"📁 {name} ({v_count} 🎥)", callback_data=f"t_{t['id']}")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Back to Batch Details", callback_data=f"c_{course_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await safe_edit_message(query.message, text, reply_markup=reply_markup)
    elif update.message:
        await safe_reply_message(update.message, text, reply_markup=reply_markup)

async def lecture_list(update: Update, context: ContextTypes.DEFAULT_TYPE, topic_id):
    """Display lectures for selected topic."""
    query = update.callback_query
    await query.answer("Fetching lectures...")
    
    videos, notes = fetch_lectures(topic_id)

    if not videos and not notes:
        text = "⚠️ No lectures or notes found in this topic."
        keyboard = [[InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0")]]
        await safe_edit_message(query.message, text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    text = f"🎥 *Lectures & Notes:* ({len(videos)} Videos, {len(notes)} PDFs)\nSelect a lecture to get direct video link:"
    keyboard = []
    
    for v in videos:
        name = v.get("name", "Lecture")
        if len(name) > 36:
            name = name[:33] + "..."
        keyboard.append([InlineKeyboardButton(f"▶️ {name}", callback_data=f"v_{v['id']}")])
        
    for n in notes:
        name = n.get("name", "Note")
        if len(name) > 36:
            name = name[:33] + "..."
        keyboard.append([InlineKeyboardButton(f"📄 {name}", callback_data=f"v_{n['id']}")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_edit_message(query.message, text, reply_markup=reply_markup)

async def lecture_links(update: Update, context: ContextTypes.DEFAULT_TYPE, video_id):
    """Fetch direct stream and PDF links for selected lecture."""
    query = update.callback_query
    await query.answer("Getting video links...")
    
    data = fetch_video_details(video_id)

    hd_url = data.get("hd_video_url")
    sd_url = data.get("video_url")
    pdfs = data.get("pdfs", [])

    lines = ["🎬 *Lecture Direct Links:*", ""]
    if hd_url:
        lines.append(f"🔗 *HD Video Stream:*\n`{hd_url}`\n")
    if sd_url and sd_url != hd_url:
        lines.append(f"🔗 *SD Video Stream:*\n`{sd_url}`\n")
    if pdfs:
        lines.append("📄 *Class PDF Notes:*")
        for p in pdfs:
            p_title = p.get('title', 'PDF Note').replace('[', '(').replace(']', ')')
            p_url = p.get('url', '')
            lines.append(f"• [{p_title}]({p_url})")

    if not hd_url and not sd_url and not pdfs:
        lines.append("❌ Could not retrieve links for this lecture.")

    text = "\n".join(lines)
    keyboard = [[InlineKeyboardButton("🔙 Back to Courses", callback_data="page_courses_0")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_edit_message(query.message, text, reply_markup=reply_markup, disable_web_page_preview=True)

async def extract_all_links_txt(update: Update, context: ContextTypes.DEFAULT_TYPE, course_id):
    """Extract all real video stream links and PDF download links into a text file for the user."""
    query = update.callback_query
    if query:
        await query.answer("Extracting Batch TXT...")

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏳ *Extracting Real Streaming & PDF Links for Batch ID `{course_id}`...*\n_Please wait, collecting all lecture streams..._",
        parse_mode="Markdown"
    )

    batches = fetch_batches()
    batch_obj = next((b for b in batches if str(b.get("id")) == str(course_id)), None)
    title = batch_obj.get("title", f"Batch_{course_id}") if batch_obj else f"Batch_{course_id}"

    topics = fetch_topics(course_id)
    if not topics:
        await status_msg.edit_text(f"❌ No topics found for Batch ID `{course_id}` to extract.", parse_mode="Markdown")
        return

    # 1. Fetch all lectures for all topics
    topic_lectures = {}
    all_videos = []
    all_notes = []

    for topic in topics:
        t_id = topic.get("id")
        videos, notes = fetch_lectures(t_id)
        topic_lectures[t_id] = (videos, notes)
        all_videos.extend(videos)
        all_notes.extend(notes)

    total_videos_count = len(all_videos)
    total_notes_count = len(all_notes)

    # 2. Fetch real stream links and note download links in parallel with ThreadPoolExecutor
    video_details_map = {}
    all_fetch_ids = [v["id"] for v in all_videos if v.get("id")] + [n["id"] for n in all_notes if n.get("id")]
    if all_fetch_ids:
        with ThreadPoolExecutor(max_workers=35) as ex:
            results = list(ex.map(fetch_video_details, all_fetch_ids))
            for xid, det in zip(all_fetch_ids, results):
                video_details_map[xid] = det

    # 3. Format the TXT file in exact Study-Mate Batch 285 style
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

        # Videos
        if videos:
            lines.append(f"🎥 [LECTURES / CLASSES - TOTAL {len(videos)}]:")
            for v_idx, v in enumerate(videos, 1):
                v_id = v.get("id")
                v_name = v.get("name", "Video Lecture")
                v_details = video_details_map.get(v_id, {})
                hd_url = v_details.get("hd_video_url") or ""
                sd_url = v_details.get("video_url") or ""
                # Main link is direct stream (Akamaized). YouTube link is backup ONLY when main link is missing
                main_link = None
                yt_link = None

                for u in [sd_url, hd_url]:
                    if u and u != "N/A":
                        if "youtube.com" in u.lower() or "youtu.be" in u.lower():
                            if not yt_link:
                                yt_link = u
                        else:
                            if not main_link:
                                main_link = u

                primary_link = main_link or yt_link or None
                v_date = get_date(v, sd_url or hd_url)

                lines.append(f"   {v_idx:02d}. {v_name}")
                if v_date:
                    lines.append(f"       📅 Date: {v_date}")
                if primary_link and primary_link != "N/A":
                    lines.append(f"       🎬 Video Stream Link: {primary_link}")

                # Collect PDFs for this video
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

        # Notes
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
        f"📄 *Extraction Completed!*\n"
        f"Here is your TXT file containing all real video stream links and PDF URLs for *Batch ID {course_id}*.\n\n"
        f"📊 *Summary:*\n"
        f"• Topics: {len(topics)}\n"
        f"• Videos: {total_videos_count}\n"
        f"• PDFs: {total_notes_count}"
    )

    try:
        await context.bot.send_document(
            chat_id=chat_id,
            document=file_bytes,
            filename=filename,
            caption=caption,
            parse_mode="Markdown"
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error sending extraction document: {e}")
        file_bytes.seek(0)
        try:
            await context.bot.send_document(
                chat_id=chat_id,
                document=file_bytes,
                filename=filename,
                caption=f"📄 Extraction Completed for Batch ID {course_id}!"
            )
            try:
                await status_msg.delete()
            except Exception:
                pass
        except Exception as e2:
            logger.error(f"Fallback send_document error: {e2}")
            await status_msg.edit_text(f"❌ Failed to send document: {e2}")

async def download_all_videos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, course_id):
    """Generate and send dedicated 1DM/IDM video download links file with instructions."""
    query = update.callback_query
    if query:
        await query.answer("Preparing Video Download File...")

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎬 *Preparing Video Downloader File for Batch ID `{course_id}`...*\n_Collecting all HD stream URLs..._",
        parse_mode="Markdown"
    )

    batches = fetch_batches()
    batch_obj = next((b for b in batches if str(b.get("id")) == str(course_id)), None)
    title = batch_obj.get("title", f"Batch_{course_id}") if batch_obj else f"Batch_{course_id}"

    topics = fetch_topics(course_id)
    if not topics:
        await status_msg.edit_text(f"❌ No topics found for Batch ID `{course_id}`.", parse_mode="Markdown")
        return

    topic_lectures = {}
    all_videos = []
    for topic in topics:
        t_id = topic.get("id")
        videos, _ = fetch_lectures(t_id)
        topic_lectures[t_id] = videos
        all_videos.extend(videos)

    total_videos_count = len(all_videos)
    video_details_map = {}
    if all_videos:
        with ThreadPoolExecutor(max_workers=35) as ex:
            v_ids = [v["id"] for v in all_videos]
            results = list(ex.map(fetch_video_details, v_ids))
            for vid, det in zip(v_ids, results):
                video_details_map[vid] = det

    lines = [
        "=" * 90,
        "KGS IAS - 1DM / IDM / ADM / VLC VIDEO DOWNLOAD LIST",
        f"BATCH NAME   : {title}",
        f"BATCH ID     : {course_id}",
        f"TOTAL VIDEOS : {total_videos_count}",
        "=" * 90,
        "",
        "# INSTRUCTIONS:",
        "# 1. Android: Open 1DM / 1DM+ -> Menu -> Import Links from text file.",
        "# 2. PC: Import in IDM / Free Download Manager or use yt-dlp.",
        "# 3. VLC: Media -> Open Network Stream -> Paste link to play directly.",
        "",
        "-" * 90,
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

    caption = (
        f"🎬 *All Video Download Links Ready!*\n"
        f"📚 *Batch:* {title}\n"
        f"🆔 *Batch ID:* `{course_id}` | *Videos:* {total_videos_count}\n\n"
        f"📥 *Sabhi Videos Download Kaise Karein:*\n"
        f"1️⃣ Android me **1DM** ya **1DM+** app install karein.\n"
        f"2️⃣ 1DM me Menu par jayein ➔ **Import Links from file** par click karein.\n"
        f"3️⃣ Is `.txt` file ko select karein. Sabhi videos 1-click me background me download hona start ho jayengi!\n\n"
        f"💻 *PC me:* IDM me 'Import text file' karein ya VLC Player me stream karein."
    )

    try:
        await context.bot.send_document(
            chat_id=chat_id,
            document=file_bytes,
            filename=filename,
            caption=caption,
            parse_mode="Markdown"
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error sending video download document: {e}")
        file_bytes.seek(0)
        try:
            await context.bot.send_document(
                chat_id=chat_id,
                document=file_bytes,
                filename=filename,
                caption=f"🎬 Video Download Links for Batch ID {course_id} ({total_videos_count} Videos)"
            )
            try:
                await status_msg.delete()
            except Exception:
                pass
        except Exception as e2:
            logger.error(f"Fallback send_document error: {e2}")
            await status_msg.edit_text(f"❌ Failed to send document: {e2}")

async def download_all_pdfs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, course_id):
    """Generate and send dedicated PDF notes download links file."""
    query = update.callback_query
    if query:
        await query.answer("Preparing PDF Notes File...")

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"📚 *Preparing PDF Notes File for Batch ID `{course_id}`...*\n_Collecting all PDF note URLs..._",
        parse_mode="Markdown"
    )

    batches = fetch_batches()
    batch_obj = next((b for b in batches if str(b.get("id")) == str(course_id)), None)
    title = batch_obj.get("title", f"Batch_{course_id}") if batch_obj else f"Batch_{course_id}"

    topics = fetch_topics(course_id)
    if not topics:
        await status_msg.edit_text(f"❌ No topics found for Batch ID `{course_id}`.", parse_mode="Markdown")
        return

    lines = [
        "=" * 90,
        "KGS IAS - ALL CLASS PDF NOTES & STUDY MATERIAL",
        f"BATCH NAME : {title}",
        f"BATCH ID   : {course_id}",
        "=" * 90,
        ""
    ]

    total_pdf_count = 0
    for topic in topics:
        t_id = topic.get("id")
        t_name = topic.get("name", f"Topic {t_id}")
        videos, notes = fetch_lectures(t_id)

        topic_pdfs = []
        for v in videos:
            for p in (v.get("pdfs") or []):
                topic_pdfs.append((p.get("title") or v.get("name", "Lecture Note"), p.get("url")))

        for n in notes:
            topic_pdfs.append((n.get("name", "Class Note"), f"https://study-mate.in/api/video/{n.get('id')}"))

        if topic_pdfs:
            lines.append(f"\n📁 TOPIC: {t_name} (ID: {t_id}) | PDFs: {len(topic_pdfs)}")
            lines.append("-" * 80)
            for p_title, p_url in topic_pdfs:
                total_pdf_count += 1
                lines.append(f"📄 {p_title}")
                lines.append(f"   Download URL: {p_url}\n")

    lines.insert(5, f"TOTAL PDFS : {total_pdf_count}")

    file_content = "\n".join(lines)
    file_bytes = io.BytesIO(file_content.encode("utf-8"))
    filename = f"Batch_{course_id}_All_PDF_Notes.txt"

    caption = (
        f"📚 *All PDF Notes Ready!*\n"
        f"Batch: {title}\n"
        f"🆔 Batch ID: `{course_id}` | Total PDFs: {total_pdf_count}\n\n"
        f"Is file me sabhi class PDF notes aur study materials ke direct download links diye gaye hain."
    )

    try:
        await context.bot.send_document(
            chat_id=chat_id,
            document=file_bytes,
            filename=filename,
            caption=caption,
            parse_mode="Markdown"
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error sending PDF document: {e}")
        file_bytes.seek(0)
        try:
            await context.bot.send_document(
                chat_id=chat_id,
                document=file_bytes,
                filename=filename,
                caption=f"📚 All PDF Notes for Batch ID {course_id} ({total_pdf_count} PDFs)"
            )
            try:
                await status_msg.delete()
            except Exception:
                pass
        except Exception as e2:
            logger.error(f"Fallback send_document error: {e2}")
            await status_msg.edit_text(f"❌ Failed to send document: {e2}")


async def send_batch_ids_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send text file containing all 1000+ Batch IDs and titles."""
    batches = fetch_batches()
    lines = ["--- KGS IAS ALL BATCH IDS LIST ---", ""]
    for b in batches:
        lines.append(f"Batch ID: {b.get('id')} | Title: {b.get('title')}")
    
    file_content = "\n".join(lines)
    file_bytes = io.BytesIO(file_content.encode('utf-8'))
    file_bytes.name = "KGS_IAS_All_Batch_IDs.txt"
    
    caption = "📋 Here is the complete text file containing all Batch IDs and Course titles!"
    
    if update.message:
        await update.message.reply_document(document=file_bytes, caption=caption)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_document(document=file_bytes, caption=caption)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback button clicks."""
    query = update.callback_query
    data = query.data
    
    if data == "menu_home":
        await start_command(update, context)
    elif data == "download_batch_ids":
        await send_batch_ids_file(update, context)
    elif data.startswith("page_courses_"):
        page = int(data.split("_")[-1])
        await courses_page(update, context, page=page)
    elif data.startswith("c_"):
        course_id = data.split("_")[-1]
        await send_batch_extraction_card(update, context, course_id)
    elif data.startswith("vclasses_"):
        course_id = data.split("_")[-1]
        await topic_list(update, context, course_id)
    elif data.startswith("pdfnotes_"):
        course_id = data.split("_")[-1]
        await topic_list(update, context, course_id)
    elif data.startswith("extract_txt_"):
        course_id = data.split("_")[-1]
        await extract_all_links_txt(update, context, course_id)
    elif data.startswith("dl_all_v_"):
        course_id = data.split("_")[-1]
        await download_all_videos_handler(update, context, course_id)
    elif data.startswith("dl_all_pdf_"):
        course_id = data.split("_")[-1]
        await download_all_pdfs_handler(update, context, course_id)
    elif data.startswith("t_"):
        topic_id = data.split("_")[-1]
        await lecture_list(update, context, topic_id)
    elif data.startswith("v_"):
        video_id = data.split("_")[-1]
        await lecture_links(update, context, video_id)

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text course search queries or direct Batch ID inputs."""
    query_text = update.message.text.strip()
    
    # 1. Smart Course / Batch ID detection
    extracted_id = extract_batch_id(query_text)
    
    if extracted_id:
        batches = fetch_batches()
        matched_batch = next((b for b in batches if str(b.get("id")) == str(extracted_id)), None)
        
        status_msg = await safe_reply_message(
            update.message,
            f"⚡ *Extracting Batch {extracted_id} Details...*\n_Please wait a moment..._"
        )
        
        await send_batch_extraction_card(
            update, context,
            course_id=extracted_id,
            batch_obj=matched_batch,
            target_msg=status_msg
        )
        return

    # 2. General Keyword Search
    await safe_reply_message(update.message, f"🔍 Searching courses for *'{query_text}'*...")
    await courses_page(update, context, page=0, search_query=query_text)
