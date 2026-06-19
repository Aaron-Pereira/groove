# groove

**Self-hosted music library manager for macOS** — download, tag, organize, and discover music on your own drive.

| | |
|---|---|
| **Download** | YouTube search, direct URLs, playlists, full artist discographies |
| **Tag & organize** | MusicBrainz metadata via [beets](https://beets.io/) — cover art, genres, ReplayGain, lyrics |
| **Discover** | Billboard Hot 100, UK Top 40, Last.fm charts; auto-queue tracks trending across charts |
| **Migrate** | Import your Spotify library from an Exportify CSV in one upload |
| **Web UI** | Local dashboard at `http://localhost:8765` — queue, library browser, watchlist |
| **Automation** | Background worker + optional launchd jobs for scraping and metadata refresh |

**Stack:** Python 3.12 · FastAPI · beets · yt-dlp · Typer · Rich

Your external drive is the single source of truth. Everything lives in
`/Volumes/Music/groove/` so no pre-existing files on the drive are touched.

### Quick start

```bash
brew install python@3.12 ffmpeg chromaprint
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/Aaron-Pereira/groove.git ~/groove
cd ~/groove
uv sync
uv run groove init /Volumes/Music    # creates layout, prompts for free API keys
uv run groove serve                  # open http://localhost:8765
```

Full setup (drive formatting, API keys, scheduled jobs) is in [One-time setup](#1-one-time-setup) below.

---

## Table of contents

1. [One-time setup](#1-one-time-setup)
2. [Day-to-day usage](#2-day-to-day-usage)
3. [Spotify migration](#3-spotify-migration)
4. [Discovery & automation](#4-discovery--automation)
5. [Troubleshooting](#5-troubleshooting)
6. [Under the hood](#6-under-the-hood)

---

## 1. One-time setup

**Total time: 10–15 minutes.**

### 1.1 Prerequisites

```bash
# macOS (Apple Silicon or Intel)
brew install python@3.12 ffmpeg chromaprint

# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc  # or open a new terminal
```

Verify:
```bash
python3.12 --version   # Python 3.12.x
ffmpeg -version        # ffmpeg version ...
uv --version           # uv 0.x.x
```

### 1.2 Prepare your drive

Plug in your USB stick or HDD. Check its filesystem:

```bash
diskutil info /Volumes/Music | grep "File System"
```

- **exFAT** – ideal. Nothing to do.
- **FAT32** – works. File size limit is 4 GB (fine for MP3s).
- **NTFS** – **needs reformatting**. macOS can only read NTFS without
  third-party drivers.

To reformat to exFAT (this erases the drive):
1. Open **Disk Utility** (`⌘ Space` → Disk Utility).
2. Select the drive (not a partition) in the left panel.
3. Click **Erase** → Format: **ExFAT** → Name: **Music** → Erase.

Or via CLI:
```bash
diskutil eraseDisk ExFAT Music /dev/diskN   # replace N with your disk number
# Find your disk number with: diskutil list
```

### 1.3 Get API keys (free)

**AcoustID** – for audio fingerprint matching (lets beets identify even
poorly-tagged files):
1. Go to https://acoustid.org/new-application
2. Log in or register (free).
3. Fill in Application name: `groove`, URL: `http://localhost`.
4. Copy the API key.

**Last.fm** – for genre tagging and chart data:
1. Go to https://www.last.fm/api/account/create
2. Log in or register (free).
3. Fill in Application name: `groove`.
4. Copy both the **API key** and the **Shared secret**.

You'll paste these into `groove init` in the next step.

### 1.4 Clone and install groove

```bash
git clone https://github.com/YOUR_USERNAME/groove.git ~/groove
cd ~/groove
uv sync
```

Verify groove is available:
```bash
uv run groove --help
```

### 1.5 Run `groove init`

```bash
uv run groove init /Volumes/Music
```

This will:
- Create `library/`, `inbox/`, `state/`, `db/`, `logs/` inside `/Volumes/Music/groove/`.
- Prompt for your AcoustID and Last.fm API keys.
- Write `groove.toml` (your config file) to `/Volumes/Music/groove/groove.toml`.
- Run `groove doctor` to verify everything works.

### 1.6 Run `groove doctor`

```bash
uv run groove doctor
```

You should see all green checkmarks. If anything is yellow/red, follow the
instructions printed next to each check.

### 1.7 Install the scheduled jobs (optional but recommended)

```bash
uv run groove install-agents
```

This copies the launchd plist files to `~/Library/LaunchAgents/` and loads
them. After this:
- `groove serve` starts automatically on login and stays running.
- Chart scraping runs daily at 07:00.
- New-release checks run weekly on Monday at 07:05.
- Metadata refresh runs weekly on Monday at 03:00.

### 1.8 Open the web UI

Open `http://localhost:8765` in any browser. You should see the empty queue.

If the server isn't running yet:
```bash
uv run groove serve
```

---

## 2. Day-to-day usage

### "I want one song"

Open `http://localhost:8765`, type `Artist - Song Title` in the form, hit Enter.
Watch the status go `pending → searching → downloading → tagging → done`.

```
Example: Sabrina Carpenter - Espresso
```

The file lands in `library/Sabrina Carpenter/Short n' Sweet (2024)/`.

### "I want one album"

Same form, type `Artist - Album Name`, change the **Kind** dropdown to **Album**.

```
Example: Arctic Monkeys - AM
```

Or from the CLI:
```bash
uv run groove request --kind album "Arctic Monkeys - AM"
```

### "I want to download from a specific YouTube video"

Paste the URL directly:
```
https://youtu.be/hLQl3WQQoQ0
```

beets will still match it against MusicBrainz for clean tags.

### "I want to queue a YouTube playlist"

Paste the playlist URL into the **Bulk Add → YouTube playlist** form:
```
https://www.youtube.com/playlist?list=PLxxxxxxxx
```

Or from the CLI:
```bash
uv run groove request --youtube-playlist "https://www.youtube.com/playlist?list=PLxxxxxxxx"
```

### "I want all of an artist's albums"

Go to `/bulk` → **Artist discography** → type the artist name → pick albums.

Or from the CLI:
```bash
uv run groove request-discography "Arctic Monkeys"
# Shows a numbered list; enter: 1,2,4  or  all
```

### "I ripped a CD"

1. Drop the ripped folder into `/Volumes/Music/groove/inbox/cds/`.
2. Run:
   ```bash
   uv run groove import-cds
   ```
3. beets opens an interactive terminal session. For each album it shows
   candidate matches with confidence scores. Press Enter to accept the top
   match, or use arrow keys + Enter to pick another.

### "I want to browse what I have"

Open `http://localhost:8765/library`. Use the search box to filter by artist,
album, or title.

Or just open `Finder → /Volumes/Music/groove/library/` — the folder layout is
`Artist/Album (Year)/NN - Track.mp3`.

---

## 3. Spotify migration

**Total time: 5–10 minutes.**

### Step 1: Export your Spotify library

1. Open [exportify.net](https://exportify.net) in a browser.
2. Click **Log in with Spotify** and authorize.
3. Click **Export Liked Songs** (or select individual playlists).
4. A `.csv` file downloads to your computer.

### Step 2: Upload to groove

1. Go to `http://localhost:8765/bulk`.
2. Click **Upload file** → pick the `.csv` from Step 1.
3. groove auto-detects the Exportify format.

### Step 3: Preview

groove shows you:
- **X to queue** – tracks that will be downloaded.
- **Y already in library** – tracks you already have.
- **Z already pending** – tracks already queued.

### Step 4: Confirm

Click **Confirm – queue X tracks**. They're added at **low priority** so any
on-demand requests you make still jump ahead.

### Step 5: Watch the progress

The queue page shows progress per track. For a 500-track library expect a few
hours (mostly beets' MusicBrainz lookups). You can close the browser; it runs
in the background.

---

## 4. Discovery & automation

### What gets scraped

| Source | Schedule | Count |
|--------|----------|-------|
| Billboard Hot 100 | Daily 07:00 | 100 tracks |
| UK Official Top 40 | Daily 07:00 | 40 tracks |
| Last.fm global top | Daily 07:00 | 100 tracks |
| Last.fm genre charts | Daily 07:00 | 50 per genre |
| MusicBrainz new releases | Weekly Monday 07:05 | per watchlist artist |

Results appear on the **Discoveries** page. Click **+ Queue** to download
anything that catches your eye, or **Dismiss** to hide it.

### Auto-queue rules

Tracks that appear on **2 or more** charts in the same week are automatically
queued at low priority. Change the threshold in `groove.toml`:

```toml
[auto_queue]
min_chart_appearances = 3   # raise to be more selective
```

### Artist watchlist

Add artists to the watchlist at `/watchlist`. Enable **Auto-download new
albums** to have groove queue new studio albums the week they're detected.

From the CLI:
```bash
uv run groove request-discography "Fontaines D.C."
```

### Customising genre charts

Edit `groove.toml`:
```toml
[discovery]
genres = ["rock", "hip-hop", "electronic", "jazz", "folk"]
```

Any Last.fm tag works as a genre. Restart the server (or wait for the next
daily scrape) for changes to take effect.

---

## 5. Troubleshooting

### "A download failed"

1. Click **Retry** in the queue UI. groove will try again (up to 3 attempts).
2. If it keeps failing, paste a specific YouTube URL into the request form –
   groove will use that URL instead of searching.
3. Run `groove doctor` to check yt-dlp and ffmpeg are healthy.

### "beets picked the wrong album"

```bash
# Re-import and pick a different candidate interactively
beet --config /Volumes/Music/groove/beets.yaml import -L "album:Name Of Album"
```

This opens beets' interactive picker for just that album.

### "A track has wrong tags"

Option A – use the Edit button in the web UI (`/library` → Edit on any track).

Option B – from the CLI:
```bash
beet --config /Volumes/Music/groove/beets.yaml modify \
  "artist:Old Artist" \
  artist="Correct Artist" album="Correct Album"
```

### "I edited tags in Kid3 or Picard and groove doesn't see the change"

Run:
```bash
uv run groove metadata rescan
```

This calls `beet update` which re-reads all files on disk and syncs beets'
database.

### "Drive unplugged mid-download"

The worker pauses automatically when `/Volumes/Music` is not reachable. Replug
the drive; the worker resumes on the next poll cycle (within 5 seconds).

### "Storage is getting full"

```bash
uv run groove doctor
```

Shows free space and a warning when you're below 5 GB. To free up space:
- Archive or delete old `state/archive/` files.
- Run `beet --config ... duplicates -d` to find and remove duplicates.
- Migrate to a larger drive (see Under the hood → Migrating drives).

### "Starting over / wiping requests"

The queue is in `state/requests.json`. You can:
- Open the file in any text editor and delete records.
- Or `echo '[]' > /Volumes/Music/groove/state/requests.json` to wipe it completely.

Archived records are in `state/archive/` and can be deleted safely.

### "The server won't start"

```bash
# Check logs
tail -50 /Volumes/Music/groove/logs/server.log
tail -50 /Volumes/Music/groove/logs/server-error.log

# Check launchd status
launchctl list | grep groove

# Start manually to see errors in the terminal
uv run groove serve
```

---

## 6. Under the hood

### File layout on the drive

```
/Volumes/Music/
└── groove/                         ← the ONLY folder groove touches
    ├── library/                    ← beets-managed (don't hand-edit paths)
    │   └── Artist Name/
    │       └── Album Name (2024)/
    │           ├── 01 - Track.mp3
    │           └── cover.jpg
    ├── inbox/
    │   ├── cds/                    ← drop CD rips here for groove import-cds
    │   ├── downloads/              ← yt-dlp staging area (auto-cleaned after import)
    │   └── review/                 ← weak-match files awaiting your decision
    ├── state/
    │   ├── requests.json           ← download queue
    │   ├── discoveries.json        ← chart findings
    │   ├── watchlist.json          ← artists to monitor
    │   ├── chart_runs.json         ← scraper run audit log
    │   ├── import_log.json         ← import history for every file
    │   ├── .locks/                 ← filelock sentinel files (don't touch)
    │   └── archive/                ← nightly-rotated old records
    ├── db/
    │   └── musiclib.db             ← beets' SQLite index (don't touch)
    ├── logs/                       ← server, scraper, import logs
    └── groove.toml                 ← your config
```

### JSON file schemas

**`state/requests.json`** – array of download requests:
```json
{
  "id": "01HWXYZ...",
  "raw_query": "Arctic Monkeys - AM",
  "kind": "album",
  "artist": "Arctic Monkeys",
  "album": "AM",
  "status": "pending",
  "priority": "normal",
  "attempts": 0,
  "error": null,
  "batch_id": null,
  "created_at": "2026-04-23T10:15:00Z"
}
```

**`state/discoveries.json`** – chart findings:
```json
{
  "id": "01HWX...",
  "source": "billboard",
  "chart_rank": 3,
  "artist": "Sabrina Carpenter",
  "title": "Espresso",
  "auto_queued": false,
  "dismissed": false,
  "appearances": 1,
  "seen_at": "2026-04-23T07:00:00Z"
}
```

**`state/watchlist.json`** – artists to monitor:
```json
{
  "artists": [
    {
      "name": "Arctic Monkeys",
      "mb_artist_id": "ada7a83c-e3b1-40b1-96ba-43200a6cbc19",
      "auto_download_new_albums": true,
      "added_at": "2026-04-20T00:00:00Z"
    }
  ]
}
```

### Beets plugins

| Plugin | Purpose |
|--------|---------|
| `chroma` | AcoustID audio fingerprint – identifies files by audio content, not filename |
| `fetchart` | Downloads cover art from MusicBrainz / Cover Art Archive |
| `embedart` | Embeds cover art into every MP3 file |
| `lastgenre` | Tags `genre` field from Last.fm |
| `replaygain` | Writes ReplayGain loudness tags (via ffmpeg, no extra tool) |
| `lyrics` | Fetches lyrics and writes `.lrc` sidecars |
| `scrub` | Removes junk vendor tags on import |
| `duplicates` | Detects and reports duplicate tracks |
| `mbsync` | Re-fetches fresh MusicBrainz data for already-imported tracks |

### Scheduled jobs (launchd)

```bash
# List running groove agents
launchctl list | grep groove

# Manually trigger a job
launchctl start com.groove.charts

# Temporarily disable a job
launchctl unload ~/Library/LaunchAgents/com.groove.charts.plist

# Re-enable it
launchctl load -w ~/Library/LaunchAgents/com.groove.charts.plist

# See all logs
ls /Volumes/Music/groove/logs/
```

### Changing config

All knobs are in `/Volumes/Music/groove/groove.toml`. Restart the server after
editing:

```bash
# If running via launchd:
launchctl kickstart -k gui/$(id -u)/com.groove.server

# If running manually:
# Ctrl-C then: uv run groove serve
```

### Audio quality

YouTube's best-quality stream is ~128 kbps Opus. groove transcodes to
**MP3 192 kbps CBR** – universally playable, no pretence of higher quality
than the source. To change the bitrate:

```toml
[audio]
bitrate = "320"   # kbps – larger files, same source fidelity
```

### Migrating to a new drive

```bash
# Copy everything
rsync -av --progress /Volumes/Music/ /Volumes/NewDrive/

# Option A: rename the new volume to "Music" (zero config change)
# Option B: update two lines in groove.toml:
#   hdd_root = "/Volumes/NewDrive/groove"
# And in beets.yaml:
#   directory: /Volumes/NewDrive/groove/library
#   library: /Volumes/NewDrive/groove/db/musiclib.db
# Then run: groove metadata rescan
```

Every file in `library/` has embedded MusicBrainz IDs and an AcoustID
fingerprint, so even if the beets database is lost the library is
self-describing and can be rebuilt with `beet import --noincremental`.

### State backup

groove runs a nightly rsync of `state/` to `~/groove-state-backup/`. To run
it manually:

```bash
uv run groove doctor --backup
```
