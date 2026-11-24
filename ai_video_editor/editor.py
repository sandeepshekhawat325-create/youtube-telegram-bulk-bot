import json
import subprocess
from pathlib import Path


def run(command: list[str]) -> str:
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout.strip()


def probe_media(path: Path) -> dict:
    raw = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    return json.loads(raw)


def render_video(input_path: Path, output_path: Path) -> None:
    """Phase 1 automatic enhancement: MP4 conversion, safe downscale, fades, audio normalization."""
    metadata = probe_media(input_path)
    video = next((s for s in metadata.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("Input file does not contain a video stream")

    duration = float(metadata.get("format", {}).get("duration", 0) or video.get("duration", 0) or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in metadata.get("streams", []))

    vf = [
        "scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease",
        "setsar=1",
    ]
    fade = min(0.35, duration / 8) if duration > 0 else 0
    if fade:
        vf.extend([f"fade=t=in:st=0:d={fade:.3f}", f"fade=t=out:st={max(0, duration-fade):.3f}:d={fade:.3f}"])

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path),
           "-map", "0:v:0", "-vf", ",".join(vf)]
    if has_audio:
        cmd += ["-map", "0:a:0?", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    else:
        cmd += ["-an"]

    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", str(output_path)]
    run(cmd)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not create an output file")
