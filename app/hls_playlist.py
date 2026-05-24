from __future__ import annotations


def trim_playlist_for_delayed_live_edge(
    playlist_text: str,
    *,
    delay_segments: int,
    min_visible_segments: int = 3,
) -> str:
    lines = playlist_text.splitlines()

    def _is_segment_uri(raw_line: str) -> bool:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            return False
        base = stripped.split("?", 1)[0].lower()
        return base.endswith(".ts") or base.endswith(".m4s") or base.endswith(".mp4")

    # Collect segment block ranges [start_idx, end_idx), where start_idx
    # includes per-segment tags (PDT/discontinuity/EXTINF) that precede
    # the URI line.
    segment_blocks: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if not _is_segment_uri(line):
            continue

        start = i
        j = i - 1
        while j >= 0:
            tag = lines[j].strip()
            if tag.startswith("#EXTINF"):
                start = j
                j -= 1
                while j >= 0:
                    prev = lines[j].strip()
                    if prev.startswith("#EXT-X-PROGRAM-DATE-TIME") or prev.startswith("#EXT-X-DISCONTINUITY"):
                        start = j
                        j -= 1
                        continue
                    break
                break
            if tag.startswith("#EXT-X-PROGRAM-DATE-TIME") or tag.startswith("#EXT-X-DISCONTINUITY"):
                start = j
                j -= 1
                continue
            break

        segment_blocks.append((start, i + 1))

    if not segment_blocks:
        return playlist_text

    max_hide = max(0, len(segment_blocks) - max(1, min_visible_segments))
    effective_delay_segments = min(max_hide, max(0, int(delay_segments)))
    if effective_delay_segments <= 0:
        return playlist_text

    cutoff = segment_blocks[-effective_delay_segments][0]
    return "\n".join(lines[:cutoff]) + "\n"
