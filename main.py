import os
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from shazamio import Shazam
import yt_dlp

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
shazam = Shazam()

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

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("Привіт! Надішли мені назву пісні, посилання або відео/кружечок/голосове — я знайду трек.")

# Пошук за текстом або посиланням
@dp.message(F.text)
async def handle_text(message: types.Message):
    msg = await message.answer("🔎 Шукаю трек...")
    try:
        data = await asyncio.to_thread(download_audio_by_query, message.text)
        audio_file = types.FSInputFile(data["file"])
        await message.answer_audio(audio=audio_file, caption=f"🎵 {data['title']}")
        await msg.delete()
        if os.path.exists(data["file"]):
            os.remove(data["file"])
    except Exception as e:
        await msg.edit_text("❌ Не вдалося знайти або завантажити трек.")

# Безкоштовне постійне розпізнавання відео / голосових / кружечків
@dp.message(F.video | F.voice | F.audio | F.video_note)
async def handle_media(message: types.Message):
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
        await msg.edit_text("❌ Помилка під час обробки медіафайлу.")
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())