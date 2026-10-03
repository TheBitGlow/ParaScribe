"""
v2t.ffmpeg_utils - FFmpeg 工具链检测、音频切片降噪与临时文件管理
包含 FFmpeg 自动发现、版本探测与降级(Item F1)、临时文件泄漏守护(Item F2)、磁盘检查(Item F4)。
"""
import os
import sys
import glob
import shutil
import atexit
import tempfile
import subprocess
import re
import logging
from pathlib import Path
from v2t.config import ALL_MEDIA_EXTS

logger = logging.getLogger("ParaScribe")

# 临时文件追踪集合，防止异常退出时残留在 temp 目录 (Item F2)
_ACTIVE_TEMP_FILES = set()


def _cleanup_all_temp_files():
    """退出前自动执行的临时文件清理回调 (Item F2)"""
    global _ACTIVE_TEMP_FILES
    for path in list(_ACTIVE_TEMP_FILES):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    _ACTIVE_TEMP_FILES.clear()


atexit.register(_cleanup_all_temp_files)


def cleanup_stale_temp_files():
    """在程序启动时清理历史残留的临时分片文件 (Item F2)"""
    try:
        patterns = [
            os.path.join(tempfile.gettempdir(), "parascribe_chunk_*.wav"),
            os.path.join(tempfile.gettempdir(), "v2t_chunk_*.wav")
        ]
        for pat in patterns:
            for old_file in glob.glob(pat):
                try:
                    os.remove(old_file)
                    logger.debug(f"已清理历史残留临时文件: {old_file}")
                except OSError:
                    pass
    except Exception:
        pass


def check_disk_space(target_dir, estimated_size_mb=100):
    """
    检查目标目录磁盘剩余空间是否充裕 (Item F4)。
    若不足预估大小则发出告警。
    """
    try:
        p = Path(target_dir).resolve()
        # 若目录尚未创建，检查其存在的父目录
        while not p.exists() and p.parent != p:
            p = p.parent

        usage = shutil.disk_usage(str(p))
        free_mb = usage.free / (1024 * 1024)
        if free_mb < estimated_size_mb:
            logger.warning(
                f"磁盘剩余空间偏低: {free_mb:.1f} MB (建议保留至少 {estimated_size_mb} MB)"
            )
            return False
        return True
    except Exception as e:
        logger.debug(f"检查磁盘空间失败: {e}")
        return True


def ensure_ffmpeg(config=None):
    """
    自动检测并配置 FFmpeg 与 FFprobe 二进制路径。
    支持用户自定义搜索路径配置 (Item C3) + WinGet 路径 + 环境变量 PATH。
    支持 FFmpeg 版本检测与能力探测 (Item F1)。
    """
    cfg = config or {}
    user_search_paths = cfg.get("ffmpeg_search_paths", [])

    winget_matches = glob.glob(
        os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffmpeg.exe"),
        recursive=True
    )
    candidates = [
        *user_search_paths,
        r"D:\ProgramFiles\ffmpeg9.0\bin\ffmpeg.exe",
        *sorted(winget_matches, reverse=True),
        shutil.which("ffmpeg"),
        r"D:\Obj\ffmpeg9.0\bin\ffmpeg.exe",
        r"D:\Obj\ffmpeg8.0\bin\ffmpeg.exe",
    ]

    ffmpeg_exe = None
    for path in candidates:
        if path and os.path.exists(path):
            ffmpeg_exe = path
            ffmpeg_dir = os.path.dirname(os.path.abspath(path))
            if ffmpeg_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
            break

    # 回退检测 imageio-ffmpeg
    if not ffmpeg_exe:
        try:
            import imageio_ffmpeg
            src_exe = imageio_ffmpeg.get_ffmpeg_exe()
            if src_exe and os.path.exists(src_exe):
                target_dir = r"D:\ProgramFiles\ffmpeg9.0\bin"
                os.makedirs(target_dir, exist_ok=True)
                target_exe = os.path.join(target_dir, "ffmpeg.exe")
                if not os.path.exists(target_exe):
                    shutil.copy2(src_exe, target_exe)
                os.environ["PATH"] = target_dir + os.pathsep + os.environ.get("PATH", "")
                ffmpeg_exe = target_exe
        except Exception:
            pass

    if not ffmpeg_exe:
        print("\n" + "=" * 72)
        print("【提示 / Notice】")
        print("未检测到可用的 FFmpeg 工具链 (音视频解码与降噪核心依赖)。")
        print("No valid FFmpeg toolchain found (required for audio decoding & denoising).")
        print("=" * 72)

        # 检查是否支持交互式输入
        user_choice = ""
        try:
            if sys.stdin and sys.stdin.isatty():
                prompt_msg = (
                    "\n是否允许言迹自动通过 WinGet 为您下载安装 FFmpeg？(Y/N) [默认: Y]\n"
                    "Would you like ParaScribe to automatically install FFmpeg via WinGet? (Y/N) [Default: Y]: "
                )
                user_choice = input(prompt_msg).strip().upper()
                if not user_choice:
                    user_choice = "Y"
            else:
                user_choice = "N"
        except Exception:
            user_choice = "N"

        if user_choice == "Y":
            print("\n正在启动 WinGet 安装 FFmpeg，请稍候...")
            print("Launching WinGet to install FFmpeg, please wait...\n")
            try:
                install_cmd = [
                    "winget", "install", "Gyan.FFmpeg.Essentials",
                    "--accept-package-agreements", "--accept-source-agreements"
                ]
                ret = subprocess.run(install_cmd)
                if ret.returncode == 0:
                    print("\nFFmpeg 安装完成！正在重新检测路径...")
                    print("FFmpeg installation completed! Re-scanning paths...\n")
                    return ensure_ffmpeg(config)
                else:
                    logger.error("WinGet 安装 FFmpeg 退出异常，退出码: %s", ret.returncode)
            except Exception as e:
                logger.error(f"调用 WinGet 安装 FFmpeg 失败: {e}")

        logger.error(
            "未找到可用的 FFmpeg！请手动安装: `winget install Gyan.FFmpeg.Essentials` 或下载解压后配置环境变量。\n"
            "FFmpeg not found! Please install manually: `winget install Gyan.FFmpeg.Essentials`."
        )
        sys.exit(1)

    # 探测 FFmpeg 主版本号 (Item F1)
    major_ver = 99
    try:
        ver_res = subprocess.run(
            [ffmpeg_exe, "-version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="ignore"
        )
        m = re.search(r"ffmpeg version (\d+)\.(\d+)", ver_res.stdout)
        if m:
            major_ver = int(m.group(1))
            minor_ver = int(m.group(2))
            logger.info(f"已启用 FFmpeg: {ffmpeg_exe} (版本 {major_ver}.{minor_ver})")
            if major_ver < 5:
                logger.warning(
                    f"FFmpeg 主版本为 {major_ver} (< 5.0)，speechnorm 语音均衡滤镜将自动安全降级。"
                )
        else:
            logger.info(f"已启用 FFmpeg: {ffmpeg_exe}")
    except Exception:
        logger.info(f"已启用 FFmpeg: {ffmpeg_exe}")

    if config is not None:
        config["_ffmpeg_version_major"] = major_ver

    # 寻找配套的 ffprobe.exe
    ffprobe_candidates = [
        os.path.join(os.path.dirname(ffmpeg_exe), "ffprobe.exe"),
        shutil.which("ffprobe"),
    ]
    for w in winget_matches:
        probe_p = os.path.join(os.path.dirname(w), "ffprobe.exe")
        if os.path.exists(probe_p):
            ffprobe_candidates.append(probe_p)

    ffprobe_exe = None
    for p in ffprobe_candidates:
        if p and os.path.exists(p):
            ffprobe_exe = p
            break

    if ffprobe_exe:
        logger.info(f"已启用 FFprobe: {ffprobe_exe}")
    else:
        logger.warning("未检测到独立 ffprobe.exe，将通过 ffmpeg -i 探测时长")

    return ffmpeg_exe, ffprobe_exe


def get_audio_duration_sec(media_path, ffmpeg_exe, ffprobe_exe=None):
    """
    使用 ffprobe 或 ffmpeg 获取音视频媒体的精确总时长（秒）。
    统一指定 encoding='utf-8', errors='ignore'，彻底杜绝中文文件名与元数据导致的解码崩溃。
    """
    media_path_str = str(media_path)

    # 优先尝试 ffprobe
    if ffprobe_exe and os.path.exists(ffprobe_exe):
        try:
            res = subprocess.run(
                [ffprobe_exe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", media_path_str],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="ignore", check=True
            )
            dur = float(res.stdout.strip())
            if dur > 0:
                return dur
        except Exception as e:
            logger.debug(f"ffprobe 获取时长失败，回退至 ffmpeg -i: {e}")

    # 回退到 ffmpeg -i 解析 Duration
    try:
        res = subprocess.run(
            [ffmpeg_exe, "-i", media_path_str],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="ignore"
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception as e:
        logger.warning(f"通过 ffmpeg 获取时长发生异常: {e}")

    logger.warning(f"未能探测到媒体时长: {media_path_str}，将作为单分片处理")
    return 0.0


def preprocess_audio_chunk(media_path, ffmpeg_exe, start_sec=0, duration_sec=900,
                           enable_denoise=True, ffmpeg_version_major=99):
    """
    分片音频前端处理流水线：
    1. 自动利用 -vn 丢弃视频轨（纯音频/视频文件通用），截取 [start_sec, start_sec + duration_sec]
    2. 重采样为 16kHz 单声道 16-bit PCM WAV
    3. 可选频域降噪 (afftdn) + 带通滤波 (80Hz~7600Hz) + 人声动态归一化 (speechnorm)
       若 FFmpeg < 5.0，自动降级跳过 speechnorm (Item F1)
    4. 采用 PIPE 捕获并严格以 utf-8 读取 stderr，错误时输出详细日志
    5. 临时文件纳入自动清理跟踪守护 (Item F2)
    """
    temp_dir = tempfile.gettempdir()
    temp_wav = os.path.join(temp_dir, f"parascribe_chunk_{os.getpid()}_{int(start_sec)}.wav")
    _ACTIVE_TEMP_FILES.add(temp_wav)

    # 格式化时长为保留2位小数的字符串
    dur_str = f"{float(duration_sec):.2f}"

    cmd = [
        ffmpeg_exe, "-y",
        "-ss", str(start_sec),
        "-t", dur_str,
        "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", "16000",
    ]

    if enable_denoise:
        if ffmpeg_version_major >= 5:
            af_filter = "highpass=f=80,lowpass=f=7600,afftdn=nf=-25,speechnorm=e=4:r=0.0001:l=1"
        else:
            # 旧版 FFmpeg 降级方案 (Item F1)
            af_filter = "highpass=f=80,lowpass=f=7600,afftdn=nf=-25"
        cmd.extend(["-af", af_filter])

    cmd.extend(["-acodec", "pcm_s16le", temp_wav])

    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )

    if res.returncode != 0:
        _ACTIVE_TEMP_FILES.discard(temp_wav)
        err_msg = res.stderr[-500:] if res.stderr else "未知 FFmpeg 错误"
        logger.error(f"FFmpeg 处理切片失败 (退出码 {res.returncode}):\n{err_msg}")
        raise RuntimeError(f"FFmpeg 处理切片失败 (exit code {res.returncode}): {err_msg.strip()}")

    return temp_wav


def scan_media_files(targets=None):
    """
    扫描待处理的音视频文件（支持指定文件、指定目录或当前目录）。
    自动识别音频与视频后缀名并去重排序。
    """
    media_files = []
    if targets:
        for t in targets:
            p = Path(t)
            if p.is_dir():
                for f in p.iterdir():
                    if f.is_file() and f.suffix.lower() in ALL_MEDIA_EXTS:
                        media_files.append(f)
            elif p.is_file():
                if p.suffix.lower() in ALL_MEDIA_EXTS:
                    media_files.append(p)
                else:
                    logger.warning(f"跳过不支持的文件格式: {p.name}")
            else:
                # 支持通配符匹配（如 *.m4a）
                matched = glob.glob(t)
                for m in matched:
                    mp = Path(m)
                    if mp.is_file() and mp.suffix.lower() in ALL_MEDIA_EXTS:
                        media_files.append(mp)
    else:
        for f in Path(".").iterdir():
            if f.is_file() and f.suffix.lower() in ALL_MEDIA_EXTS:
                media_files.append(f)

    # 去重并排序
    unique_files = sorted(list({str(p.resolve()): p for p in media_files}.values()), key=lambda x: x.name)
    return unique_files
