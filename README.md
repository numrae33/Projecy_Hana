# Hana — Discord AI Chat Bot

A Discord bot with a Thai-speaking character persona ("Hana"), built with Python, `discord.py` and the Anthropic API. Built as a personal project for my friends' Discord server and kept running on a Raspberry Pi.

> This repository is a sanitized portfolio copy. All real user IDs, channel IDs, role IDs and audio files have been removed. See `config.example.json` for the expected structure.

## Features

- **AI character chat** — replies in a consistent persona using Claude (Haiku) through the Anthropic API, with a system prompt that defines tone, pronouns and rules.
- **Per-channel memory** — keeps the last 10 messages per channel as conversation context (`collections.deque`), resettable by the owner with `!resetmem`.
- **Output filter** — post-processes model replies to enforce the persona's speech style and block out-of-character phrases.
- **Voice channel greetings** — plays personalized sounds through FFmpeg when members join or unmute; `!H` summons the bot into voice.
- **YouTube upload notifier** — polls the YouTube Data API every 5 minutes (non-blocking via `asyncio.to_thread`) and announces new videos in a chosen channel.
- **Access control** — owner-only commands, role-restricted commands, and a busy lock so only one AI reply is generated at a time.
- **Safe config** — secrets in environment variables, personal IDs in a git-ignored `config.json`.

## Tech stack

Python 3 · discord.py · Anthropic SDK · FFmpeg · YouTube Data API v3 · python-dotenv

## Setup

```bash
pip install discord.py anthropic requests python-dotenv
cp .env.example .env              # fill in your keys
cp config.example.json config.json  # fill in your server's IDs
python hana_bot.py
```

Requires FFmpeg installed and available on PATH (or set `ffmpeg_executable` in `config.json`).

## Project structure

```
hana_bot.py          # bot source
config.example.json  # config template (fake IDs)
.env.example         # secret keys template
```

## What I learned

- Working with async Python and Discord's event model
- Prompt design and post-processing for a consistent character
- Calling external APIs safely (timeouts, error handling, quota failures)
- Keeping secrets and personal data out of source control

---

# ฮานะ — บอท Discord คุยด้วย AI (ภาษาไทย)

โปรเจกต์ส่วนตัวที่ทำให้เซิร์ฟเวอร์เพื่อน เป็นบอทคาแรกเตอร์ "ฮานะ" เด็กสาวร่าเริง ใช้ Python + discord.py + Anthropic API รันบน Raspberry Pi

ฟีเจอร์หลัก: แชทกับ AI ตามบุคลิกที่กำหนด, จำบทสนทนาแยกตามห้อง, เล่นเสียงทักทายในห้องเสียง, แจ้งเตือนคลิป YouTube ใหม่, และระบบจำกัดสิทธิ์คำสั่ง

*โค้ดชุดนี้ลบ ID และข้อมูลส่วนตัวของเพื่อนๆ ออกทั้งหมดแล้ว*
