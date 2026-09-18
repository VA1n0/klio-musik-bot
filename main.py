import os
import asyncio
import sqlite3
import logging
from datetime import datetime
from dotenv import load_dotenv
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from shazamio import Shazam
import yt_dlp

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Вкажіть свій особистий Telegram ID (дізнатися можна в @userinfobot)
ADMIN_ID = 7314990219 

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
shazam = Shazam()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- РОБОТА З БАЗОЮ ДАНИХ (SQLite) ---
def init_db():
    conn = sqlite3.connect("bot_analytics.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT,
            last_active TEXT
        )
    """)
    conn.commit()
    conn.close()

def track_user(user_id: int, username: str, first_name: str):
    conn = sqlite3.connect("bot_analytics.db")
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO users (user_id, username, first_name, joined_at, last_active)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_active = excluded.last_active
    """, (user_id, username, first_name or "", now, now))
    conn.commit()
    conn.close()

def get_analytics():
    conn = sqlite3.connect("bot_analytics.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM users WHERE last_active LIKE ?", (f"{today}%",))
    active_today = cursor.fetchone()[0]
    conn.close()
    return total_users, active_today

init_db()

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def handle_ping(request):
    return web.Response(text="Bot is live!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"HTTP-сервер успішно запущено на порту {port}")

# --- ФУНКЦІЯ ЗАВАНТАЖЕННЯ АУДІО ---
def download_audio_by_query(query: str) -> dict:
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'downloaded_song.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'default_search': 'ytsearch1',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=True)
        if 'entries' in info and len(info['entries']) > 0:
            info = info['entries'][0]
        title = info.get("title", "Аудіотрек")
        return {"title": title, "file": "downloaded_song.mp3"}

# --- ХЕНДЛЕРИ КОМАНД ТА АДМІН-ПАНЕЛІ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    track_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer("Привіт! Надішли мені назву пісні, посилання або відео/кружечок/голосове — я знайду трек.")

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        total, active_today = get_analytics()
        stats_text = (
            "📊 **Статистика бота KLIO:**\n\n"
            f"👤 Всього унікальних юзерів: **{total}**\n"
            f"🔥 Активних сьогодні (DAU): **{active_today}**"
        )
        await message.answer(stats_text, parse_mode="Markdown")

@dp.message(Command("export_users"))
async def export_users(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        conn = sqlite3.connect("bot_analytics.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        rows = cursor.fetchall()
        conn.close()

        file_path = "users.txt"
        with open(file_path, "w") as f:
            for row in rows:
                f.write(f"{row[0]}\n")

        await message.answer_document(types.FSInputFile(file_path))
        if os.path.exists(file_path):
            os.remove(file_path)

# --- ХЕНДЛЕРИ ПОШУКУ ТА МЕДІА ---
@dp.message(F.text)
async def handle_text(message: types.Message):
    track_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    msg = await message.answer("🔎 Шукаю трек...")
    try:
        data = await asyncio.to_thread(download_audio_by_query, message.text)
        audio_file = types.FSInputFile(data["file"])
        await message.answer_audio(audio=audio_file, caption=f"🎵 {data['title']}")
        await msg.delete()
        if os.path.exists(data["file"]):
            os.remove(data["file"])
    except Exception as e:
        logging.error(f"Text error: {e}")
        await msg.edit_text("❌ Не вдалося знайти або завантажити трек.")

@dp.message(F.video | F.voice | F.audio | F.video_note)
async def handle_media(message: types.Message):
    track_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    msg = await message.answer("🎧 Розпізнаю аудіо через Shazam...")
    
    file_id = (message.video.file_id if message.video 
               else message.voice.file_id if message.voice 
               else message.video_note.file_id if message.video_note 
               else message.audio.file_id)
    
    file = await bot.get_file(file_id)
    temp_file = "temp_media_file"
    await bot.download_file(file.file_path, temp_file)

    try:
        out = await shazam.recognize(temp_file)
        track = out.get("track")
        if not track:
            await msg.edit_text("❌ Shazam не зміг розпізнати трек з цього відео.")
            return

        title = track.get("title")
        subtitle = track.get("subtitle")
        search_query = f"{subtitle} - {title}"
        await msg.edit_text(f"✨ Знайдено: **{search_query}**. Завантажую MP3...")

        data = await asyncio.to_thread(download_audio_by_query, search_query)
        audio_file = types.FSInputFile(data["file"])
        await message.answer_audio(audio=audio_file, caption=f"🎵 {search_query}")
        await msg.delete()

        if os.path.exists(data["file"]):
            os.remove(data["file"])
    except Exception as e:
        logging.error(f"Media error: {e}")
        await msg.edit_text("❌ Помилка під час обробки медіафайлу.")
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)

# --- ГОЛОВНИЙ ЗАПУСК ---
async def main():
    asyncio.create_task(start_web_server())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
