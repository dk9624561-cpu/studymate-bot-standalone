import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8957554054:AAHF8lSks33v2GKUrwM10KV9DyepRN5VXaM")

# API Endpoints
BATCHES_URL = "https://sunnyji7256.github.io/kgs_batch_list/New_Sunny.json"
CLASSROOM_URL = "https://study-mate.in/api/classroom/{}"
LESSON_URL = "https://study-mate.in/api/lesson/{}"
VIDEO_URL = "https://study-mate.in/api/video/{}"

# Pagination settings
ITEMS_PER_PAGE = 8

# Default Upload Channel ID or Username (optional, can also be set dynamically via /setchannel)
UPLOAD_CHANNEL = os.getenv("UPLOAD_CHANNEL", "")

# Optional KGS Auth Token (Bearer token for direct official API access)
KGS_TOKEN = os.getenv("KGS_TOKEN", "")

