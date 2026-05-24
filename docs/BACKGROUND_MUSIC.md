# Background Music

RetroStation MC can include audio in the generated HLS stream. Audio is required for better compatibility with IPTV players that do not reliably play video-only MPEG-TS HLS streams.

## Modes

| Mode | Behavior |
|---|---|
| `none` | Uses generated silent stereo AAC audio |
| `single` | Uses one uploaded audio file |
| `playlist` | Uses an ordered list of uploaded audio files through FFmpeg concat input |

## Supported upload extensions

The upload form accepts:

- `.aac`
- `.flac`
- `.m4a`
- `.mp3`
- `.ogg`
- `.wav`

Maximum upload size is 100 MB per request based on the Flask `MAX_CONTENT_LENGTH` setting.

## Content validation

The server validates file content using magic bytes after upload. A file with a supported extension is rejected if its header does not look like one of the supported audio formats.

Recognized signatures include MP3, FLAC, WAV/RIFF, OGG, AAC ADTS, and ISO BMFF/MP4-style `ftyp` containers.

## Storage path

Uploaded files are stored in:

```text
data/music/
```

Filenames are sanitized with Werkzeug `secure_filename`.

## Single-track mode

When `music_mode=single`, the selected file is used as FFmpeg input.

With loop enabled:

```text
-stream_loop -1 -i <file>
```

With loop disabled, the app mixes the finite music track with infinite silence so the HLS stream continues after the track ends.

## Playlist mode

When `music_mode=playlist`, selected files are written to:

```text
data/music_playlist.txt
```

FFmpeg reads this file with the concat demuxer.

With loop enabled, the concat input is looped. With loop disabled, the finite playlist is mixed with infinite silence so encoding continues after the playlist ends.

## Audio encoding

Silent mode:

```text
-c:a aac -b:a 32k
```

Music modes:

```text
-c:a aac -b:a 128k
```

## Applying music changes

Music settings affect the FFmpeg pipeline. Restart the guide after changing music mode, loop setting, or selected files.
