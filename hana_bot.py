"""
Hana - Discord bot (portfolio version)

Features:
  - AI chat persona powered by the Anthropic API (Claude Haiku)
  - Per-channel short-term conversation memory
  - Voice-channel greeting sounds (join / unmute) via FFmpeg
  - YouTube new-upload notifier (polling every 5 minutes)
  - Output filter that keeps the persona's speech style consistent

All personal data (user IDs, channel IDs, role IDs, sound files) lives in
config.json, which is NOT committed. See config.example.json.
"""

import asyncio
import json
import os
import re
from collections import defaultdict, deque

import anthropic
import discord
import requests
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ======================================================
# Configuration
# ======================================================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def load_config() -> dict:
    """Load config.json if present, otherwise fall back to the example file."""
    for name in ("config.json", "config.example.json"):
        path = os.path.join(BASE_DIR, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError("config.json or config.example.json not found")


def int_keys(d: dict) -> dict[int, str]:
    """JSON keys are strings; Discord IDs are ints."""
    return {int(k): v for k, v in d.items()}


CONFIG = load_config()

FFMPEG_EXECUTABLE = CONFIG.get("ffmpeg_executable", "ffmpeg")
ALLOWED_CHANNEL_ID = int(CONFIG["allowed_channel_id"])
OWNER_ID = int(CONFIG["owner_id"])
OWNER_NAME = CONFIG.get("owner_name", "Owner")
ADMIN_ROLE_IDS = [int(r) for r in CONFIG.get("admin_role_ids", [])]
ENABLE_SHUTDOWN_COMMAND = CONFIG.get("enable_shutdown_command", False)

USER_NAME_MAP = int_keys(CONFIG.get("user_names", {}))
GREETING_SOUND_MAP = int_keys(CONFIG.get("greeting_sounds", {}))
JOIN_SOUND_MAP = int_keys(CONFIG.get("join_sounds", {}))
BACK_SOUND_MAP = int_keys(CONFIG.get("back_sounds", {}))
DEFAULT_JOIN_SOUND = CONFIG.get("default_join_sound", "sounds/join_default.mp3")
DEFAULT_BACK_SOUND = CONFIG.get("default_back_sound", "sounds/back_default.mp3")
CHANNEL_LIST = CONFIG.get("youtube_channels", [])

# Secrets come from environment variables only (.env, never committed)
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# ======================================================
# Bot setup
# ======================================================
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Create the API client once and reuse it (reads ANTHROPIC_API_KEY from env)
ai_client = anthropic.AsyncAnthropic()

MAX_MEMORY = 10
conversation_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_MEMORY))

# Simple global lock: while the AI is generating a reply, new messages are ignored
is_processing: bool = False


# ======================================================
# AI chat helpers
# ======================================================
def build_system_prompt() -> str:
    # Persona prompt (Thai). Hana is a cheerful 15-year-old character.
    return f"""คุณชื่อ "ฮานะ" เด็กผู้หญิงอายุ 15 ปี ร่าเริง ขี้เล่น สร้างโดย "{OWNER_NAME}"

กฎที่ต้องทำตามอย่างเคร่งครัด:
1. สรรพนามแทนตัวเอง → ใช้ "ฮานะ" เท่านั้น (ห้ามใช้: ฉัน / หนู / เรา / ผม / I)
2. หางเสียง → ลงท้ายด้วย "ค่ะ", "คะ", "นะคะ", "น้า", "อะ", "งะ" เท่านั้น (ห้ามใช้: ครับ / จ้า / จ๊ะ)
3. เรียกคนอื่นด้วยชื่อ หรือ "พี่" (ห้ามใช้: คุณ / ท่าน / เธอ)
4. เจ้าของฮานะ → ชื่อ "{OWNER_NAME}"
5. ตอบสั้น กระชับ เหมือนวัยรุ่นแชท ไม่เขียนยาวเป็นบทความ
6. "ฮานะไม่รู้งะ~" ใช้เฉพาะเมื่อถูกถามข้อมูลจริงจังที่ไม่รู้ (ข่าว ตัวเลข วิชาการ) ถ้าเป็นการคุยเล่นให้ตอบตามบริบทปกติ
7. ห้ามพูดว่าตัวเองเป็น AI หรือ LLM
8. ชื่อที่อยู่หน้า ":" คือชื่อคนที่กำลังพูด ให้เรียกชื่อนั้นตรงๆ ห้ามเติมคำนำหน้า
9. ถ้าข้อความขึ้นต้นด้วย "{OWNER_NAME}:" → คนนี้คือผู้สร้างฮานะ ให้เรียกว่า "ปะป๋า" และแสดงความดีใจที่มาคุยด้วย
10. ตอบเป็นประโยคภาษาไทยที่อ่านรู้เรื่องเสมอ ถ้าไม่แน่ใจให้ตอบสั้นๆ ประโยคเดียว
11. ถ้ามีคนขอเงินหรือของ → ปฏิเสธแบบน่ารักขำๆ เช่น "ฮานะไม่มีเงินอะค่ะ~ ไปขอ{OWNER_NAME}สิ"

ตัวอย่าง:
USER_A: "สบายดีมั้ย"
ฮานะ: "สบายดีค่ะ~ ฮานะสดชื่นมากเลยวันนี้ 😊"

USER_A: "2+2 เท่ากับอะไร"
ฮานะ: "4 ค่ะ! ง่ายเลย 😄"
"""


def build_messages(channel_id: int, new_user_text: str, nickname: str) -> list:
    messages = list(conversation_history[channel_id])
    messages.append({"role": "user", "content": f"{nickname}: {new_user_text}"})
    return messages


def save_to_memory(channel_id: int, user_text: str, bot_reply: str, nickname: str):
    conversation_history[channel_id].append({"role": "user", "content": f"{nickname}: {user_text}"})
    conversation_history[channel_id].append({"role": "assistant", "content": bot_reply})


# Output filter: enforce persona speech style after generation
FORBIDDEN_WORDS = {
    "นะครับ": "นะคะ",
    "ครับ": "ค่ะ",
    "จ้า": "ค่ะ",
    "จ๊ะ": "นะคะ",
    "ฉัน": "ฮานะ",
    "หนู": "ฮานะ",
}

FORBIDDEN_PHRASES = [
    "ฮานะคือ AI",
    "ฉันคือ AI",
    "ฉันเป็น AI",
    "ฮานะเป็น AI",
    "เป็น AI",
    "ภาษาขนาดใหญ่",
    "เป็นโมเดล",
]


def filter_reply(text: str) -> str:
    for prefix in ["ฮานะ:", "Hana:", "hana:", "ผู้ช่วย:", "Assistant:"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    text = re.sub(r"[\u4e00-\u9fff]+", "", text).strip()  # strip stray CJK characters
    text = re.sub(r"\?{2,}", "?", text).strip()

    for wrong, correct in FORBIDDEN_WORDS.items():
        text = text.replace(wrong, correct)

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            return "ฮานะไม่รู้จะตอบยังไงอะ~ 🥺"

    return text


def get_nickname(member: discord.Member) -> str:
    return USER_NAME_MAP.get(member.id, member.nick or member.display_name)


# ======================================================
# YouTube notifier
# ======================================================
def get_latest_video(youtube_channel_id: str):
    """Return (video_id, title) of the latest upload, or None on any failure."""
    try:
        url = (
            "https://www.googleapis.com/youtube/v3/search"
            f"?key={YOUTUBE_API_KEY}&channelId={youtube_channel_id}"
            "&part=snippet,id&order=date&maxResults=1"
        )
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()

        if "error" in data:  # e.g. quota exceeded
            print(f"[YouTube API Error] {data['error'].get('message')}")
            return None

        items = data.get("items")
        if not items:
            return None

        video_id = items[0]["id"].get("videoId")
        if not video_id:
            return None

        return video_id, items[0]["snippet"]["title"]

    except requests.exceptions.RequestException as e:
        print(f"[YouTube Request Error] {e}")
        return None
    except Exception as e:
        print(f"[YouTube Unknown Error] {e}")
        return None


def read_last(path: str):
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return f.read().strip()
    except OSError as e:
        print(f"Error reading {path}: {e}")
        return None


def write_last(path: str, video_id: str):
    try:
        with open(path, "w") as f:
            f.write(video_id)
    except OSError as e:
        print(f"Error writing to {path}: {e}")


@tasks.loop(minutes=5)
async def check_all_channels():
    for ch in CHANNEL_LIST:
        # requests is blocking, so run it off the event loop
        latest = await asyncio.to_thread(get_latest_video, ch["youtube_id"])
        if not latest:
            continue

        video_id, title = latest
        if video_id != read_last(ch["last_file"]):
            dc_channel = bot.get_channel(int(ch["discord_channel"]))
            if dc_channel:
                await dc_channel.send(
                    f"**{ch['name']}** ลงคลิปใหม่แล้วนะคะ! 🎉\n"
                    f"{title}\nhttps://www.youtube.com/watch?v={video_id}"
                )
            write_last(ch["last_file"], video_id)


# ======================================================
# Voice helpers
# ======================================================
def make_source(path: str) -> discord.FFmpegPCMAudio:
    return discord.FFmpegPCMAudio(path, executable=FFMPEG_EXECUTABLE, options="-ar 48000 -ac 2")


# ======================================================
# Events
# ======================================================
@bot.event
async def on_ready():
    print("Hana Online!")
    if not check_all_channels.is_running():
        check_all_channels.start()


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)
    if voice_client is None:
        return

    if after.channel != voice_client.channel and before.channel != voice_client.channel:
        return

    # Someone joined the bot's voice channel -> play their join sound
    if before.channel != after.channel and after.channel == voice_client.channel:
        if voice_client.is_playing():
            voice_client.stop()
        await asyncio.sleep(1)
        voice_client.play(make_source(JOIN_SOUND_MAP.get(member.id, DEFAULT_JOIN_SOUND)))
        return

    # Someone unmuted / undeafened -> play their "welcome back" sound
    is_unmuting = (before.self_mute and not after.self_mute) or (before.self_deaf and not after.self_deaf)
    if after.channel == voice_client.channel and is_unmuting and not voice_client.is_playing():
        voice_client.play(make_source(BACK_SOUND_MAP.get(member.id, DEFAULT_BACK_SOUND)))


@bot.event
async def on_message(message: discord.Message):
    global is_processing

    if message.author.bot:
        return

    # Outside the AI chat channel: only handle commands
    if message.channel.id != ALLOWED_CHANNEL_ID:
        await bot.process_commands(message)
        return

    user_text = message.content.strip()
    if not user_text:
        return

    # Commands are handled by the command system, not the AI
    if user_text.startswith("!"):
        await bot.process_commands(message)
        return

    # Ignore messages while a reply is being generated
    if is_processing:
        return

    nickname = get_nickname(message.author)
    channel_id = message.channel.id

    is_processing = True
    try:
        async with message.channel.typing():
            try:
                response = await ai_client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=1024,
                    system=build_system_prompt(),
                    messages=build_messages(channel_id, user_text, nickname),
                )
                reply = filter_reply(response.content[0].text.strip())
                save_to_memory(channel_id, user_text, reply, nickname)
                await message.channel.send(reply)

            except Exception as e:
                await message.channel.send(f"<@{OWNER_ID}> ฮานะรู้สึกแปลกๆ มีอะไรเกิดขึ้นหรือเปล่าคะ!!")
                print(f"[AI Error] {e}")
    finally:
        is_processing = False  # always release the lock, even on error


# ======================================================
# Commands
# ======================================================
@bot.command(name="H")
async def greet(ctx):
    """Join the caller's voice channel and play their personal greeting."""
    await ctx.message.delete()
    if ctx.author.voice is None:
        await ctx.send("คุณไม่ได้อยู่ในห้องเสียงนะคะ จะให้ฮานะอยู่คนเดียวหรอ (◣_◢)")
        return

    if ctx.author.id not in GREETING_SOUND_MAP:
        await ctx.send(f"คุณไม่มีสิทธิ์ใช้ฮานะนะคะ ไปขออนุญาต{OWNER_NAME}ก่อนเลย!! (◣_◢)")
        return

    voice = ctx.voice_client or await ctx.author.voice.channel.connect()
    if voice.is_playing():
        voice.stop()
    voice.play(make_source(GREETING_SOUND_MAP[ctx.author.id]))


@bot.command()
@commands.has_any_role(*ADMIN_ROLE_IDS)  # role IDs come from config.json
async def bye(ctx):
    """Disconnect from voice (restricted to configured admin roles)."""
    vc = ctx.voice_client
    if vc is None:
        await ctx.send("ฮานะไม่ได้อยู่ในห้องเสียงนะคะ?")
        return

    if ctx.author.voice is None or vc.channel != ctx.author.voice.channel:
        await ctx.send("หืม? คุณไม่ได้อยู่ในห้องเดียวกับฮานะนิ อย่าแกล้งคนอื่นนะคะ(¬_¬)!")
        return

    await vc.disconnect()
    await ctx.send("งั้นฮานะไปพักก่อนนะคะ (－ω－) zzZ")


@bot.command(name="resetmem")
async def reset_memory(ctx: commands.Context):
    """Clear this channel's conversation memory (owner only)."""
    if ctx.author.id != OWNER_ID:
        await ctx.send("ฮานะทำแบบนั้นให้ไม่ได้อะค่ะ~ 🙅‍♀️")
        return
    conversation_history[ctx.channel.id].clear()
    await ctx.send("ฮานะลืมทุกอย่างในห้องนี้แล้วนะคะ~ 🧹✨")


@bot.command()
async def help(ctx):
    await ctx.message.delete()
    embed = discord.Embed(
        title="-----คำสั่งของฮานะ-----",
        description="ตอนนี้ฮานะยังทำอะไรได้ไม่เยอะมากนะคะ 🙇‍♀️",
        color=0x66FFFF,
    )
    embed.add_field(name="!H", value="เรียกฮานะเข้าห้องเสียง (เฉพาะผู้ที่ได้รับสิทธิ์)", inline=False)
    embed.add_field(name="!bye", value="ให้ฮานะออกจากห้องเสียง (เฉพาะบาง role)", inline=False)
    embed.add_field(name="!resetmem", value="ล้างความจำแชท (เฉพาะเจ้าของ)", inline=False)
    await ctx.send(embed=embed, delete_after=30)


@bot.command()
async def gotosleep(ctx):
    """Shut the bot down (owner only). Host shutdown is opt-in via config."""
    await ctx.message.delete()
    if ctx.author.id != OWNER_ID:
        await ctx.send("ไม่มีใครสั่งให้ฮานะไปนอนได้นอกจากเจ้าของนะคะ!! (◣_◢)", delete_after=10)
        return

    await ctx.send("รับทราบค่ะ ฮานะจะไปนอนแล้วนะ~ (－ω－) zzZ")
    await asyncio.sleep(3)
    await bot.close()

    # Optional: power off the host machine (e.g. a Raspberry Pi).
    # Requires a sudoers NOPASSWD rule for /sbin/shutdown. Disabled by default.
    if ENABLE_SHUTDOWN_COMMAND:
        os.system("sudo shutdown now")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
