# Themes

Themes live under:

```text
app/themes/<theme_name>/theme.json
```

The admin UI lists any directory under `app/themes/` that contains a theme file.

## Bundled themes

v1.0.0 includes these theme directories:

- `classic_blue`
- `comcast`
- `dark`
- `directv`
- `light`
- `retro_aol`
- `retro_magazine`
- `retroiptv`
- `tvguide_1990`

## Theme JSON structure

Themes are JSON files with a `colors` object. The current renderer/admin UI expects keys such as:

| Key | Used for |
|---|---|
| `background` | Main guide/admin background |
| `header_bg` | Header bar/background accent |
| `header_text` | Header text |
| `program_bg` | Program cell/panel background |
| `program_text` | Program text |
| `channel_bg` | Channel column/status background |
| `time_text` | Time/header secondary text |
| `grid_line` | Borders and grid lines |

## Example

```json
{
  "colors": {
    "background": "#0b1220",
    "header_bg": "#2f65d9",
    "header_text": "#ffffff",
    "program_bg": "#111b31",
    "program_text": "#e8eefb",
    "channel_bg": "#0a1324",
    "time_text": "#a8b8d8",
    "grid_line": "#223454"
  }
}
```

## Adding a theme

1. Create a new directory under `app/themes/`.
2. Add `theme.json`.
3. Restart or refresh the admin page.
4. Select the theme in the admin UI.
5. Save configuration.
6. Refresh or restart the guide pipeline if needed.

## Theme preview

The admin UI reads theme colors and applies them to CSS variables for immediate preview. The guide renderer reads the selected theme through `guide_state.json`.
