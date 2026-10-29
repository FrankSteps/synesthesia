# Synesthesia

Synesthesia is a music-driven color API that assigns customizable colors to the music you're listening to, making them available for use in external projects and devices. It supports integrations with Strawberry Music Player and YouTube, and can also update your GitHub profile status with the currently playing track. Use it to synchronize colors with music in Arduino projects, hardware interfaces, ambient lighting, and other creative applications.

## Features

- **Multiple sources:** Supports Strawberry Music Player and YouTube.
- **Automatic source selection:** In `auto` mode, the most recently active source takes control.
- **GitHub status integration:** Updates your GitHub profile status with the current track or video title.
- **Custom status emojis:** Choose a preset emoji or enter a custom emoji.
- **Color rules:** Assign colors to albums, artists, track titles, and keywords using `config.vnl`.
- **Control panel:** Configure the active source, GitHub updates, and status emoji through a local web interface.
- **Local API:** Exposes current track information, its URL, and its assigned color.

## Project Structure

```text
synesthesia/
├── extension/
│   ├── content.js
│   ├── icons/
│   │   └── github-beats.png
│   └── manifest.json
├── input/
│   ├── config.vnl
│   ├── docs_input.md
│   └── example.vnl
├── logs/
│   └── server.log
├── src/
│   └── synesthesia.py
├── .env
├── .gitignore
├── LICENSE
├── README.md
├── settings.json
└── start.sh
```

The `.env` and `settings.json` files contain local configuration and may not exist in a fresh clone. The server uses default settings when `settings.json` is absent.

## Requirements

- Linux with Python 3.
- Strawberry Music Player for Strawberry integration.
- A compatible browser for the YouTube extension.
- A GitHub personal access token with permission to update your profile status, if you want GitHub status integration.
- Python dependencies:
  - Flask
  - Flask-CORS
  - Requests
  - python-dotenv
  - dbus-python

On Ubuntu or Debian, install the system packages required to create the virtual environment and build `dbus-python`:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip \
    build-essential pkg-config libdbus-1-dev libglib2.0-dev psmisc
```

Package requirements may vary by distribution.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/FrankSteps/github-beats.git
cd github-beats
```

### 2. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install the dependencies

```bash
pip install flask flask-cors requests python-dotenv dbus-python
```

If `dbus-python` fails to build, check that the required system development packages are installed for your distribution.

### 4. Configure the environment

Create a `.env` file in the project root and add your GitHub personal access token:

```dotenv
GITHUB_TOKEN=your_github_token
```

Keep your token private. Never commit your `.env` file or share your token.

A valid GitHub token is required by the current server implementation at startup, even if you do not intend to use GitHub status updates.

### 5. Configure color rules

Edit `input/config.vnl` to customize how tracks are mapped to colors. See [Color Configuration](#color-configuration) and [`input/docs_input.md`](input/docs_input.md) for details.

### 6. Start the server

From the project root, run:

```bash
./start.sh
```

The script stops any process using port `8080`, activates the `.venv` environment, and starts the server.

The first run may ask for your system password because the script uses `sudo` to release the port.

The server listens on `http://127.0.0.1:8080`. Press `Ctrl+C` in the terminal to stop it.

### 7. Install the browser extension

Load the `extension` directory as an unpacked extension through your browser's extension management page. Enable the extension and open a YouTube video.

### 8. Open the control panel

Visit [http://localhost:8080/control](http://localhost:8080/control).

From the control panel, you can select the source, enable or disable GitHub status updates, and customize the status emoji. Changes are saved locally and persist across server restarts.

## Source Selection

Synesthesia supports three source modes:

| Mode | Behavior |
| --- | --- |
| `auto` | The most recently active source takes control. |
| `strawberry` | Only Strawberry updates the current track. |
| `youtube` | Only YouTube updates the current track. |

The selected source, GitHub update preference, and status emoji are persisted in the root-level `settings.json` file. If the file is absent, the server starts with default settings and uses them until settings are changed.

## Color Configuration

Track colors are configured in `input/config.vnl`.

You can define a default color, match albums, artists, and titles, and optionally enable keyword-based matching.

Example:

```ini
[DEFAULT] = #000000

[KEYWORD] = TRUE

[ALBUM : "Minecraft - Volume Alpha"] = #60F807
[ARTIST : "Frank Steps"] = #FF00FF
[TITLE : "Resonance"] = #FF0044

[KEYWORD] {
    #00FF00 = minecraft | c418 | mice on venus
    #8A2BE2 = nostalgia | ambient | afterimage
}
```

Colors use the hexadecimal `#RRGGBB` format. Matching is case-insensitive and supports partial matches. Ordinary rules are evaluated in file order; keyword rules are considered afterward when keyword matching is enabled.

For the complete syntax and rule behavior, see [`input/docs_input.md`](input/docs_input.md).

## Local API

The server exposes the following endpoints on port `8080`:

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/` | GET | Returns current track metadata and its assigned color. |
| `/current` | GET | Returns the current title, URL, color, and source. |
| `/video` | POST | Receives video information from the browser extension. |
| `/settings` | GET / POST | Reads or updates source, GitHub status, and emoji settings. |
| `/control` | GET | Serves the control panel. |

By default, the server listens on `127.0.0.1`, making the API accessible from the local machine.

## License

This project is licensed under the [MIT License](LICENSE).