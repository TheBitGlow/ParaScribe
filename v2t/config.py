"""
v2t.config - Voice2Text 配置与日志管理模块
负责默认配置定义、YAML 配置文件加载、命令行参数解析及统一日志初始化。
"""
import os
import sys
import argparse
import warnings
import logging
from pathlib import Path

# 常量定义：支持的媒体扩展名集合
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".opus"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts", ".wmv"}
ALL_MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

# 断点续传文件格式版本号 (Item F3)
PARTIAL_FORMAT_VERSION = 2

# 标准化环境变量 (Item D2)
_default_cache = os.path.join(os.path.expanduser("~"), ".cache", "modelscope")
os.environ.setdefault("MODELSCOPE_CACHE", _default_cache)
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
warnings.filterwarnings("ignore", category=UserWarning)

# 内置默认核心配置（只读基准，Item C2）
DEFAULT_CONFIG = {
    # 输出控制
    "output_dir": "已生成",              # 默认输出目录: 已生成 (可由命令行 -o 覆盖)
    "output_format": "paragraphs",       # 可选: paragraphs, timestamps, raw, srt
    "also_save_timestamp_version": True, # 额外保存带毫秒时间戳的文本
    "export_srt": True,                  # 自动导出标准 SRT 字幕
    "export_word_docx": True,            # 自动导出排版 Word 文档
    "max_line_length": 60,               # 单行建议最大字数
    "max_paragraph_sentences": 4,        # 单段最大句子数
    "semantic_pause_threshold_ms": 1500, # 语义停顿换段阈值(ms)
    "output_encoding": "utf-8",
    "retry_attempts": 2,
    "model_trust_remote_code": False,
    "log_level": "INFO",
    "auto_open_dir": False,              # 是否在完成后自动打开结果目录 (Item E3)

    # 预处理与分片控制
    "enable_audio_preprocess": True,     # FFmpeg 频域降噪与均衡
    "chunk_duration_sec": 900,           # 基础切片时长(秒)，900s = 15分钟
    "chunk_overlap_sec": 10,             # 切片重叠秒数(Item B1)，防边界吞字
    "ffmpeg_search_paths": [],           # 自定义 FFmpeg 搜索路径 (Item C3)

    # 文本优化控制
    "remove_filler_words": True,         # 清理口语废词 (Item A2)
    "fix_stutter_repeats": True,         # 修复口吃叠字与 ASR 叠词 (Item A4)
    "convert_chinese_numbers": False,    # 是否转换中文数字为阿拉伯数字
    "replacements_file": "replacements.txt", # 自定义 ASR 纠错替换表 (Item A6)

    # 识别与硬件推理
    "device": "auto",                    # 推理设备: auto, cpu, cuda (Item G1)
    "language": "zh",                    # 语言偏好: zh (中英混读), en (英文优先), auto (自动)
    "cpu_threads": 6,                    # CPU 推理线程数
    "vad_max_segment_ms": 30000,         # VAD 最大单段切片毫秒
    "batch_size_s": 300,                 # 批处理秒数
    "hotwords_file": "hotwords.txt",     # 自定义领域热词文件
}


def setup_logging(log_level="INFO"):
    """配置日志（幂等初始化，支持 UTF-8 编码写入文件与控制台）"""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    level = getattr(logging, log_level.upper(), logging.INFO)
    logger = logging.getLogger("ParaScribe")
    logger.setLevel(level)

    if logger.handlers:
        return logger

    logger.propagate = False
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

    file_handler = logging.FileHandler("parascribe.log", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


logger = setup_logging(DEFAULT_CONFIG.get("log_level", "INFO"))


def load_yaml_config(yaml_path="config.yaml"):
    """
    加载 YAML 配置文件 (Item D1)。
    如果未安装 pyyaml 或配置文件不存在，安全优雅降级返回空字典。
    """
    if not os.path.exists(yaml_path):
        return {}

    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            user_data = yaml.safe_load(f) or {}

        # 将嵌套章节扁平化映射到配置 key
        flat_config = {}
        for section_key, section_val in user_data.items():
            if isinstance(section_val, dict):
                for k, v in section_val.items():
                    # 映射常见别名
                    if k == "format":
                        flat_config["output_format"] = v
                    elif k == "encoding":
                        flat_config["output_encoding"] = v
                    elif k == "also_save_timestamp":
                        flat_config["also_save_timestamp_version"] = v
                    elif k == "export_docx":
                        flat_config["export_word_docx"] = v
                    else:
                        flat_config[k] = v
            else:
                flat_config[section_key] = section_val

        logger.debug(f"已加载外部配置文件: {yaml_path}")
        return flat_config
    except ImportError:
        logger.debug("未安装 pyyaml，跳过 config.yaml 加载（可 pip install pyyaml）")
        return {}
    except Exception as e:
        logger.warning(f"解析 YAML 配置文件失败，将使用默认配置: {e}")
        return {}


def parse_args(args_list=None):
    """命令行参数解析 (Item C2)"""
    parser = argparse.ArgumentParser(
        description="言迹 (ParaScribe) 高准度音视频转文字工具 | High-accuracy Speech-to-Text Transcription Tool"
    )
    parser.add_argument("files", nargs="*", help="指定要转录的音视频文件或目录（留空则扫描当前目录）/ Input media files or directories")
    parser.add_argument("-f", "--format", choices=["paragraphs", "timestamps", "raw", "srt"],
                        default=None, help="输出主文稿格式 / Output text format (默认: paragraphs)")
    parser.add_argument("-o", "--output-dir", type=str, default=None, help="指定结果输出目录 / Output directory (默认: 已生成)")
    parser.add_argument("--config", type=str, default="config.yaml", help="指定 YAML 配置文件路径 / Config YAML path")
    parser.add_argument("--lang", choices=["zh", "en", "auto"], default=None,
                        help="语言偏好 / Language preference (zh/en/auto)")
    parser.add_argument("--no-denoise", action="store_true", help="跳过 FFmpeg 频域降噪预处理 / Disable audio denoising")
    parser.add_argument("--no-docx", action="store_true", help="不生成 Word (.docx) 文档 / Skip Word docx export")
    parser.add_argument("--no-srt", action="store_true", help="不生成标准 .srt 字幕文件 / Skip SRT subtitle export")
    parser.add_argument("--hotwords", type=str, default=None, help="自定义热词文件路径 / Custom hotwords file path")
    parser.add_argument("--replacements", type=str, default=None, help="自定义纠错替换表路径 / Custom replacements path")
    parser.add_argument("--overwrite", action="store_true", help="强制覆盖已存在的转录结果 / Overwrite existing results")
    parser.add_argument("--cpu-threads", type=int, default=None, help="CPU 推理线程数 / CPU inference threads")
    parser.add_argument("--chunk-sec", type=int, default=None, help="切片长度秒数 / Chunk duration in seconds")
    parser.add_argument("--overlap-sec", type=int, default=None, help="切片重叠秒数 / Chunk overlap in seconds")
    parser.add_argument("--vad-max-segment", type=int, default=None, help="VAD切片上限毫秒 / VAD max segment in ms")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default=None,
                        help="推理设备选择: auto (自动检测), cpu, cuda / Device selection")
    parser.add_argument("--open-dir", action="store_true", help="转录完成后自动打开输出目录 / Auto open output directory")
    parser.add_argument("--no-open", action="store_true", help="转录完成后不打开输出目录 / Do not open output directory")
    return parser.parse_args(args_list)


def build_config(args=None, yaml_path="config.yaml"):
    """
    构建合并后的全局配置字典 (Item C2)。
    优先级规则: 命令行参数 > YAML 配置文件 > DEFAULT_CONFIG
    """
    config = dict(DEFAULT_CONFIG)

    # 1. 尝试从 YAML 加载
    actual_yaml = args.config if (args and hasattr(args, "config") and args.config) else yaml_path
    yaml_cfg = load_yaml_config(actual_yaml)
    config.update(yaml_cfg)

    # 2. 覆盖命令行参数
    if args:
        if args.format is not None:
            config["output_format"] = args.format
        if getattr(args, "output_dir", None):
            config["output_dir"] = args.output_dir
        if hasattr(args, "lang") and args.lang is not None:
            config["language"] = args.lang
        if args.hotwords is not None:
            config["hotwords_file"] = args.hotwords
        if args.replacements is not None:
            config["replacements_file"] = args.replacements
        if args.cpu_threads is not None:
            config["cpu_threads"] = args.cpu_threads
        if args.chunk_sec is not None:
            config["chunk_duration_sec"] = args.chunk_sec
        if args.overlap_sec is not None:
            config["chunk_overlap_sec"] = args.overlap_sec
        if args.vad_max_segment is not None:
            config["vad_max_segment_ms"] = args.vad_max_segment
        if args.device is not None:
            config["device"] = args.device
        if args.no_denoise:
            config["enable_audio_preprocess"] = False
        if args.no_docx:
            config["export_word_docx"] = False
        if args.no_srt:
            config["export_srt"] = False
        if args.open_dir:
            config["auto_open_dir"] = True
        if args.no_open:
            config["auto_open_dir"] = False

    return config
