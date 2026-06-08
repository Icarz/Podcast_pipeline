"""Audio transcription via the Groq Whisper API.

Sends an audio file to Groq's ``whisper-large-v3`` model and returns the full
text plus segment-level timestamps.

Groq enforces a 25 MB request limit; long episodes (Modern Wisdom runs 2-3 h,
often 100 MB+) exceed it and the connection drops silently. For oversized files
we split the audio into ~20-minute chunks with ffmpeg, transcribe each chunk,
and stitch the results back into one transcript with timestamps shifted by each
chunk's real start offset -- so callers get the identical dict shape either way.
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
# Clamp the derived segment length: never longer than 20 min (keeps stitching
# offsets sane) nor shorter than 1 min (avoids a pathological flood of chunks).
MAX_CHUNK_SECONDS = 1200
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
    """
    pattern = os.path.join(out_dir, "chunk_%03d.mp3")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", audio_path,
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


def transcribe(audio_path: str) -> dict:
    """Transcribe ``audio_path`` with Groq Whisper (segment + word timestamps).

    Files at/under Groq's 25 MB request limit go in a single request; larger
    files are split into ~20-minute chunks, transcribed individually, and
    stitched with offset-corrected timestamps. Either way returns a dict with:
        ``text``     -- the full transcript string
        ``segments`` -- list of {"start": float, "end": float, "text": str}
        ``words``    -- list of {"word": str, "start": float, "end": float}
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(audio_path)

    client = _client()
    size_bytes = os.path.getsize(audio_path)
    size_mb = size_bytes / (1024 * 1024)

    if size_bytes > MAX_SINGLE_FILE_BYTES:
        return _transcribe_chunked(audio_path, client, size_mb)

    logger.info("Transcribing %s (%.1f MB) with %s", os.path.basename(audio_path), size_mb, MODEL)
    result = _transcribe_file(audio_path, client)
    logger.info(
        "Transcribed %d segments, %d words, %d chars",
        len(result["segments"]), len(result["words"]), len(result["text"]),
    )
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
