# groove

**Self-hosted music library for macOS** — search for songs or albums, download them, tag them automatically, and browse everything in a simple web page on your Mac.

| | |
|---|---|
| **Download** | YouTube / YouTube Music search, direct links, playlists, full artist discographies |
| **Tag & organize** | Automatic metadata via [beets](https://beets.io/) — cover art, genres, track numbers |
| **Discover** | Billboard, UK Top 40, Last.fm charts; random album by year; chart suggestions you can approve |
| **Web UI** | Local dashboard at **http://localhost:8765** — queue, library, discoveries |
| **How you run it** | Start with one Terminal command when you want to use it (`groove serve`) |

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

### Read this first — three separate steps

Setting up groove is **not** just cloning from GitHub. You need **three** things:

| Step | What | Command | How often |
|------|------|---------|-----------|
| **1. Install the app** | Download groove from GitHub | `git clone` + `uv sync` (Steps 1–5) | Once |
| **2. Create your library** | Build folders, database, and settings on your Mac | `uv run groove init ~/Music` (Step 7) | **Once — do not skip** |
| **3. Run groove** | Open the web page and download music | `uv run groove serve` (Step 9) | Every time you use it |

**Cloning GitHub only does step 1.** It does **not** create your music library, database, or config. If you run `groove serve` without running `groove init` first, the web page may open but downloads will not work properly and your library will be empty.

Step 7 (`groove init`) is the step that **initialises your personal groove library** on your Mac.

---

### Two folders — keep this in mind

This guide sets up groove to store everything on your **Mac's internal disk**. groove uses two separate folders:

| Folder | What it is | You touch it? |
|--------|------------|---------------|
| `~/groove` | The **app** (downloaded from GitHub) | **No** — only run the update commands below |
| `~/Music/groove` | Your **personal library** — music files, database, queue, settings, API keys | **No** — created by `groove init`; groove manages it via the web UI |

Your MP3 files live in `~/Music/groove/library/`. The app code in `~/groove` stays separate. **Until you run `groove init`, the `~/Music/groove` folder does not exist.**

**How you start groove:** each time you want to download music or open the web page, you run one command in Terminal (`uv run groove serve`) and leave that window open. When you are done, press **Ctrl + C** to stop it. There is no background auto-start in this setup.

---

### Before you begin

You will need:

- A Mac running a recent version of macOS
- An internet connection
- About 30 minutes
- **Free disk space on your Mac** — at least 20 GB to start (more as your library grows; music is stored in `~/Music/groove/`)
- Two free website accounts for API keys (step 6) — AcoustID and Last.fm

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

> **Stop — you are not finished yet.**  
> The app is installed, but **your library does not exist yet**. You still need **Step 7** (`groove init ~/Music`) before you can download music. Do not skip to `groove serve` until Step 7 is complete.

**Optional check** — this should fail or show nothing useful until after Step 7:

```bash
ls ~/Music/groove
```

If you see `No such file or directory`, that is normal — Step 7 creates it.

---

### Step 6 — Get free API keys

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

### Step 7 — Create your library (`groove init`) — required

This is the most important one-time step. It **initialises your personal groove library** on your Mac — it does not come from GitHub.

Run:

```bash
cd ~/groove
uv run groove init ~/Music
```

**What this creates** (all inside `~/Music/groove/`):

| Created | Purpose |
|---------|---------|
| `library/` | Where your downloaded MP3s are stored |
| `db/musiclib.db` | The **library database** (beets index of everything you own) |
| `state/` | Download queue, chart discoveries, watchlist |
| `inbox/` | Staging area for downloads and CD rips |
| `logs/` | Error logs if something goes wrong |
| `groove.toml` | Your settings and API keys |
| `beets.yaml` | Tagging and import rules |

The wizard will:
- Create all of the above
- Ask for your AcoustID and Last.fm keys (paste them when prompted; Enter to skip is OK but charts/tagging work better with keys)
- Run a health check

When it finishes you should see **Setup complete!**

**Verify your library was created:**

```bash
ls ~/Music/groove
```

You should see folders like `library`, `db`, `state`, and files `groove.toml` and `beets.yaml`.

You can open the music folder in Finder anytime: **Go → Home → Music → groove**.

> **Only run `groove init` once.** Running it again on the same Mac is usually unnecessary. If you already ran it and see `Setup complete!`, skip to Step 8.

---

### Step 8 — Run the health check

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

### Step 9 — Start groove (do this every time you use it)

**Only do this after Step 7 (`groove init`) has finished successfully.**

groove does **not** run in the background automatically with this setup. Each time you want to download music or open the web page:

1. Open **Terminal**
2. Run:

```bash
cd ~/groove
uv run groove serve
```

3. Leave this Terminal window **open** the whole time you are using groove
4. Open your browser and go to: **http://localhost:8765**

You should see the groove home page. Downloads and the library only work while this command is running.

**To stop groove** when you are finished: click the Terminal window and press **Ctrl + C**.  
You can close the browser tab anytime.

> **Tip:** Bookmark http://localhost:8765 in your browser. You still need to run `uv run groove serve` in Terminal first — the bookmark only works while that window is open.

---

### You are done!

Try downloading something:

1. Open **http://localhost:8765**
2. Type `Sabrina Carpenter - Espresso` in the search box
3. Press Enter
4. Watch the status change: `pending` → `downloading` → `done`
5. Go to **Library** to see your song

**Next time you want to use groove**, repeat [Step 9](#step-9--start-groove-do-this-every-time-you-use-it) — open Terminal, run `uv run groove serve`, then open http://localhost:8765.

---

## For users: starting and stopping groove

This is the routine you will use **every day** (after the one-time setup above).

### Start

```bash
cd ~/groove
uv run groove serve
```

Wait until you see a line like `Uvicorn running on http://127.0.0.1:8765`, then open **http://localhost:8765** in your browser.

**Keep the Terminal window open.** If you close it or press Ctrl + C, groove stops and the web page will not load.

### Stop

In the Terminal window where `groove serve` is running, press **Ctrl + C**.

### Your music is always on disk

Stopping groove does **not** delete your music. Your files stay in `~/Music/groove/library/` whether or not groove is running. You only need to start groove when you want to download something new or use the web UI.

### Optional: USB drive instead of internal storage

If you later move to an external USB drive named **Music**, run `uv run groove init /Volumes/Music` on a fresh setup (or ask Aaron to help migrate). USB setups can use `uv run groove install-agents` for auto-start on login. **This guide does not use that** — stick with `~/Music/groove` and manual `groove serve` unless Aaron changes your setup.

---

## For users: getting updates

Aaron updates groove on GitHub. When he tells you an update is ready, run these three commands in Terminal:

```bash
cd ~/groove
git pull
uv sync
```

Then restart groove (if it was running):

1. In the Terminal window where `groove serve` is running, press **Ctrl + C**
2. Start it again:

```bash
cd ~/groove
uv run groove serve
```

That is all you need. **Do not edit files in `~/groove`.** Just pull updates.

---

## For users: everyday use

**First:** start groove in Terminal (`cd ~/groove && uv run groove serve`). See [Starting and stopping groove](#for-users-starting-and-stopping-groove).

### Download a song

1. Open **http://localhost:8765** (with `groove serve` running)
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

Click **Library** in the top menu (with groove running), or open your music in Finder:

**Finder:** Home → **Music** → **groove** → **library**

Full path: `~/Music/groove/library/`  
Folders look like: `Artist Name/Album Name (Year)/01 - Track Name.mp3`

You can browse MP3s in Finder even when groove is not running. The web **Library** page needs `groove serve` to be running.

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
| `~/Music/groove/groove.toml` or `beets.yaml` | Settings files — ask Aaron if something needs changing |
| Files inside `~/Music/groove/library/` by hand | beets manages names and tags; use the web UI or ask Aaron |
| `db/musiclib.db` | beets' internal database |

**Safe to use:** the web UI at http://localhost:8765, and the Terminal commands in this README.

---

## For users: simple troubleshooting

### I cloned from GitHub but nothing works / library is empty

You probably skipped **Step 7**. Cloning only installs the app — it does **not** create your library or database.

Run this once:

```bash
cd ~/groove
uv run groove init ~/Music
```

Wait for **Setup complete!**, then:

```bash
uv run groove serve
```

Check the library folder exists:

```bash
ls ~/Music/groove/library
ls ~/Music/groove/db/musiclib.db
```

### `groove doctor` says "hard drive not found" or "Drive root not found"

This usually means **your library was never created** (`groove init` not run yet), or groove is looking in the wrong place.

**Fix 1 — create the library (most common):**

```bash
cd ~/groove
uv run groove init ~/Music
uv run groove doctor
```

**Fix 2 — library exists but doctor still fails:**

Point doctor at your config file explicitly:

```bash
uv run groove doctor --config ~/Music/groove/groove.toml
```

**Check which path groove is using:**

```bash
ls ~/Music/groove/groove.toml
```

If that file exists, `groove doctor` should find it automatically (after you `git pull` the latest app update). If not, use `--config` as above.

The error mentions `/Volumes/Music/groove` when no config file was found — that is the old default for USB drives, not your Mac's internal library at `~/Music/groove`.

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

groove is probably not running. Start it:

```bash
cd ~/groove
uv run groove serve
```

Wait for `Uvicorn running on http://127.0.0.1:8765`, then open **http://localhost:8765** (not https). The Terminal window must stay open.

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
- The last few lines from: `~/Music/groove/logs/server.log`  
  (In Terminal: `tail -30 ~/Music/groove/logs/server.log`)

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

### 1.2 Storage layout (default: internal disk)

Data root: `~/Music/groove/` (created by `groove init ~/Music`).

Ensure enough free space: `df -h ~`

**Optional — external USB drive:** format as **ExFAT**, name it **Music**, then `uv run groove init /Volumes/Music`. See [Optional: USB drive](#optional-usb-drive-instead-of-internal-storage) in the user guide.

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
uv run groove init ~/Music
```

Creates `~/Music/groove/`. Users start the app manually with `uv run groove serve` each session.

### 1.6 Run `groove doctor`

```bash
uv run groove doctor
```

### 1.7 Start the web UI (each session)

```bash
uv run groove serve
```

Open http://localhost:8765. Press Ctrl + C to stop.

**Optional — `install-agents`:** only for USB setups at `/Volumes/Music/groove` (auto-start + daily chart scrape). Not used for internal `~/Music/groove` + manual serve.

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

### File layout (internal storage — default)

```
~/Music/groove/
├── library/              ← your music (beets-managed)
├── inbox/
│   ├── cds/
│   ├── downloads/        ← temporary staging (auto-cleaned)
│   └── review/
├── state/                ← queue, discoveries, watchlist
├── db/musiclib.db        ← beets database
├── logs/                 ← server.log (useful for troubleshooting)
├── groove.toml           ← settings + API keys
└── beets.yaml
```

USB setups use the same layout under `/Volumes/Music/groove/`.

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
uv run groove init ~/Music
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
