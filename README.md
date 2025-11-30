# InstaPi 📸

A simple, privacy-focused digital picture frame for Raspberry Pi. Display your Google Photos on any screen with zero data collection and no extra apps.

![InstaPi](app/static/instapi_logo_full.jpg)

## Features

- **QR Code Setup** - Scan to sign in, no keyboard needed
- **Google Photos Picker** - Select exactly which photos to display
- **Privacy First** - No data collection, no third-party apps, runs locally
- **Easy Updates** - Scan the corner QR anytime to add more photos
- **Open Source** - Customize it however you want

## Repository Structure

```
instapi/
├── app/                 # Flask application (runs on Pi)
│   ├── main.py
│   ├── routes/
│   ├── templates/
│   └── static/
├── website/             # Landing page (GitHub Pages)
│   └── index.html
├── pi-setup/            # Raspberry Pi setup scripts
│   ├── install.sh
│   ├── instapi.service
│   └── kiosk.sh
└── README.md
```

## Quick Start (Development)

```bash
git clone https://github.com/kellygold/instapi.git
cd instapi/app
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp secrets.json.template secrets.json  # Add your Google creds
python main.py
```

## Raspberry Pi Installation

```bash
curl -sSL https://raw.githubusercontent.com/kellygold/instapi/main/pi-setup/install.sh | bash
```

Or manually:
```bash
git clone https://github.com/kellygold/instapi.git
cd instapi
chmod +x pi-setup/*.sh
./pi-setup/install.sh
./pi-setup/kiosk.sh  # Optional: auto-start in fullscreen
```

## Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable the **Photos Picker API**
4. Create OAuth 2.0 credentials (Web application)
5. Add redirect URI: `https://your-domain/oauth2callback`
6. Download and save as `app/secrets.json`

## How It Works

1. **Frame shows QR code** → Scan with phone
2. **Sign in with Google** → Authorize photo access
3. **Pick your photos** → Using Google's native picker
4. **Photos display** → Slideshow starts automatically
5. **Add more anytime** → Small QR in corner of slideshow

## Tech Stack

- **Python 3** + Flask
- **Google Photos Picker API**
- **HTML/CSS/JS** (no frontend framework)
- **Tailwind CSS** (website only)

## License

MIT - Do whatever you want with it.

---

🌐 **Website:** [instapi.dev](https://instapi.dev)

Built with frustration at expensive frames that don't work. 🖼️
