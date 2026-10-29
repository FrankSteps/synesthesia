# Synesthesia

Synesthesia is a music-driven color API that assigns customizable colors to the music you're listening to, making them available for use in external projects and devices. It supports integrations with Strawberry Music Player and YouTube, and can also update your GitHub profile status with the currently playing track. Use it to synchronize colors with music in Arduino projects, hardware interfaces, ambient lighting, and other creative applications.

## Features

- **Multiple sources:** Supports Strawberry Music Player and YouTube.
- **Automatic source selection:** In `auto` mode, the most recently active source takes control.
- **GitHub status integration:** Updates your GitHub profile status with the current track or video title.
- **Custom status emojis:** Choose a preset emoji or enter a custom emoji.
- **Color rules:** Assign colors to albums, artists, track titles, and keywords using `config.vnl`.
- **Control panel:** Configure the active source, GitHub updates, and status emoji through a local web interface.
- **Local API:** Exposes the current track information, its URL, and its assigned color.

## Project Structure

```text
synesthesia
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
├── server/
│   ├── start.sh
│   └── synesthesia.py
├── .env_example
├── .gitignore
├── LICENSE
├── README.md
└── settings.json
```

## Requirements

- Python 3
- Strawberry Music Player (for Strawberry integration)
- A compatible browser for the YouTube extension
- A GitHub personal access token that permits updating your profile status
- The Python dependencies used by the server:
  - Flask
  - Flask-CORS
  - Requests
  - python-dotenv
  - dbus-python

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/FrankSteps/github-beats.git
cd github-beats
```

### 2. Configure your GitHub token

Create a `.env` file in the project root and configure your GitHub token:

```dotenv
GITHUB_TOKEN=your_github_token
```

Keep your token private. Do not commit your `.env` file.

### 3. Install the dependencies

Activate your Python virtual environment, if you use one, and install the required dependencies.

```bash
pip install flask flask-cors requests python-dotenv dbus-python
```

Depending on your Linux distribution, `dbus-python` may require system development packages.

### 4. Start the server

Run the project's startup script:

```bash
./server/start.sh
```

The server listens on port `8080`.

### 5. Install the browser extension

Load the `extension` directory as an unpacked extension using your browser's extension management page. Enable the extension and open a YouTube video.

### 6. Open the control panel

Visit:

[http://localhost:8080/control](http://localhost:8080/control)

From the control panel, you can select the source, enable or disable GitHub status updates, and customize the status emoji.

## Source Selection

Synesthesia supports three source modes:

| Mode | Behavior |
| --- | --- |
| `auto` | The most recently active source takes control. |
| `strawberry` | Only Strawberry updates the current track. |
| `youtube` | Only YouTube updates the current track. |

The selected source and other settings are persisted in `settings.json`.

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

## License

This project is licensed under the [MIT License](LICENSE).
