# Developed by:  Francisco Passos :: Frank Steps
# Developed on:  09/09/2025
# Modified on:   10/29/2026
#
# Unified server:
#   - Reads the current track from Strawberry (D-Bus / MPRIS)
#   - Receives the current YouTube video from the browser extension
#   - Maps each track to a color using the rules in config.input
#   - Optionally mirrors the track on the GitHub user status
#   - Source, GitHub updates and status emoji are controlled from /control

# libraries
import json
import logging
import os
import re
import threading
import time
import dbus
import requests

from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.serving import WSGIRequestHandler


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR = Path(__file__).resolve().parent.parent
POLL_INTERVAL = 2  # seconds between Strawberry checks
DEFAULT_EMOJI = ":musical_note:"

# auto = last source to play wins | strawberry / youtube = ignore the other one
VALID_SOURCES = ("auto", "strawberry", "youtube")


#
# GITHUB TOKEN
#


load_dotenv(BASE_DIR / ".env")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if not GITHUB_TOKEN:
    raise RuntimeError("GITHUB_TOKEN not found. Check your .env file.")

logging.info("Token loaded successfully!")


#
# STRAWBERRY INTERFACE
#


def get_strawberry_track():
    # Query the Strawberry player through its MPRIS D-Bus interface
    bus = dbus.SessionBus()
    player = bus.get_object(
        "org.mpris.MediaPlayer2.strawberry",
        "/org/mpris/MediaPlayer2"
    )
    properties = dbus.Interface(player, "org.freedesktop.DBus.Properties")

    metadata = properties.Get("org.mpris.MediaPlayer2.Player", "Metadata")
    status = properties.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus")

    album = metadata.get("xesam:album", "unknown")
    artist = metadata.get("xesam:artist", "unknown")
    title = metadata.get("xesam:title", "unknown")

    # MPRIS returns the artist as a list
    if isinstance(artist, (list, tuple)):
        artist = ", ".join(str(a) for a in artist)

    return {
        "album": str(album),
        "artist": str(artist),
        "title": str(title),
        "playing": str(status) == "Playing"
    }


#
# COLOR RULES (config.input)
#


def load_config(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        content = file.read()

    # Remove comments
    content = re.sub(r"(?m)//.*$", "", content)

    # [DEFAULT] = #RRGGBB
    default_match = re.search(
        r"(?im)^\s*\[DEFAULT\]\s*=\s*(#[0-9A-Fa-f]{6})\s*$",
        content
    )
    default_color = default_match.group(1) if default_match else None

    # [KEYWORD] = TRUE | FALSE
    keyword_match = re.search(
        r"(?im)^\s*\[KEYWORD\]\s*=\s*(TRUE|FALSE)\s*$",
        content
    )
    keyword_enabled = (
        keyword_match is not None
        and keyword_match.group(1).upper() == "TRUE"
    )

    # [KEYWORD] { #RRGGBB = word | word ... }
    keyword_rules = []
    keyword_block = re.search(
        r"\[KEYWORD\]\s*\{(.*?)\}",
        content,
        re.DOTALL | re.IGNORECASE
    )

    if keyword_block:
        for color, keywords in re.findall(
            r"(?m)^\s*(#[0-9A-Fa-f]{6})\s*=\s*([^\n]+)",
            keyword_block.group(1)
        ):
            for keyword in keywords.split("|"):
                keyword = keyword.strip()
                if keyword:
                    keyword_rules.append((keyword.casefold(), color))

    # Strip the directives so only ordinary rules remain
    rules_content = re.sub(
        r"\[KEYWORD\]\s*\{.*?\}",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE
    )

    rules_content = re.sub(
        r"(?im)^\s*\[(?:DEFAULT|KEYWORD)\]\s*=\s*[^\n]*$",
        "",
        rules_content
    )

    # Ordinary rules: [ALBUM: "a"; ARTIST: "b"] = #RRGGBB
    rule_pattern = re.compile(
        r"\[((?:[^\]]|\n)*?)\]\s*=\s*(#[0-9A-Fa-f]{6})",
        re.DOTALL
    )

    condition_pattern = re.compile(
        r'(ALBUM|ARTIST|TITLE)\s*:\s*([^;]+)',
        re.IGNORECASE
    )

    rules = []

    for conditions, color in rule_pattern.findall(rules_content):
        parsed_conditions = []

        for field, values in condition_pattern.findall(conditions):
            expected_values = [
                value.casefold()
                for value in re.findall(r'"([^"]*)"', values)
            ]

            if expected_values:
                parsed_conditions.append((field.upper(), expected_values))

        if parsed_conditions:
            rules.append({"conditions": parsed_conditions, "color": color})

    return {
        "default": default_color,
        "keyword_enabled": keyword_enabled,
        "keyword_rules": keyword_rules,
        "rules": rules
    }


def resolve_color(config, track):
    normalized_track = {
        key.upper(): str(value).casefold()
        for key, value in track.items()
        if key in ("album", "artist", "title")
    }

    # Ordinary rules, in file order; ";" means OR (any condition may match)
    for rule in config["rules"]:
        if any(
            any(value in normalized_track.get(field, "") for value in expected_values)
            for field, expected_values in rule["conditions"]
        ):
            return rule["color"]

    # Keyword rules, in file order
    if config["keyword_enabled"]:
        track_text = " ".join(normalized_track.values())

        for keyword, color in config["keyword_rules"]:
            if keyword in track_text:
                return color

    return config["default"]


#
# GITHUB STATUS
#


# Variables avoid manual escaping of titles and emojis
STATUS_MUTATION = """
mutation($emoji: String, $message: String) {
    changeUserStatus(input: {emoji: $emoji, message: $message}) {
        status { emoji message }
    }
}
"""


def update_github_status(emoji=None, message=None):
    payload = {
        "query": STATUS_MUTATION,
        # GitHub limits the status message to 80 characters
        "variables": {"emoji": emoji, "message": message[:80] if message else None}
    }
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}

    try:
        response = requests.post(
            "https://api.github.com/graphql",
            json=payload,
            headers=headers,
            timeout=10
        )
        result = response.json()
    except (requests.RequestException, ValueError):
        logging.exception("Failed to update GitHub status")
        return

    if "errors" in result:
        logging.warning("GitHub rejected the status: %s", result["errors"])
    else:
        logging.info("GitHub status updated: %s", result["data"]["changeUserStatus"]["status"])


#
# SHARED STATE
#


app = Flask(
    __name__,
    static_folder=str(BASE_DIR / "assets"),
    static_url_path="/assets"
)

CORS(app)

config = load_config(BASE_DIR / "input" / "config.vnl")
state_lock = threading.Lock()

SETTINGS_FILE = BASE_DIR / "settings.json"


def normalize_emoji(value):
    # Accepts ":name:" shortcodes or native emoji; returns None if invalid
    value = str(value).strip()

    if re.fullmatch(r":[a-z0-9_+-]+:", value):
        return value

    if 0 < len(value) <= 16 and all(ord(char) > 127 for char in value):
        return value

    return None


def normalize_color(value):
    # Accept #RRGGBB or RGB values such as '255 255 255' / '255, 255, 255'
    value = str(value).strip()

    if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return value.upper()

    rgb_match = re.fullmatch(
        r"\(?\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*\)?",
        value
    )

    if rgb_match:
        red, green, blue = map(int, rgb_match.groups())
        if all(0 <= channel <= 255 for channel in (red, green, blue)):
            return f"#{red:02X}{green:02X}{blue:02X}"

    return None


def load_settings():
    # Defaults, overridden by whatever valid values were saved last time
    loaded = {
        "source": os.getenv("DEFAULT_SOURCE", "auto").lower(),
        "github_enabled": True,
        "emoji": DEFAULT_EMOJI,
        "forced_color_enabled": False,
        "forced_color": "#FFFFFF"
    }

    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}

    if not isinstance(saved, dict):
        saved = {}

    if saved.get("source") in VALID_SOURCES:
        loaded["source"] = saved["source"]

    if isinstance(saved.get("github_enabled"), bool):
        loaded["github_enabled"] = saved["github_enabled"]

    emoji = normalize_emoji(saved["emoji"]) if "emoji" in saved else None
    if emoji:
        loaded["emoji"] = emoji

    if loaded["source"] not in VALID_SOURCES:
        loaded["source"] = "auto"

    forced_color = normalize_color(saved.get("forced_color", loaded["forced_color"]))
    if forced_color:
        loaded["forced_color"] = forced_color

    if isinstance(saved.get("forced_color_enabled"), bool):
        loaded["forced_color_enabled"] = saved["forced_color_enabled"]

    return loaded


def save_settings():
    # Write to a temp file first so a crash never leaves a broken file
    try:
        temp_file = SETTINGS_FILE.with_suffix(".tmp")
        temp_file.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(SETTINGS_FILE)
    except OSError:
        logging.exception("Failed to save settings")


# User-controlled settings (changed from /control, saved to settings.json)
settings = load_settings()

# Current track, shared by both sources.
# base_color is the color from the config rules; the color actually
# served is computed on demand by effective_color(), so it can never
# be out of sync with the forced color settings.
state = {
    "source": None,
    "album": "unknown",
    "artist": "unknown",
    "title": "",
    "url": "",
    "message": "",
    "base_color": config["default"]
}

# Last video received from YouTube, re-applied when switching back to it
last_youtube = None

# Last Strawberry track sent, so the watcher only reacts to changes
watcher = {"last_key": None}


def effective_color():
    # Call with state_lock already held
    if settings["forced_color_enabled"]:
        return settings["forced_color"]
    return state["base_color"]


def set_current_track(source, track, url=""):
    if source == "strawberry" and track["artist"] != "unknown":
        message = f'{track["artist"]} - {track["title"]}'
    else:
        message = track["title"]

    base_color = resolve_color(config, track)

    with state_lock:
        state.update({
            "source": source,
            "album": track["album"],
            "artist": track["artist"],
            "title": track["title"],
            "url": url,
            "message": message,
            "base_color": base_color
        })
        color = effective_color()

    push_status()
    return color


def push_status():
    # Send the current track to GitHub, if the user enabled it
    with state_lock:
        enabled = settings["github_enabled"]
        emoji = settings["emoji"]
        message = state["message"]

    if enabled and message:
        update_github_status(emoji, message)


#
# STRAWBERRY WATCHER (background thread)
#


def strawberry_watcher():
    while True:
        try:
            if settings["source"] in ("auto", "strawberry"):
                track = get_strawberry_track()

                if track["playing"]:
                    key = (track["album"], track["artist"], track["title"])

                    if key != watcher["last_key"]:
                        watcher["last_key"] = key
                        color = set_current_track("strawberry", track)
                        logging.info("Strawberry: %s - %s [%s] : %s", track["artist"], track["title"], track["album"], color)

        except Exception:
            # Strawberry is closed or D-Bus is unavailable: try again later
            pass

        time.sleep(POLL_INTERVAL)


#
# TRACK ENDPOINTS
#


# Receives the current video from the browser extension
@app.route("/video", methods=["POST"])
def video():
    global last_youtube

    data = request.get_json(silent=True) or {}
    title = data.get("title")
    url = data.get("url", "")

    if not title:
        return jsonify({"error": "Missing title"}), 400

    track = {"album": "unknown", "artist": "unknown", "title": title}

    # Remember it even when ignored, so it can be applied later
    last_youtube = (track, url)

    if settings["source"] not in ("auto", "youtube"):
        return jsonify({"status": "ignored", "source": settings["source"]}), 200

    logging.info("YouTube: %s (%s)", title, url)
    set_current_track("youtube", track, url)

    return "OK", 200


# Current track with its color
@app.get("/")
def get_track():
    with state_lock:
        return jsonify({
            "source": state["source"],
            "album": state["album"],
            "artist": state["artist"],
            "title": state["title"],
            "url": state["url"],
            "color": effective_color()
        })


# Current title and URL (used by the C++ client)
@app.get("/current")
def get_current():
    with state_lock:
        return jsonify({
            "title": state["title"],
            "url": state["url"],
            "color": effective_color(),
            "source": state["source"]
        }), 200


# Clear the current github status
@app.post("/clear")
def clear_status():
    update_github_status()
    return jsonify({"status": "cleared"}), 200



#
# SETTINGS
#


# GET returns the settings; POST accepts any of:
# source, github_enabled, emoji, forced_color, forced_color_enabled

@app.route("/settings", methods=["GET", "POST"])
def settings_endpoint():

    if request.method == "POST":

        data = request.get_json(silent=True) or {}
        updates = {}

        if "source" in data:
            source = str(data["source"]).lower()

            if source not in VALID_SOURCES:
                return jsonify({
                    "error": f"Invalid source. Use: {', '.join(VALID_SOURCES)}"
                }), 400

            updates["source"] = source

        if "github_enabled" in data:
            if not isinstance(data["github_enabled"], bool):
                return jsonify({"error": "github_enabled must be true or false"}), 400

            updates["github_enabled"] = data["github_enabled"]

        if "emoji" in data:
            emoji = normalize_emoji(data["emoji"])

            if emoji is None:
                return jsonify({
                    "error": "Invalid emoji. Use :name: or a native emoji"
                }), 400

            updates["emoji"] = emoji

        if "forced_color" in data:
            forced_color = normalize_color(data["forced_color"])

            if forced_color is None:
                return jsonify({
                    "error": "Invalid color. Use #RRGGBB or RGB values such as 255 255 255"
                }), 400

            updates["forced_color"] = forced_color

        if "forced_color_enabled" in data:
            if not isinstance(data["forced_color_enabled"], bool):
                return jsonify({
                    "error": "forced_color_enabled must be true or false"
                }), 400

            updates["forced_color_enabled"] = data["forced_color_enabled"]

        with state_lock:
            previous = dict(settings)
            settings.update(updates)
            save_settings()

        logging.info("Settings updated: %s", updates)

        source_changed = (
            "source" in updates
            and updates["source"] != previous["source"]
        )

        github_turned_on = (
            updates.get("github_enabled")
            and not previous["github_enabled"]
        )

        emoji_changed = (
            updates.get("emoji", previous["emoji"])
            != previous["emoji"]
        )

        # Re-apply the track of the newly selected source right away
        if source_changed and updates["source"] == "strawberry":
            watcher["last_key"] = None

        if source_changed and updates["source"] == "youtube" and last_youtube:
            set_current_track("youtube", last_youtube[0], last_youtube[1])

        elif github_turned_on or emoji_changed:
            push_status()

    with state_lock:
        return jsonify({
            **settings,
            "valid_sources": list(VALID_SOURCES)
        })


#
# CONTROL PAGE
#

CONTROL_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/x-icon" href="/assets/logo/cdonut.ico?">
<title>Synesthesia</title>
<style>
    :root {
        color-scheme: light dark;
        --bg: #f4f4f7;
        --card: #ffffff;
        --text: #16161d;
        --muted: #6b6b7a;
        --border: #e2e2ea;
        --accent: #ff0044;
        --accent-text: #ffffff;
        --field: #f0f0f5;
        --shadow: 0 1px 2px rgba(0,0,0,.05), 0 8px 24px rgba(0,0,0,.06);
    }

    :root[data-theme="light"] { color-scheme: light; }

    @media (prefers-color-scheme: dark) {
        :root:not([data-theme="light"]) {
            --bg: #0e0e13;
            --card: #17171f;
            --text: #f1f1f6;
            --muted: #8d8d9e;
            --border: #272733;
            --field: #1f1f29;
            --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35);
        }
    }

    :root[data-theme="dark"] {
        color-scheme: dark;
        --bg: #0e0e13;
        --card: #17171f;
        --text: #f1f1f6;
        --muted: #8d8d9e;
        --border: #272733;
        --field: #1f1f29;
        --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35);
    }

    * { box-sizing: border-box; }

    body {
        margin: 0;
        padding: 6vh 1rem 4rem;
        background: var(--bg);
        color: var(--text);
        font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
        line-height: 1.4;
    }

    main {
        max-width: 520px;
        margin: 0 auto;
        display: grid;
        gap: 1rem;
    }

    header.top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
    }

    /* Logo + title */
    .brand {
        display: flex;
        align-items: center;
        gap: .8rem;
        min-width: 0;
    }

    .brand img {
        width: 5.5rem;
        height: 5.5rem;
        object-fit: contain;
        flex-shrink: 0;
    }

    header h1 {
        margin: 0;
        font-size: 1.6rem;
        letter-spacing: -.02em;
    }

    header p {
        margin: .2rem 0 0;
        color: var(--muted);
        font-size: .9rem;
    }

    .card {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 1.1rem 1.2rem;
        box-shadow: var(--shadow);
    }

    .card h2 {
        margin: 0 0 .8rem;
        font-size: .75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: .08em;
        color: var(--muted);
    }

    .row {
        display: flex;
        align-items: center;
        gap: .6rem;
    }

    .row.between { justify-content: space-between; }

    .label {
        font-size: .85rem;
        color: var(--muted);
        margin: 0 0 .4rem;
    }

    .label:not(:first-of-type) { margin-top: 1rem; }

    /* Icon buttons (gear / close) */
    .icon-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 2.5rem;
        height: 2.5rem;
        padding: 0;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: var(--card);
        color: var(--muted);
        cursor: pointer;
        flex-shrink: 0;
        transition: color .15s, border-color .3s, transform .2s;
    }

    .icon-btn:hover { color: var(--accent); border-color: var(--accent); }
    .icon-btn svg { width: 20px; height: 20px; }
    #open-settings:hover svg { transform: rotate(45deg); }
    #open-settings svg { transition: transform .25s; }

    .icon-btn.small {
        width: 2rem;
        height: 2rem;
        border-radius: 10px;
        background: var(--field);
    }

    .icon-btn.small svg { width: 16px; height: 16px; }

    /* Modal */
    dialog.modal {
        width: min(92vw, 420px);
        max-height: 90vh;
        overflow: auto;
        margin: auto;
        color: var(--text);
    }

    dialog.modal::backdrop {
        background: rgba(0,0,0,.5);
        backdrop-filter: blur(3px);
    }

    dialog.modal[open] { animation: pop .18s ease-out; }

    @keyframes pop {
        from { opacity: 0; transform: translateY(8px) scale(.98); }
        to   { opacity: 1; transform: none; }
    }

    .modal-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: .8rem;
    }

    .modal-head h2 { margin: 0; }

    /* Now playing */
    .dot {
        width: 2.4rem;
        height: 2.4rem;
        border-radius: 12px;
        border: 1px solid var(--border);
        flex-shrink: 0;
        transition: background .3s;
    }

    .np-text { min-width: 0; flex: 1; }

    #np-title {
        font-weight: 600;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    #np-meta {
        color: var(--muted);
        font-size: .8rem;
        font-variant-numeric: tabular-nums;
    }

    /* Segmented control */
    .segmented {
        display: grid;
        grid-auto-flow: column;
        grid-auto-columns: 1fr;
        background: var(--field);
        border-radius: 12px;
        padding: 4px;
        gap: 4px;
    }

    .segmented button {
        border: 0;
        background: transparent;
        color: var(--muted);
        padding: .55rem;
        border-radius: 9px;
        font: inherit;
        font-weight: 500;
        cursor: pointer;
        transition: background .3s, color .15s;
    }

    .segmented button:hover { color: var(--text); }

    .segmented button.active {
        background: var(--accent);
        color: var(--accent-text);
    }

    /* Switch */
    .switch {
        position: relative;
        width: 48px;
        height: 28px;
        border: 0;
        border-radius: 999px;
        background: var(--field);
        cursor: pointer;
        transition: background .3s;
        flex-shrink: 0;
    }

    .switch::after {
        content: "";
        position: absolute;
        top: 3px;
        left: 3px;
        width: 22px;
        height: 22px;
        border-radius: 50%;
        background: #fff;
        box-shadow: 0 1px 3px rgba(0,0,0,.3);
        transition: transform .2s;
    }

    .switch[aria-checked="true"] { background: var(--accent); }
    .switch[aria-checked="true"]::after { transform: translateX(20px); }

    /* Buttons & inputs */
    .btn {
        border: 1px solid var(--border);
        background: var(--field);
        color: var(--text);
        padding: .55rem 1rem;
        border-radius: 10px;
        font: inherit;
        font-weight: 500;
        cursor: pointer;
        transition: filter .15s, transform .05s, background .3s;
    }

    .btn:hover { filter: brightness(1.08); }
    .btn:active { transform: scale(.97); }

    .btn.primary {
        background: var(--accent);
        border-color: var(--accent);
        color: var(--accent-text);
    }

    .btn.danger { color: var(--accent); }

    input[type="text"] {
        flex: 1;
        min-width: 0;
        padding: .6rem .8rem;
        border-radius: 10px;
        border: 1px solid var(--border);
        background: var(--field);
        color: var(--text);
        font: inherit;
    }

    input[type="text"]:focus {
        outline: 2px solid var(--accent);
        outline-offset: -1px;
    }

    /* Emoji grid */
    .emojis {
        display: grid;
        grid-template-columns: repeat(6, 1fr);
        gap: .5rem;
        margin-bottom: .8rem;
    }

    .emojis button {
        aspect-ratio: 1;
        font-size: 1.4rem;
        border: 2px solid transparent;
        background: var(--field);
        border-radius: 12px;
        cursor: pointer;
        transition: transform .1s, border-color .3s;
    }

    .emojis button:hover { transform: translateY(-2px); }
    .emojis button.active { border-color: var(--accent); }

    /* Color pickers */
    input[type="color"] {
        width: 3rem;
        height: 2.6rem;
        padding: 0;
        border: 1px solid var(--border);
        border-radius: 10px;
        background: var(--field);
        cursor: pointer;
        flex-shrink: 0;
    }

    .hint {
        margin: .7rem 0 0;
        font-size: .8rem;
        color: var(--muted);
    }

    .badge {
        font-size: .7rem;
        font-weight: 600;
        padding: .15rem .55rem;
        border-radius: 999px;
        background: var(--field);
        color: var(--muted);
        transition: background .3s;
    }

    .badge.on {
        background: var(--accent);
        color: var(--accent-text);
    }

    [hidden] { display: none !important; }

    /* Toast */
    #toast {
        position: fixed;
        left: 50%;
        bottom: 1.5rem;
        transform: translate(-50%, 150%);
        background: #222;
        color: #fff;
        padding: .7rem 1.1rem;
        border-radius: 12px;
        font-size: .9rem;
        box-shadow: 0 8px 24px rgba(0,0,0,.3);
        max-width: 90vw;
        z-index: 1000;
        opacity: 0;
        visibility: hidden;
        pointer-events: none;
        transition: transform .25s, opacity .25s, visibility .25s;
    }
    
    #toast.show {
        transform: translate(-50%, 0);
        opacity: 1;
        visibility: visible;
    }
    
    #toast.error { background: #c62828; }
</style>
</head>
<body>
<main>
    <header class="top">
        <div class="brand">
            <img src="/assets/logo/cdonut.png" alt="synesthesia logo" onerror="this.hidden = true">
            <div>
                <h1>Synesthesia</h1>
                <p>Source, GitHub status and color controls</p>
            </div>
        </div>
        <button class="icon-btn" id="open-settings" aria-label="Appearance settings" title="Appearance">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="3"></circle>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
            </svg>
        </button>
    </header>

    <section class="card">
        <h2>Now</h2>
        <div class="row">
            <div class="dot" id="np-dot"></div>
            <div class="np-text">
                <div id="np-title">Nothing playing</div>
                <div id="np-meta">—</div>
            </div>
        </div>
    </section>

    <section class="card">
        <h2>Source</h2>
        <div class="segmented" id="sources"></div>
    </section>

    <section class="card">
        <h2>GitHub status</h2>
        <div class="row between">
            <span>Mirror music to status</span>
            <button class="switch" id="github" role="switch" aria-label="Update GitHub status"></button>
        </div>
        <div class="row" style="margin-top: .9rem;">
            <button class="btn danger" id="clear">Clear status</button>
        </div>
    </section>

    <section class="card">
        <h2>Emoji</h2>
        <div class="emojis" id="emojis"></div>
        <div class="row">
            <input type="text" id="custom" placeholder=":fire: or 🔥" autocomplete="off">
            <button class="btn primary" id="apply">Apply</button>
        </div>
        <p class="hint" id="current"></p>
    </section>

    <section class="card">
        <div class="row between">
            <h2 style="margin: 0;">Forced color</h2>
            <span class="badge" id="forced-badge">OFF</span>
        </div>
        <div class="row" style="margin-top: .9rem;">
            <input type="color" id="color-picker" value="#ffffff" aria-label="Pick a color">
            <input type="text" id="color-value" placeholder="#FFFFFF or 255 255 255" autocomplete="off">
        </div>
        <div class="row" style="margin-top: .7rem;">
            <button class="btn primary" id="apply-color">Apply</button>
            <button class="btn" id="disable-color">Disable</button>
        </div>
        <p class="hint">When enabled, it ignores the config rules and always uses this color. The interface follows it too.</p>
    </section>
</main>

<dialog id="settings-dialog" class="card modal" aria-labelledby="settings-title">
    <div class="modal-head">
        <h2 id="settings-title">Appearance</h2>
        <button class="icon-btn small" id="close-settings" aria-label="Close" title="Close">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
        </button>
    </div>

    <p class="label">Theme</p>
    <div class="segmented" id="theme"></div>

    <p class="label">Accent color</p>
    <div class="segmented" id="accent-mode"></div>

    <div class="row" id="accent-custom" style="margin-top: .7rem;" hidden>
        <input type="color" id="accent-picker" value="#ff0044" aria-label="Pick an accent color">
        <input type="text" id="accent-value" placeholder="#FF0044 or 255 0 68" autocomplete="off">
        <button class="btn primary" id="accent-apply">Apply</button>
    </div>

    <p class="hint" id="accent-hint"></p>
</dialog>

<div id="toast"></div>

<script>
    const SOURCES = [["auto", "Auto"], ["strawberry", "Strawberry"], ["youtube", "YouTube"]];
    const THEMES = [["auto", "Auto"], ["light", "Light"], ["dark", "Dark"]];
    const ACCENT_MODES = [["music", "From music"], ["custom", "Custom"]];
    const PRESETS = [
        ["🎵", ":musical_note:"], ["🎧", ":headphones:"], ["🎶", ":notes:"],
        ["📺", ":tv:"], ["🔥", ":fire:"], ["❤️", ":heart:"]
    ];
    const FALLBACK_ACCENT = "#ff0044";

    const $ = id => document.getElementById(id);
    const colorPicker = $("color-picker");
    const colorValue = $("color-value");
    const accentPicker = $("accent-picker");
    const accentValue = $("accent-value");
    const settingsDialog = $("settings-dialog");
    let toastTimer;

    // State
    let settings = {};          // server settings
    let trackColor = null;      // color of the current track
    let prefs = loadPrefs();    // browser-only preferences

    function loadPrefs() {
        const defaults = {theme: "auto", accent: "music", custom: FALLBACK_ACCENT};
        try {
            return Object.assign(defaults, JSON.parse(localStorage.getItem("synesthesia-prefs") || "{}"));
        } catch (err) {
            return defaults;
        }
    }

    function savePrefs() {
        try {
            localStorage.setItem("synesthesia-prefs", JSON.stringify(prefs));
        } catch (err) {
            // storage unavailable: preferences last only for this session
        }
    }

    // Accepts "#RGB", "#RRGGBB", "255 255 255" or "255,255,255" -> "#rrggbb" (or null)
    function normalizeColor(input) {
        if (!input) return null;
        const v = String(input).trim();

        let m = v.match(/^#?([0-9a-f]{3})$/i);
        if (m) return "#" + m[1].split("").map(c => c + c).join("").toLowerCase();

        m = v.match(/^#?([0-9a-f]{6})$/i);
        if (m) return "#" + m[1].toLowerCase();

        m = v.match(/^(\\d{1,3})[\\s,]+(\\d{1,3})[\\s,]+(\\d{1,3})$/);
        if (m) {
            const rgb = m.slice(1, 4).map(Number);
            if (rgb.every(n => n <= 255)) {
                return "#" + rgb.map(n => n.toString(16).padStart(2, "0")).join("");
            }
        }
        return null;
    }

    // Pick black or white text depending on how bright the color is
    function textOn(hex) {
        const n = parseInt(hex.slice(1), 16);
        const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
        const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        return luminance > 0.6 ? "#111111" : "#ffffff";
    }

    // Priority: custom > forced color > current track > fallback
    function currentAccent() {
        if (prefs.accent === "custom") {
            const c = normalizeColor(prefs.custom);
            if (c) return c;
        }
        if (settings.forced_color_enabled) {
            const c = normalizeColor(settings.forced_color);
            if (c) return c;
        }
        return normalizeColor(trackColor) || FALLBACK_ACCENT;
    }

    function applyAccent() {
        const accent = currentAccent();
        const root = document.documentElement.style;
        root.setProperty("--accent", accent);
        root.setProperty("--accent-text", textOn(accent));
    }

    function applyTheme() {
        if (prefs.theme === "auto") {
            document.documentElement.removeAttribute("data-theme");
        } else {
            document.documentElement.setAttribute("data-theme", prefs.theme);
        }
    }

    function renderAppearance() {
        $("theme").replaceChildren(...THEMES.map(([name, label]) =>
            makeButton(label, name === prefs.theme, () => {
                prefs.theme = name;
                savePrefs();
                applyTheme();
                renderAppearance();
            })));

        $("accent-mode").replaceChildren(...ACCENT_MODES.map(([name, label]) =>
            makeButton(label, name === prefs.accent, () => {
                prefs.accent = name;
                savePrefs();
                applyAccent();
                renderAppearance();
            })));

        const custom = normalizeColor(prefs.custom) || FALLBACK_ACCENT;
        $("accent-custom").hidden = prefs.accent !== "custom";
        accentPicker.value = custom;
        accentValue.value = custom.toUpperCase();

        $("accent-hint").textContent = prefs.accent === "music"
            ? "The interface color follows the song that is playing."
            : "Using your custom color, regardless of the song.";
    }

    function toast(message, isError) {
        const el = $("toast");
        el.textContent = message;
        el.className = "show" + (isError ? " error" : "");
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { el.className = ""; }, 2800);
    }

    function makeButton(label, active, onClick) {
        const button = document.createElement("button");
        button.textContent = label;
        if (active) button.className = "active";
        button.onclick = onClick;
        return button;
    }

    // Send changes to the server; the response already has the saved settings
    async function update(changes) {
        try {
            const res = await fetch("/settings", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(changes)
            });
            const data = await res.json();

            if (!res.ok) {
                toast(data.error || "Error saving", true);
                return;
            }

            render(data);
            refreshNow();
        } catch (err) {
            toast("Can't reach the server", true);
        }
    }

    function render(s) {
        settings = s;

        $("sources").replaceChildren(...SOURCES.map(([name, label]) =>
            makeButton(label, name === s.source, () => update({source: name}))));

        const github = $("github");
        github.setAttribute("aria-checked", String(s.github_enabled));
        github.onclick = () => update({github_enabled: !s.github_enabled});

        $("emojis").replaceChildren(...PRESETS.map(([label, code]) =>
            makeButton(label, code === s.emoji, () => update({emoji: code}))));
        $("current").textContent = "Current emoji: " + s.emoji;

        colorPicker.value = (s.forced_color || "#FFFFFF").toLowerCase();
        colorValue.value = (s.forced_color || "#FFFFFF").toUpperCase();

        const badge = $("forced-badge");
        badge.textContent = s.forced_color_enabled ? "ON" : "OFF";
        badge.className = "badge" + (s.forced_color_enabled ? " on" : "");

        applyAccent();
    }

    async function refreshNow() {
        try {
            const res = await fetch("/current", {cache: "no-store"});
            const t = await res.json();

            trackColor = t.color || null;

            $("np-title").textContent = t.title || "Nothing playing";
            $("np-dot").style.background = t.color || "transparent";
            $("np-meta").textContent = (t.source || "—") + " · " + (t.color || "no color");

            applyAccent();
        } catch (err) {
            // server offline: keep the last values
        }
    }

    async function load() {
        try {
            render(await (await fetch("/settings")).json());
        } catch (err) {
            toast("Can't reach the server", true);
        }
        refreshNow();
    }

    // Appearance pop-up
    $("open-settings").onclick = () => {
        renderAppearance();
        settingsDialog.showModal();
    };

    $("close-settings").onclick = () => settingsDialog.close();

    // Click on the backdrop (outside the card) closes the pop-up
    settingsDialog.addEventListener("click", e => {
        if (e.target === settingsDialog) settingsDialog.close();
    });

    colorPicker.addEventListener("input", () => {
        colorValue.value = colorPicker.value.toUpperCase();
    });

    accentPicker.addEventListener("input", () => {
        accentValue.value = accentPicker.value.toUpperCase();
        prefs.custom = accentPicker.value;
        savePrefs();
        applyAccent();
    });

    $("accent-apply").onclick = () => {
        const c = normalizeColor(accentValue.value);
        if (!c) {
            toast("Invalid color", true);
            return;
        }
        prefs.custom = c;
        savePrefs();
        applyAccent();
        renderAppearance();
    };

    $("apply").onclick = () => {
        const value = $("custom").value.trim();
        if (value) {
            update({emoji: value});
            $("custom").value = "";
        }
    };

    $("apply-color").onclick = () => {
        const value = colorValue.value.trim() || colorPicker.value;
        update({forced_color: value, forced_color_enabled: true});
    };

    $("disable-color").onclick = () => update({forced_color_enabled: false});

    $("clear").onclick = async () => {
        await fetch("/clear", {method: "POST"});
        toast("GitHub status cleared");
    };

    applyTheme();
    applyAccent();
    renderAppearance();
    load();
    setInterval(refreshNow, 3000);
</script>
</body>
</html>
"""



@app.get("/control")
def control():
    return CONTROL_PAGE


#
# RUN SERVER
#


if __name__ == "__main__":
    class QuietBadRequestHandler(WSGIRequestHandler):
        # Silence the noise from malformed requests (HTTP 400)
        def log_request(self, code="-", size="-"):
            if str(code) == "400":
                return
            super().log_request(code, size)

        def send_error(self, code, message=None, explain=None):
            if code == 400:
                self.close_connection = True
                return
            super().send_error(code, message, explain)

    threading.Thread(target=strawberry_watcher, daemon=True).start()

    app.run(
        host="0.0.0.0",
        port=8080,
        debug=False,
        request_handler=QuietBadRequestHandler
    )