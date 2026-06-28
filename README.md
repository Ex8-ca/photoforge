# photoforge — AI Image Generator

Self-hosted web UI for image generation, supporting two backends:

- **FLUX.1-dev** — Local GPU generation via ComfyUI (default)
- **MiniMax image-01** — Cloud generation via MiniMax API

## Architecture

- **Frontend:** Single `index.html` file (Tailwind CSS, Material Icons)
- **Backend:** Python reverse proxy (`server.py`) serving static files + proxying API requests
- **ComfyUI Backend:** Docker container on `127.0.0.1:8188`
- **Cloud History:** Cloudinary for persistent image history
- **MiniMax:** Cloud API for fast generation (~90s per image)

## Features

- 9 style presets (Photo Real, Cinematic, Portrait, Fashion, Product, etc.)
- 9 subject quick-picks
- Aspect ratio control + manual W/H
- FLUX: Seed, steps, CFG, sampler, scheduler, LoRA strength, batch size
- MiniMax: Simple prompt + aspect ratio (Cloudinary auto-upload)
- Cloud history strip with Cloudinary integration
- Server status with GPU/VRAM monitoring

## Setup

```bash
# 1. Copy and edit .env
cp .env.example .env
# Edit .env with your credentials

# 2. Install dependencies
pip install cloudinary

# 3. Run
python3 server.py 8888

# Or as systemd service
sudo -S -p '' cp photoforge.service /etc/systemd/system/
sudo -S -p '' systemctl enable --now photoforge
```

## .env Variables

```
CLOUDINARY_CLOUD_NAME=your_cloud
CLOUDINARY_API_KEY=your_key
CLOUDINARY_API_SECRET=your_secret
```

MiniMax key is auto-loaded from `~/.hermes/skills/minimax-speech/.env`.

## License

Private — Marc Smith
