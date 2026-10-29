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

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import dbus
import requests
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


def update_github_status(emoji, message):
    payload = {
        "query": STATUS_MUTATION,
        # GitHub limits the status message to 80 characters
        "variables": {"emoji": emoji, "message": message[:80]}
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


app = Flask(__name__)
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


def load_settings():
    # Defaults, overridden by whatever valid values were saved last time
    loaded = {
        "source": os.getenv("DEFAULT_SOURCE", "auto").lower(),
        "github_enabled": True,
        "emoji": DEFAULT_EMOJI
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

# Current track, shared by both sources
state = {
    "source": None,
    "album": "unknown",
    "artist": "unknown",
    "title": "",
    "url": "",
    "message": "",
    "color": config["default"]
}

# Last video received from YouTube, re-applied when switching back to it
last_youtube = None

# Last Strawberry track sent, so the watcher only reacts to changes
watcher = {"last_key": None}


def set_current_track(source, track, url=""):
    if source == "strawberry" and track["artist"] != "unknown":
        message = f'{track["artist"]} - {track["title"]}'
    else:
        message = track["title"]

    color = resolve_color(config, track)

    with state_lock:
        state.update({
            "source": source,
            "album": track["album"],
            "artist": track["artist"],
            "title": track["title"],
            "url": url,
            "message": message,
            "color": color
        })

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
            "color": state["color"]
        })


# Current title and URL (used by the C++ client)
@app.get("/current")
def get_current():
    with state_lock:
        return jsonify({
            "title": state["title"],
            "url": state["url"],
            "color": state["color"],
            "source": state["source"]
        }), 200


#
# SETTINGS
#


# GET returns the settings; POST accepts any of: source, github_enabled, emoji
@app.route("/settings", methods=["GET", "POST"])
def settings_endpoint():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        updates = {}

        if "source" in data:
            source = str(data["source"]).lower()
            if source not in VALID_SOURCES:
                return jsonify({"error": f"Invalid source. Use: {', '.join(VALID_SOURCES)}"}), 400
            updates["source"] = source

        if "github_enabled" in data:
            if not isinstance(data["github_enabled"], bool):
                return jsonify({"error": "github_enabled must be true or false"}), 400
            updates["github_enabled"] = data["github_enabled"]

        if "emoji" in data:
            emoji = normalize_emoji(data["emoji"])
            if emoji is None:
                return jsonify({"error": "Invalid emoji. Use :name: or a native emoji"}), 400
            updates["emoji"] = emoji

        with state_lock:
            previous = dict(settings)
            settings.update(updates)
            save_settings()

        logging.info("Settings updated: %s", updates)

        source_changed = "source" in updates and updates["source"] != previous["source"]
        github_turned_on = updates.get("github_enabled") and not previous["github_enabled"]
        emoji_changed = updates.get("emoji", previous["emoji"]) != previous["emoji"]

        # Re-apply the track of the newly selected source right away
        if source_changed and updates["source"] == "strawberry":
            watcher["last_key"] = None

        if source_changed and updates["source"] == "youtube" and last_youtube:
            set_current_track("youtube", last_youtube[0], last_youtube[1])
        elif github_turned_on or emoji_changed:
            push_status()

    with state_lock:
        return jsonify({**settings, "valid_sources": list(VALID_SOURCES)})


#
# CONTROL PAGE
#


CONTROL_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Synesthesia</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: sans-serif; max-width: 480px; margin: 8vh auto; padding: 0 1rem; text-align: center; }
  h3 { margin: 2rem 0 .6rem; }
  button { font-size: 1.1rem; padding: .6rem 1.2rem; margin: .3rem; cursor: pointer; }
  button.active { background: #1db954; color: #fff; border: none; }
  input { font-size: 1.1rem; padding: .5rem; width: 9rem; text-align: center; }
  #error { color: #d33; min-height: 1.4rem; margin-top: 1rem; }
</style>
</head>
<body>
  <h3>Source</h3>
  <div id="sources"></div>

  <h3>GitHub status</h3>
  <div id="github"></div>

  <h3>Emoji</h3>
  <div id="emojis"></div>
  <input id="custom" placeholder=":fire: or 🔥">
  <button id="apply">Apply</button>
  <p id="current"></p>

  <div id="error"></div>

<script>
  const SOURCES = ["auto", "strawberry", "youtube"];
  const PRESETS = [
    ["🎵", ":musical_note:"], ["🎧", ":headphones:"], ["🎶", ":notes:"],
    ["📺", ":tv:"], ["🔥", ":fire:"], ["❤️", ":heart:"]
  ];

  // Send changes to the server and show its error message, if any
  async function update(changes) {
    const res = await fetch("/settings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(changes)
    });
    const data = await res.json();
    document.getElementById("error").textContent = res.ok ? "" : data.error;
    load();
  }

  function makeButton(label, active, onClick) {
    const button = document.createElement("button");
    button.textContent = label;
    if (active) button.className = "active";
    button.onclick = onClick;
    return button;
  }

  async function load() {
    const s = await (await fetch("/settings")).json();

    document.getElementById("sources").replaceChildren(...SOURCES.map(name =>
      makeButton(name, name === s.source, () => update({source: name}))));

    document.getElementById("github").replaceChildren(
      makeButton(s.github_enabled ? "Updates: ON" : "Updates: OFF", s.github_enabled,
        () => update({github_enabled: !s.github_enabled})));

    document.getElementById("emojis").replaceChildren(...PRESETS.map(([label, code]) =>
      makeButton(label, code === s.emoji, () => update({emoji: code}))));

    document.getElementById("current").textContent = "Current emoji: " + s.emoji;
  }

  document.getElementById("apply").onclick = () => {
    const value = document.getElementById("custom").value.trim();
    if (value) update({emoji: value});
  };

  load();
</script>
</body>
</html>"""


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
        port=8080,
        debug=False,
        request_handler=QuietBadRequestHandler
    )