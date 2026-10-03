# ParaScribe.py - 言迹 (ParaScribe) 高准度音视频转文字工具
# 官方统一启动入口
import sys
from pathlib import Path

# 确保当前路径在 sys.path 中
_current_dir = str(Path(__file__).parent.resolve())
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

# 统一主入口与核心功能导出
from v2t import (
    main,
    DEFAULT_CONFIG as CONFIG,
    clean_and_improve_text,
    format_paragraphs,
    format_paragraphs_semantic,
    format_from_sentence_info,
    format_ms,
    ms_to_srt_time,
    wrap_subtitle_text,
    load_replacements,
    ensure_ffmpeg,
    get_audio_duration_sec,
    preprocess_audio_chunk,
    scan_media_files
)

if __name__ == "__main__":
    main()
