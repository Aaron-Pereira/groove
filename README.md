# groove

**Self-hosted music library for macOS** — search for songs or albums, download them, tag them automatically, and browse everything in a simple web page on your Mac.

| | |
|---|---|
| **Download** | YouTube / YouTube Music search, direct links, playlists, full artist discographies |
| **Tag & organize** | Automatic metadata via [beets](https://beets.io/) — cover art, genres, track numbers |
| **Discover** | Billboard, UK Top 40, Last.fm charts; random album by year; chart suggestions you can approve |
| **Web UI** | Local dashboard at **http://localhost:8765** — queue, library, discoveries |
| **Runs in the background** | Optional auto-start on login |

**Stack:** Python 3.12 · FastAPI · beets · yt-dlp · Typer

---

## Who should read what

| You are… | Start here |
|----------|------------|
| **Using groove on your Mac** (download music, open the web UI, get updates) | [First-time setup](#for-users-first-time-setup-on-your-mac) below |
| **Changing the code** (Aaron / developers) | [For developers](#for-developers) at the bottom |

---

## For users: first-time setup on your Mac

**Time needed:** about 20–30 minutes the first time.  
**You do not need to know how to code.** You will copy and paste commands into the **Terminal** app.

### Two folders — keep this in mind

groove uses **two separate places** on your Mac:

| Folder | What it is | You touch it? |
|--------|------------|---------------|
| `~/groove` | The **app** (downloaded from GitHub) | **No** — only run the update commands below |
| Your **music data** folder (see step 6) | Your library, queue, settings, API keys | **No** — groove manages this; use the web UI instead |

Your music files, queue, and settings live in the **data** folder — not inside `~/groove`.

---

### Before you begin

You will need:

- A Mac running a recent version of macOS
- An internet connection
- About 30 minutes
- A **USB drive** (recommended, 64 GB or larger) **or** free space on your Mac's internal disk
- Two free website accounts for API keys (step 7) — AcoustID and Last.fm

---

### Step 1 — Open Terminal

1. Press **⌘ Space** (Command + Space)
2. Type **Terminal**
3. Press **Enter**

A window with a command prompt appears. You will paste commands here and press **Enter** after each one.

---

### Step 2 — Install Homebrew (if you don't have it)

Homebrew installs the tools groove needs. Paste this line and press Enter:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the on-screen instructions. When it finishes, it may ask you to run two extra lines — copy those from the Terminal output and run them.

**Check it worked:**

```bash
brew --version
```

You should see something like `Homebrew 4.x.x`.

---

### Step 3 — Install required tools

Paste this whole block and press Enter (it may take a few minutes):

```bash
brew install python@3.12 ffmpeg chromaprint deno git
```

| Tool | Why |
|------|-----|
| `python` | Runs groove |
| `ffmpeg` | Converts audio to MP3 |
| `chromaprint` | Helps identify songs by sound |
| `deno` | **Required** for YouTube downloads to work |
| `git` | Downloads and updates groove from GitHub |

**Check it worked:**

```bash
python3.12 --version
deno --version
ffmpeg -version | head -1
```

---

### Step 4 — Install `uv` (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then reload your shell (or close Terminal and open it again):

```bash
source ~/.zshrc
```

**Check it worked:**

```bash
uv --version
```

---

### Step 5 — Download groove from GitHub

```bash
git clone https://github.com/Aaron-Pereira/groove.git ~/groove
cd ~/groove
uv sync
```

This creates `~/groove` on your Mac and installs everything groove needs.  
**Do not edit any files inside `~/groove`.**

**Check it worked:**

```bash
cd ~/groove
uv run groove --help
```

You should see a list of groove commands.

---

### Step 6 — Choose where your music library lives

Pick **one** option:

#### Option A — USB drive (recommended)

1. Plug in a USB drive.
2. In **Disk Utility**, format it as **ExFAT** and name it **Music** (this erases the drive).
3. After plugging it in, it should appear at `/Volumes/Music`.

#### Option B — Mac internal storage

If you are not using a USB drive, groove can store everything in your home folder instead.  
Use `~/Music` as the location in the next step.

---

### Step 7 — Get free API keys

groove needs two free keys for tagging and chart data. Create them in your browser:

**AcoustID** (audio fingerprinting):
1. Go to https://acoustid.org/new-application
2. Register or log in (free)
3. Application name: `groove`, URL: `http://localhost`
4. Copy the **API key**

**Last.fm** (genres and charts):
1. Go to https://www.last.fm/api/account/create
2. Register or log in (free)
3. Application name: `groove`
4. Copy the **API key** and **Shared secret**

Keep these handy — you will paste them in the next step.

---

### Step 8 — Run the setup wizard

**If using a USB drive named Music:**

```bash
cd ~/groove
uv run groove init /Volumes/Music
```

**If using your Mac's internal storage:**

```bash
cd ~/groove
uv run groove init ~/Music
```

The wizard will:
- Create a `groove` folder for your library, queue, and settings
- Ask for your AcoustID and Last.fm keys (paste them when prompted; Enter to skip is OK but charts/tagging work better with keys)
- Run a health check

When it finishes you should see **Setup complete!**

---

### Step 9 — Run the health check

```bash
cd ~/groove
uv run groove doctor
```

Look for green checkmarks. Important ones:

- **yt-dlp** — should show version `2026.8.19` or newer
- **ytdlp_js_runtime** — should say `deno found`
- **ffmpeg** — should be OK
- **free_space** — should show enough free disk space

If anything is red, read the message next to it. Common fix for download problems:

```bash
brew install deno
```

Then run `uv run groove doctor` again.

---

### Step 10 — Start groove and open the web page

**Easiest way (good for first try):**

```bash
cd ~/groove
uv run groove serve
```

Leave this Terminal window open while you use groove.  
Open your browser and go to: **http://localhost:8765**

You should see the groove home page with an empty queue.

To stop groove: click the Terminal window and press **Ctrl + C**.

---

### Step 11 — Auto-start on login (optional)

If you used a **USB drive at `/Volumes/Music`**, you can have groove start automatically whenever you log in and the drive is plugged in:

```bash
cd ~/groove
uv run groove install-agents
```

After this, groove runs in the background — you do not need to keep a Terminal window open.  
Open **http://localhost:8765** anytime.

To check it is running:

```bash
launchctl list | grep groove
```

> **Note:** Auto-start background logs are written to `/Volumes/Music/groove/logs/`.  
> If you chose **Option B** (internal storage at `~/Music/groove`), use **Step 10** (manual `groove serve`) instead of `install-agents`, or ask Aaron to help configure auto-start for your setup.

---

### You are done!

Try downloading something:

1. Open **http://localhost:8765**
2. Type `Sabrina Carpenter - Espresso` in the search box
3. Press Enter
4. Watch the status change: `pending` → `downloading` → `done`
5. Go to **Library** to see your song

---

## For users: getting updates

Aaron updates groove on GitHub. When he tells you an update is ready, run these three commands in Terminal:

```bash
cd ~/groove
git pull
uv sync
```

Then restart groove:

**If you use `groove serve` in a Terminal window:**
- Press **Ctrl + C** in that window
- Run `uv run groove serve` again

**If you use auto-start (`install-agents`):**

```bash
launchctl kickstart -k gui/$(id -u)/com.groove.server
```

Or simply **log out and back in** (with your USB drive plugged in, if you use one).

That is all you need. **Do not edit files in `~/groove`.** Just pull updates.

---

## For users: everyday use

### Download a song

1. Open **http://localhost:8765**
2. Type `Artist - Song Title`
3. Press Enter

Example: `Arctic Monkeys - Do I Wanna Know?`

### Download an album

1. Same page, type `Artist - Album Name`
2. Change the **Kind** dropdown to **Album**
3. Press Enter

Example: `Arctic Monkeys - AM`

### Download from a YouTube link

Paste the full URL into the search box:

```
https://www.youtube.com/watch?v=xxxxxxxx
```

### Browse your library

Click **Library** in the top menu, or open your music folder in Finder:

- USB drive: `/Volumes/Music/groove/library/`
- Internal storage: `~/Music/groove/library/`

Folders look like: `Artist Name/Album Name (Year)/01 - Track Name.mp3`

### Discover new music

Click **Discoveries** in the top menu. Chart hits from Billboard, UK Top 40, and Last.fm appear here.

By default, tracks that trend on multiple charts are **proposed** for download — they appear under **Awaiting your approval**. Click **Accept** to queue them, or **Dismiss** to hide them.

Try **Random Album** to roll a random album from a year you pick.

### Import a Spotify library

1. Export your liked songs from https://exportify.net (free)
2. Go to **http://localhost:8765/bulk**
3. Upload the `.csv` file
4. Review the preview and click **Confirm**

### Queue many albums at once

Go to **Bulk Add** → **Artist discography**, type an artist name, and pick albums.

---

## For users: what you should NOT change

| Do not edit… | Why |
|--------------|-----|
| Anything inside `~/groove` | That is the app — Aaron updates it via GitHub |
| `groove.toml` or `beets.yaml` in your data folder | Settings files — ask Aaron if something needs changing |
| Files inside `library/` by hand | beets manages names and tags; use the web UI or ask Aaron |
| `db/musiclib.db` | beets' internal database |

**Safe to use:** the web UI at http://localhost:8765, and the Terminal commands in this README.

---

## For users: simple troubleshooting

### Downloads keep failing (HTTP 403 / Forbidden)

```bash
cd ~/groove
git pull
uv sync
brew install deno
uv run groove doctor
```

Make sure **ytdlp_js_runtime** shows `deno found`, then restart groove.

### The web page won't open

Make sure groove is running:

```bash
cd ~/groove
uv run groove serve
```

Then open **http://localhost:8765** (not https).

### USB drive was unplugged

Plug the drive back in. If you use auto-start, wait a few seconds and refresh the browser. Downloads resume automatically.

### Something is stuck in the queue

On the queue page, click **Retry**. If it still fails after 3 tries, try pasting a specific YouTube URL instead of a search.

### Check free disk space

```bash
cd ~/groove
uv run groove doctor
```

### Still stuck?

Send Aaron:
- A screenshot of the queue page
- The last few lines from the log file:
  - USB: `/Volumes/Music/groove/logs/server.log`
  - Internal: `~/Music/groove/logs/server.log`

---

## Table of contents (detailed reference)

1. [One-time setup (technical)](#1-one-time-setup-technical-reference)
2. [Day-to-day usage (CLI)](#2-day-to-day-usage)
3. [Spotify migration](#3-spotify-migration)
4. [Discovery & automation](#4-discovery--automation)
5. [Troubleshooting (advanced)](#5-troubleshooting)
6. [Under the hood](#6-under-the-hood)
7. [Moving to a new computer](#7-moving-to-a-new-computer)
8. [For developers](#for-developers)

---

## 1. One-time setup (technical reference)

This section mirrors the user guide above with more detail for anyone comfortable with the command line.

### 1.1 Prerequisites

```bash
brew install python@3.12 ffmpeg chromaprint deno git
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc
```

Verify:
```bash
python3.12 --version
deno --version
ffmpeg -version
uv --version
```

### 1.2 Prepare your drive

```bash
diskutil info /Volumes/Music | grep "File System"
```

- **exFAT** – ideal
- **FAT32** – works (4 GB file size limit)
- **NTFS** – reformat to exFAT in Disk Utility

### 1.3 API keys

See [Step 7](#step-7--get-free-api-keys) in the user guide.

### 1.4 Clone and install

```bash
git clone https://github.com/Aaron-Pereira/groove.git ~/groove
cd ~/groove
uv sync
```

### 1.5 Run `groove init`

```bash
# External USB drive named "Music":
uv run groove init /Volumes/Music

# Or internal storage:
uv run groove init ~/Music
```

### 1.6 Run `groove doctor`

```bash
uv run groove doctor
```

### 1.7 Install scheduled jobs (optional)

```bash
uv run groove install-agents
```

After this:
- `groove serve` starts automatically on login (when the drive is available)
- Chart scraping runs daily at 07:00
- New-release checks run weekly on Monday at 07:05
- Metadata refresh runs weekly on Monday at 03:00

### 1.8 Open the web UI

```bash
uv run groove serve
```

Open http://localhost:8765

---

## 2. Day-to-day usage

### "I want one song"

Open http://localhost:8765, type `Artist - Song Title`, hit Enter.

### "I want one album"

Same form, set **Kind** to **Album**, or:

```bash
uv run groove request --kind album "Arctic Monkeys - AM"
```

### "I want to download from a specific YouTube video"

Paste the URL directly into the request form.

### "I want to queue a YouTube playlist"

Bulk Add → YouTube playlist, or:

```bash
uv run groove request --youtube-playlist "https://www.youtube.com/playlist?list=PLxxxxxxxx"
```

### "I want all of an artist's albums"

Go to `/bulk` → **Artist discography**, or:

```bash
uv run groove request-discography "Arctic Monkeys"
```

### "I ripped a CD"

1. Drop the ripped folder into `inbox/cds/` inside your data folder.
2. Run `uv run groove import-cds`
3. Follow beets' interactive prompts in Terminal.

### "I want to browse what I have"

http://localhost:8765/library or Finder → your `library/` folder.

---

## 3. Spotify migration

1. Export from https://exportify.net
2. Upload at http://localhost:8765/bulk
3. Review preview → **Confirm**
4. Watch progress on the queue page (runs in background)

---

## 4. Discovery & automation

### What gets scraped

| Source | Schedule | Count |
|--------|----------|-------|
| Billboard Hot 100 | Daily 07:00 | 100 tracks |
| Billboard 200 (albums) | Daily 07:00 | 200 albums |
| UK Official Top 40 | Daily 07:00 | 40 tracks |
| Last.fm global top | Daily 07:00 | 100 tracks |
| Last.fm genre charts | Daily 07:00 | 50 per genre |
| MusicBrainz new releases | Weekly Monday 07:05 | per watchlist artist |

Results appear on **Discoveries**. Use **+ Queue** or **Dismiss**.

### Auto-queue and approval

Tracks on **2 or more** charts in the same week are flagged. By default they require your approval before downloading (`require_approval = true` in `groove.toml`).

```toml
[auto_queue]
min_chart_appearances = 2
require_approval = true   # false = download without asking
```

### Random album by year

http://localhost:8765/random-album — picks from Billboard year-end Top 200 (MusicBrainz fallback for older years).

### Artist watchlist

Add artists at `/watchlist`. Enable **Auto-download new albums** for automatic queuing.

### Customising genre charts

```toml
[discovery]
genres = ["rock", "hip-hop", "electronic", "jazz", "folk"]
billboard_200 = true
```

Restart the server after editing `groove.toml`.

---

## 5. Troubleshooting

### Download failed

1. Click **Retry** in the queue UI (up to 3 attempts).
2. Paste a specific YouTube URL.
3. Run `uv run groove doctor` — check yt-dlp version and deno.

### beets picked the wrong album

```bash
uv run beet --config /path/to/your/groove/beets.yaml import -L "album:Name Of Album"
```

### Album has wrong track numbers

```bash
uv run beet --config /path/to/your/groove/beets.yaml import -C -I \
  "/path/to/your/groove/library/Artist Name/Album Name"
```

Or: `uv run groove metadata retag-albums`

### Wrong tags on one track

Edit in the web UI (`/library` → Edit), or:

```bash
uv run beet --config /path/to/your/groove/beets.yaml modify \
  "artist:Old Artist" artist="Correct Artist" album="Correct Album"
```

### Tags edited outside groove (Kid3, Picard)

```bash
uv run groove metadata rescan
```

### Drive unplugged mid-download

Worker pauses when the data folder is unreachable. Replug; resumes within ~5 seconds.

### Storage full

```bash
uv run groove doctor
```

### Server won't start

```bash
tail -50 /path/to/your/groove/logs/server.log
launchctl list | grep groove
uv run groove serve
```

---

## 6. Under the hood

### File layout (external drive example)

```
/Volumes/Music/
└── groove/
    ├── library/              ← your music (beets-managed)
    ├── inbox/
    │   ├── cds/
    │   ├── downloads/        ← temporary staging (auto-cleaned)
    │   └── review/
    ├── state/                ← queue, discoveries, watchlist
    ├── db/musiclib.db        ← beets database
    ├── logs/
    ├── groove.toml           ← settings + API keys
    └── beets.yaml
```

Internal storage is the same structure under `~/Music/groove/`.

### Config

All settings in `groove.toml` inside your **data** folder (not in `~/groove`).

### Audio quality

YouTube streams are ~128 kbps Opus. groove transcodes to **MP3 192 kbps**.

```toml
[audio]
bitrate = "320"
```

### Migrating to a new drive

```bash
rsync -av --progress /Volumes/Music/groove/ /Volumes/NewDrive/groove/
```

Update `hdd_root` in `groove.toml` and paths in `beets.yaml`, then `uv run groove metadata rescan`.

### State backup

```bash
uv run groove doctor --backup
```

Nightly rsync to `~/groove-state-backup/` when agents are installed.

---

## 7. Moving to a new computer

The **app** is on GitHub (`~/groove`). Your **music and settings** are in your data folder — copy both.

### On the old Mac

```bash
cd ~/groove && git push origin main   # developers only
rsync -avh ~/Music/groove/ /Volumes/Backup/groove-data/   # or /Volumes/Music/groove/
```

### On the new Mac

Follow [First-time setup](#for-users-first-time-setup-on-your-mac) steps 1–5, then restore your data folder instead of running a fresh `init`:

```bash
rsync -avh /Volumes/Backup/groove-data/ ~/Music/groove/
# Update hdd_root in groove.toml if the path changed
uv run groove doctor
uv run groove serve
```

---

## For developers

### Quick start (dev)

```bash
brew install python@3.12 ffmpeg chromaprint deno
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/Aaron-Pereira/groove.git ~/groove
cd ~/groove
uv sync
uv run groove init /Volumes/Music    # or ~/Music
uv run groove serve
```

### Making changes

```bash
cd ~/groove
# edit code in src/groove/
uv run pytest tests/
git add -A && git commit -m "Describe your change"
git push origin main
```

Users pull with `git pull && uv sync` — see [Getting updates](#for-users-getting-updates).

### Key dependencies

- **yt-dlp** `>=2026.8.19` with `[default]` extras (includes yt-dlp-ejs)
- **deno** on PATH for YouTube JS challenges
- **beets** for import and tagging

### Beets plugins

| Plugin | Purpose |
|--------|---------|
| `chroma` | AcoustID fingerprinting |
| `fetchart` / `embedart` | Cover art |
| `lastgenre` | Genre from Last.fm |
| `replaygain` | Loudness normalization |
| `lyrics` | Lyrics sidecars |
| `scrub` | Clean junk tags |
| `duplicates` | Duplicate detection |
| `mbsync` | Refresh MusicBrainz data |

### Scheduled jobs

```bash
launchctl list | grep groove
launchctl start com.groove.charts
launchctl unload ~/Library/LaunchAgents/com.groove.server.plist
```
