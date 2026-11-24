# AI Auto Video Editor Bot — Phase 1

This is a separate video-editor service added alongside the existing YouTube/Telegram bulk-bot project. Existing bot files and dependencies are preserved.

## Phase 1 features
- Telegram video and video-document intake
- Temporary per-job working directory
- FFprobe input validation
- FFmpeg MP4 H.264/AAC processing
- Preserve aspect ratio and downscale only when a dimension exceeds 1920px
- Subtle video fade-in/fade-out
- Audio loudness normalization when audio exists
- Return processed video to Telegram

**Scope note:** Phase 1 is an automatic FFmpeg enhancement MVP, not yet semantic AI editing. Scene understanding, smart highlight selection, Whisper subtitles, music selection, motion graphics and generated effects are planned for later phases. Phase 1 does not require a paid AI API.

## VPS requirements
- Ubuntu 22.04 or 24.04
- Docker Engine and Docker Compose plugin
- Telegram bot token from @BotFather
- Recommended starting point: 2 vCPU, 4 GB RAM, 30 GB SSD for short clips. CPU-heavy rendering may need more resources.

## Deploy

1. Clone the repository:
   git clone https://github.com/sandeepshekhawat325-create/youtube-telegram-bulk-bot.git
   cd youtube-telegram-bulk-bot
   git checkout feature/ai-auto-video-editor-phase1

2. Install Docker Engine and the Compose plugin using the official Ubuntu instructions:
   https://docs.docker.com/engine/install/ubuntu/

3. Create your private environment file:
   cp .env.example .env
   nano .env

4. Paste the token created with @BotFather into BOT_TOKEN. Do not commit or share .env.

5. Build and start the editor:
   docker compose up -d --build

6. View logs:
   docker compose logs -f ai-video-editor

7. Stop:
   docker compose down

## Test
- Open your Telegram bot and send /start.
- Send a short MP4 video below the configured download limit.
- Wait for the processed MP4 response.

## Current limits
- The hosted Telegram Bot API download limit is 20 MB; this MVP defaults to 19 MB.
- The output limit defaults to 49 MB.
- The MVP handles one update at a time and uses local CPU rendering.
- Temporary job files are removed after processing.
- Larger files require a local Telegram Bot API server and a separate large-file handling update.

## Roadmap
- Phase 2: queue, concurrent jobs, progress updates, retries and per-user limits
- Phase 3: scene detection and semantic video analysis
- Phase 4: Whisper captions and subtitle styling
- Phase 5: smart cuts/highlights, music ducking, intro/outro and branding templates
- Phase 6: large-file support, resource profiles, monitoring and production hardening

## Security
- Keep .env private.
- Restrict VPS SSH access and keep OS/Docker packages updated.
- Add access control and rate limiting before opening the bot to a large public audience.
