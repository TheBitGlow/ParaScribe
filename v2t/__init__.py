"""
v2t - 言迹 (ParaScribe) 核心音视频转写引擎
"""
from v2t.main import main
from v2t.config import DEFAULT_CONFIG, build_config
from v2t.text_processor import (
    clean_and_improve_text,
    format_paragraphs,
    format_paragraphs_semantic,
    format_from_sentence_info,
    format_ms,
    ms_to_srt_time,
    wrap_subtitle_text,
    load_replacements
)
from v2t.ffmpeg_utils import (
    ensure_ffmpeg,
    get_audio_duration_sec,
    preprocess_audio_chunk,
    scan_media_files
)

__version__ = "2.0.0"

__all__ = [
    "main",
    "DEFAULT_CONFIG",
    "build_config",
    "clean_and_improve_text",
    "format_paragraphs",
    "format_paragraphs_semantic",
    "format_from_sentence_info",
    "format_ms",
    "ms_to_srt_time",
    "wrap_subtitle_text",
    "load_replacements",
    "ensure_ffmpeg",
    "get_audio_duration_sec",
    "preprocess_audio_chunk",
    "scan_media_files"
]
