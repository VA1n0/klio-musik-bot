import os
import asyncio
import sqlite3
import logging
import subprocess
from datetime import datetime
from dotenv import load_dotenv
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from shazamio import Shazam
import yt_dlp

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 7314990219

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
shazam = Shazam()

SEARCH_CACHE = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- DATABASE ---
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

# --- WEB SERVER ---
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

# --- SEARCH & DOWNLOAD FUNCTIONS ---
def search_tracks(query: str, limit: int = 5) -> list:
    """Шукає варіанти треків через yt_dlp"""
    ydl_opts = {
        'quiet': True,
        'default_search': f'scsearch{limit}',
        'no_warnings': True,
        'ignoreerrors': True,
    }
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(query, download=False)
            if info and 'entries' in info:
                for entry in info['entries']:
                    if entry:
                        title = entry.get('title') or entry.get('fulltitle') or "Без назви"
                        url = entry.get('webpage_url') or entry.get('url')
                        if url:
                            results.append({'title': title, 'url': url})
        except Exception as e:
            logging.error(f"Search error: {e}")
    return results

def download_by_url(url: str, output_filename: str) -> str:
    """Завантажує обраний трек за посиланням"""
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f"{output_filename}.%(ext)s",
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return f"{output_filename}.mp3"

def extract_audio_from_file(input_path: str, output_path: str) -> bool:
    try:
        cmd = ['ffmpeg', '-y', '-i', input_path, '-vn', '-acodec', 'libmp3lame', '-q:a', '2', output_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

# --- COMMANDS ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    track_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer("Привіт! Надішли назву пісні або відео/голосове — я знайду схожі варіанти.")

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        total, active_today = get_analytics()
        await message.answer(f"📊 **Статистика KLIO:**\n\n👤 Всього: **{total}**\n🔥 Активних: **{active_today}**", parse_mode="Markdown")

# --- HANDLERS ---
async def send_search_results(message: types.Message, query: str, status_msg: types.Message):
    tracks = await asyncio.to_thread(search_tracks, query, 5)
    if not tracks:
        await status_msg.edit_text(f"❌ Нічого не знайдено за запитом: **{query}**", parse_mode="Markdown")
        return

    user_id = message.from_user.id
    SEARCH_CACHE[user_id] = tracks

    builder = InlineKeyboardBuilder()
    for idx, tr in enumerate(tracks):
        builder.button(text=f"🎵 {tr['title'][:40]}", callback_data=f"dl_{idx}")
    builder.adjust(1)

    await status_msg.edit_text(f"🔍 Знайдено варіанти за запитом **{query}**:\nОберіть потрібний трек:", reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.message(F.text)
async def handle_text(message: types.Message):
    track_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    msg = await message.answer("🔎 Шукаю варіанти...")
    await send_search_results(message, message.text, msg)

@dp.message(F.video | F.voice | F.audio | F.video_note)
async def handle_media(message: types.Message):
    track_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    msg = await message.answer("🎧 Розпізнаю через Shazam...")
    
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
            extracted_file = "extracted_audio.mp3"
            if extract_audio_from_file(temp_file, extracted_file):
                await message.answer_audio(audio=types.FSInputFile(extracted_file), caption="🎵 Аудіо з вашого відео")
                await msg.delete()
                os.remove(extracted_file)
            else:
                await msg.edit_text("❌ Не вдалося розпізнати трек.")
            return

        query = f"{track.get('subtitle')} - {track.get('title')}"
        await send_search_results(message, query, msg)

    except Exception as e:
        logging.error(f"Media error: {e}")
        await msg.edit_text("❌ Помилка під час обробки.")
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)

# --- CALLBACK FOR BUTTONS ---
@dp.callback_query(F.data.startswith("dl_"))
async def handle_download_callback(callback: types.CallbackQuery):
    idx = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    tracks = SEARCH_CACHE.get(user_id)
    if not tracks or idx >= len(tracks):
        await callback.answer("⏳ Сесія пошуку застаріла. Введіть запит знову.", show_alert=True)
        return

    selected_track = tracks[idx]
    await callback.message.edit_text(f"⏳ Завантажую: **{selected_track['title']}**...", parse_mode="Markdown")
    
    out_name = f"song_{user_id}_{idx}"
    try:
        file_path = await asyncio.to_thread(download_by_url, selected_track['url'], out_name)
        audio_file = types.FSInputFile(file_path)
        await callback.message.answer_audio(audio=audio_file, caption=f"🎵 {selected_track['title']}")
        await callback.message.delete()
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logging.error(f"Download error: {e}")
        await callback.message.edit_text("❌ Не вдалося завантажити цей варіант.")

# --- MAIN RUN ---
async def main():
    asyncio.create_task(start_web_server())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
