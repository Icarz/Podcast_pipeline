"""Audio transcription via the Groq Whisper API.

Sends an audio file to Groq's ``whisper-large-v3`` model and returns the full
text plus segment-level timestamps.

Groq enforces a 25 MB request limit; long episodes (Modern Wisdom runs 2-3 h,
often 100 MB+) exceed it and the connection drops silently. For oversized files
we split the audio into ~5-minute chunks with ffmpeg, transcribe each chunk,
and stitch the results back into one transcript with timestamps shifted by each
chunk's real start offset -- so callers get the identical dict shape either way.
Chunks are kept short (not just under the byte limit) because Whisper's own
word-timestamp alignment drifts within a single long transcription request;
short chunks bound how far that drift can accumulate before the next chunk's
boundary resets it. See ``MAX_CHUNK_SECONDS`` below for the full story.
"""

import logging
import os
import shutil
import subprocess
import tempfile

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "whisper-large-v3"

# Groq caps a single transcription request at 25 MB. Stay a touch under it so a
# file hovering near the line doesn't trip the silent drop.
MAX_SINGLE_FILE_BYTES = 24 * 1024 * 1024
# Target byte budget per chunk. We size the segment *duration* from the file's
# real bitrate (size / duration) so a high-bitrate episode gets shorter chunks
# rather than blindly cutting 20-min segments that can themselves exceed 25 MB.
TARGET_CHUNK_BYTES = 20 * 1024 * 1024
# Clamp the derived segment length. Confirmed by measurement (Jul 1 2026): the
# chunk-stitching offset itself is accurate to ~50ms cumulative (ffprobe's
# duration estimate on a stream-copied chunk is fine) -- the real source of a
# ~1-1.5s caption/audio desync traced to a real render was Whisper's own
# word-timestamp alignment drifting WITHIN a single long transcription request
# (the bad clip sat 8.8 minutes into an uninterrupted 20-minute chunk, nowhere
# near a chunk boundary). Whisper's word timestamps are decoder cross-attention
# based, not a true forced-aligner, and drift increases the further you get
# from the start of one unbroken inference. Shortening the per-request window
# bounds how much drift can accumulate before the next (accurate) chunk
# boundary resets it -- more Groq requests for very long episodes, but each
# one is a ~5-8s round trip, so the added latency is minor next to the
# correctness gain. Never longer than 5 min (keeps within-chunk timestamp
# drift small) nor shorter than 1 min (avoids a pathological flood of chunks).
MAX_CHUNK_SECONDS = 300
MIN_CHUNK_SECONDS = 60


def _client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set (check your .env file)")
    return Groq(api_key=api_key)


def _seg_value(segment, key):
    """Read ``key`` from a segment that may be a dict or an SDK object."""
    if isinstance(segment, dict):
        return segment.get(key)
    return getattr(segment, key, None)


def _transcribe_file(audio_path: str, client: Groq) -> dict:
    """Send one (already-small-enough) file to Groq and normalize the result."""
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f.read()),
            model=MODEL,
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )

    raw_segments = _seg_value(result, "segments") or []
    segments = [
        {
            "start": _seg_value(s, "start"),
            "end": _seg_value(s, "end"),
            "text": (_seg_value(s, "text") or "").strip(),
        }
        for s in raw_segments
    ]

    raw_words = _seg_value(result, "words") or []
    words = [
        {
            "word": (_seg_value(w, "word") or "").strip(),
            "start": _seg_value(w, "start"),
            "end": _seg_value(w, "end"),
        }
        for w in raw_words
        if (_seg_value(w, "word") or "").strip()
    ]

    text = (_seg_value(result, "text") or "").strip()
    return {"text": text, "segments": segments, "words": words}


def _ffprobe_duration(path: str) -> float:
    """Return the duration of ``path`` in seconds via ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def _segment_seconds(audio_path: str, size_bytes: int) -> int:
    """Pick a segment length (s) so each chunk stays under ``TARGET_CHUNK_BYTES``.

    Derived from the file's real average bitrate (size / duration) so a
    high-bitrate file gets proportionally shorter chunks, then clamped to
    [``MIN_CHUNK_SECONDS``, ``MAX_CHUNK_SECONDS``].
    """
    duration = _ffprobe_duration(audio_path)
    bytes_per_second = size_bytes / duration
    seconds = int(TARGET_CHUNK_BYTES / bytes_per_second)
    return max(MIN_CHUNK_SECONDS, min(seconds, MAX_CHUNK_SECONDS))


def _split_audio(audio_path: str, out_dir: str, segment_seconds: int) -> list[str]:
    """Split ``audio_path`` into ``segment_seconds`` MP3 chunks with ffmpeg.

    Uses stream copy (``-c copy``) so it's fast and lossless; ffmpeg cuts on the
    nearest packet boundary, so real chunk durations vary slightly from
    ``segment_seconds`` -- the caller reads each chunk's actual length to offset
    timestamps rather than assuming an exact stride.

    ``-map 0:a`` restricts the copy to the audio stream only. Some podcast MP3s
    carry an embedded cover-art image as a second ("attached pic") stream; if
    that image's codec data is malformed (seen in the wild: JPEG bytes
    mislabeled as PNG in the ID3 tag), ffmpeg's segment muxer fails to write a
    header for it and aborts the WHOLE split with "Could not write header" --
    even though the image is irrelevant to transcription and the audio stream
    itself is perfectly fine. Excluding it up front sidesteps the bug entirely.
    """
    pattern = os.path.join(out_dir, "chunk_%03d.mp3")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", audio_path,
            "-map", "0:a",
            "-f", "segment",
            "-segment_time", str(segment_seconds),
            "-c", "copy",
            pattern,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    chunks = sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir)
        if f.startswith("chunk_") and f.endswith(".mp3")
    )
    if not chunks:
        raise RuntimeError(f"ffmpeg produced no chunks for {audio_path}")
    return chunks


def _transcribe_chunked(audio_path: str, client: Groq, size_mb: float) -> dict:
    """Transcribe an oversized file by splitting, transcribing, and stitching."""
    tmp_dir = tempfile.mkdtemp(prefix="transcribe_chunks_")
    try:
        seg_seconds = _segment_seconds(audio_path, os.path.getsize(audio_path))
        chunks = _split_audio(audio_path, tmp_dir, seg_seconds)
        logger.warning(
            "File too large for single request (%.1f MB), splitting into %d chunks",
            size_mb, len(chunks),
        )

        all_segments: list[dict] = []
        all_words: list[dict] = []
        text_parts: list[str] = []
        offset = 0.0  # cumulative real duration of chunks already transcribed

        for i, chunk in enumerate(chunks):
            logger.info("Transcribing chunk %d/%d (%s)", i + 1, len(chunks), os.path.basename(chunk))
            part = _transcribe_file(chunk, client)

            for s in part["segments"]:
                all_segments.append({
                    "start": (s["start"] or 0.0) + offset,
                    "end": (s["end"] or 0.0) + offset,
                    "text": s["text"],
                })
            for w in part["words"]:
                all_words.append({
                    "word": w["word"],
                    "start": (w["start"] or 0.0) + offset,
                    "end": (w["end"] or 0.0) + offset,
                })
            if part["text"]:
                text_parts.append(part["text"])

            # Advance the offset by this chunk's true duration so the next
            # chunk's timestamps line up with the full episode.
            offset += _ffprobe_duration(chunk)

        text = " ".join(text_parts).strip()
        logger.info(
            "Stitched %d chunks: %d segments, %d words, %d chars",
            len(chunks), len(all_segments), len(all_words), len(text),
        )
        return {"text": text, "segments": all_segments, "words": all_words}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _enforce_monotonic_words(words: list[dict]) -> list[dict]:
    """Clamp word start/end times to be non-decreasing, in transcript (text) order.

    Whisper occasionally emits a word with an earlier timestamp than the word
    immediately before it in the transcript -- a real, confirmed alignment
    glitch (production case: word "that" timestamped at 40.30s appeared right
    after "being" at 40.56s, both correct in TEXT order). Every downstream
    consumer that walks ``words`` sequentially assumes non-decreasing times:
    ``video_gen``'s caption-clip loop computes each word's on-screen window as
    ``[this_word.start, next_word.start)``, so an out-of-order start makes a
    caption clip begin before the previous one has finished, rendering two
    caption blocks on top of each other. ``ai_extract``'s sentence-boundary
    snapping has the same assumption. Fix once here, at the source, rather
    than in every consumer: clamp each word's start (and end) to at least the
    previous word's end. This never reorders the words themselves (which
    would garble the sentence), only tightens a timestamp that briefly ran
    backwards.
    """
    out: list[dict] = []
    prev_end = 0.0
    for w in words:
        start, end = w.get("start"), w.get("end")
        if start is None or end is None:
            out.append(w)
            continue
        if start < prev_end:
            start = prev_end
        if end < start:
            end = start
        out.append({**w, "start": start, "end": end})
        prev_end = end
    return out


def transcribe(audio_path: str) -> dict:
    """Transcribe ``audio_path`` with Groq Whisper (segment + word timestamps).

    Files at/under Groq's 25 MB request limit go in a single request; larger
    files are split into ~5-minute chunks, transcribed individually, and
    stitched with offset-corrected timestamps. Either way returns a dict with:
        ``text``     -- the full transcript string
        ``segments`` -- list of {"start": float, "end": float, "text": str}
        ``words``    -- list of {"word": str, "start": float, "end": float},
                         guaranteed non-decreasing start/end times (see
                         ``_enforce_monotonic_words``)
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(audio_path)

    client = _client()
    size_bytes = os.path.getsize(audio_path)
    size_mb = size_bytes / (1024 * 1024)

    if size_bytes > MAX_SINGLE_FILE_BYTES:
        result = _transcribe_chunked(audio_path, client, size_mb)
    else:
        logger.info("Transcribing %s (%.1f MB) with %s", os.path.basename(audio_path), size_mb, MODEL)
        result = _transcribe_file(audio_path, client)
        logger.info(
            "Transcribed %d segments, %d words, %d chars",
            len(result["segments"]), len(result["words"]), len(result["text"]),
        )

    result["words"] = _enforce_monotonic_words(result["words"])
    return result


if __name__ == "__main__":
    import glob

    import config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    mp3s = sorted(
        glob.glob(os.path.join(config.TMP_DIR, "*.mp3")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not mp3s:
        raise SystemExit(f"No MP3 found in {config.TMP_DIR} - run rss_ingest first.")

    audio_path = mp3s[0]
    print(f"Transcribing: {os.path.basename(audio_path)}\n")

    result = transcribe(audio_path)

    print(f"Total segments: {len(result['segments'])}")
    print(f"Total chars   : {len(result['text'])}\n")

    print("=== First 5 segments ===")
    for seg in result["segments"][:5]:
        print(f"[{seg['start']:>7.2f} - {seg['end']:>7.2f}]  {seg['text']}")

    print("\n=== Transcript preview (first 400 chars) ===")
    print(result["text"][:400].encode("ascii", "replace").decode("ascii"))
