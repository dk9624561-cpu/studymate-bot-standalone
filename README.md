---
title: StudyMate Telegram Bot
emoji: 📚
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# StudyMate Bot Standalone Project 🚀

A dedicated, modular Python Telegram Bot and Web Application for navigating **StudyMate** (`http://sunnyji01.github.io/Studymate/home.html` -> `study-mate.in`).

---

## 📁 Project Structure

```
studymate-bot-standalone/
├── .env                     # Bot Configuration (Contains BOT_TOKEN)
├── .env.example             # Environment variable template
├── requirements.txt         # Python dependencies
├── Dockerfile               # Production Docker container definition
├── main.py                  # Main entry point launcher script
├── bot/                     # Modular Telegram Bot package
│   ├── __init__.py
│   ├── config.py            # Central settings & API routes
│   ├── api.py               # StudyMate API Client (Courses, Topics, Lectures, Videos)
│   └── handlers.py          # Command, button, & search handlers
├── web_app/                 # Interactive Web UI Application
│   ├── index.html
│   ├── style.css
│   └── app.js
├── start_bot.bat            # One-click Windows Telegram Bot launcher
└── start_web.bat            # One-click Windows Web Server launcher
```

---

## ⚡ Quick Start

### 1. Run Telegram Bot (Local / Server)
```bash
pip install -r requirements.txt
python main.py
```
Or simply double-click `start_bot.bat` on Windows.

### 2. Run Web Application
```bash
python -m http.server 3000 --directory web_app
```
Or double-click `start_web.bat` on Windows, then open `http://localhost:3000` in your browser.

---

## 🐳 Docker Cloud Deployment (Render, Heroku, VPS)

To deploy 24/7 on any cloud container service:
```bash
docker build -t studymate-bot .
docker run -d --env-file .env studymate-bot
```

---

## 🛰️ Key Features

- **1000+ Batches Indexing**: Full support for Khan Sir & KGS courses.
- **Search Engine**: Live keyword filtering for courses.
- **Hierarchical Drilldown**: `Course -> Topic -> Lecture -> Links`.
- **Direct Video & PDF Links**: HD & SD stream URLs and class notes PDFs.
