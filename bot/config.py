import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8801935577:AAGEehNQczG7pS9-3hejpFzphOFS2xz-L6o")

# API Endpoints
BATCHES_URL = "https://sunnyji7256.github.io/kgs_batch_list/New_Sunny.json"
CLASSROOM_URL = "https://study-mate.in/api/classroom/{}"
LESSON_URL = "https://study-mate.in/api/lesson/{}"
VIDEO_URL = "https://study-mate.in/api/video/{}"

# Pagination settings
ITEMS_PER_PAGE = 8
