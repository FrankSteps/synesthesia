# Color Configuration (`config.vnl`)

Synesthesia uses `config.vnl` to assign colors to the currently playing track. You can define a default color, match specific albums, artists, or titles, and optionally enable keyword-based matching.

## Example

```ini
// Default color: used when no rule matches
[DEFAULT] = #000000

// Enable keyword matching
[KEYWORD] = TRUE

// Album rules
[ALBUM : "Minecraft - Volume Alpha"] = #60F807
[ALBUM : "Minecraft - Volume Beta"] = #FF1E00
[ALBUM : "Afterimage"] = #8A2BE2

// Artist rules
[ARTIST : "Frank Steps"] = #FF00FF
[ARTIST : "Crystal Castles", "Molchat Doma"] = #00FFFF
[ARTIST : "The Beach Boys"] = #FFD700

// Title rules
[TITLE : "Resonance"] = #FF0044
[TITLE : "Mice on Venus"] = #00FF00

// Keyword rules
[KEYWORD] {
    #00FF00 = c418 | minecraft | mice on venus
    #FF1000 = dead voxel | alpha | ki
    #8A2BE2 = nostalgia | ambient | afterimage
}
```

## Configuration Directives

| Directive           | Description                                                |
| ---                 | ---                                                        |
| `[DEFAULT]`         | Defines the fallback color when no matching rule is found. |
| `[KEYWORD] = TRUE`  | Enables keyword-based color matching.                      |
| `[KEYWORD] = FALSE` | Disables keyword-based color matching.                     |
| `[ALBUM : "..."]`   | Matches an album name.                                     |
| `[ARTIST : "..."]`  | Matches an artist name.                                    |
| `[TITLE : "..."]`   | Matches a track title.                                     |
| `[KEYWORD] { ... }` | Defines keyword groups and their associated colors.        |

Colors must use the hexadecimal `#RRGGBB` format.

## Matching Behavior

**Case-insensitive matching**
Matching ignores letter case. For example, `Frank Steps`, `frank steps`, and `FRANK STEPS` are treated equivalently.

**Partial matching**
Rules use substring matching rather than requiring an exact match. For example:

```ini
[TITLE : "Resonance"] = #FF0044
```

This rule also matches titles such as `Resonance (Slowed)`.

**Multiple values**
Separate multiple values with commas:

```ini
[ARTIST : "Frank Steps", "Djavan"] = #FF00FF
```

The rule matches when the artist metadata contains either value.

**Multiple conditions**
Separate conditions with semicolons:

```ini
[
    ARTIST : "TV Girl", "Oliver Tree";
    TITLE : "Resonance"
] = #FF0044
```

In the current implementation, conditions are combined using **OR**. The rule matches if any specified condition matches. A semicolon does not require all conditions to match.

**Keyword matching**
When keyword matching is enabled, Synesthesia searches the album, artist, and title metadata for the configured keywords:

```ini
[KEYWORD] {
    #00FF00 = minecraft | c418 | mice on venus
    #FF1000 = dead voxel | alpha
}
```

Keywords are separated by vertical bars (`|`). Matching is case-insensitive and uses substring matching.

## Rule Priority

Synesthesia evaluates ordinary album, artist, and title rules before keyword rules.

1. Ordinary rules are evaluated in the order they appear in the configuration file.
2. The first matching ordinary rule determines the color.
3. If no ordinary rule matches and keyword matching is enabled, keyword rules are evaluated in their configured order.
4. If no rule matches, the default color is returned.

This means that rule order matters: place more specific rules before broader rules when they could match the same track.

## Tips

- Use distinct colors to make different albums, artists, or musical styles easy to recognize.
- Set `[KEYWORD] = FALSE` if you want to rely exclusively on explicit album, artist, and title rules.
- Keep the default color dark or neutral if you want unmatched tracks to remain visually unobtrusive.
- Remember that keyword matching can produce unintended matches when a keyword appears as part of a longer word or phrase.
