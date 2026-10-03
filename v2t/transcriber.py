"""
v2t.transcriber - FunASR 模型加载、硬件设备探测与推理封装
支持 GPU CUDA / CPU 智能自适应(Item G1)、离线模型寻址配置化(Item C4)、模型加载计时(Item G2)。
"""
import os
import sys
import logging
from datetime import datetime

logger = logging.getLogger("ParaScribe")


def detect_device(user_choice="auto"):
    """
    智能硬件推理设备检测 (Item G1)。
    支持 auto (自适应), cpu (强制CPU), cuda (强制GPU)。
    当为 auto 且检测到显存 >= 4GB 的 NVIDIA 显卡时自动启用 CUDA。
    """
    if user_choice == "cpu":
        return "cpu"

    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            logger.info(f"检测到可用 GPU: {gpu_name} (显存: {gpu_mem:.1f} GB)")

            if user_choice == "cuda" or gpu_mem >= 4.0:
                logger.info("已自动选用 CUDA GPU 加速推理")
                return "cuda:0"
            else:
                logger.info(f"GPU 显存 ({gpu_mem:.1f} GB) 低于 4.0GB 建议阈值，回退至多核 CPU 模式")
                return "cpu"
        else:
            if user_choice == "cuda":
                logger.warning("未检测到可用的 CUDA 环境，回退至 CPU 模式")
            return "cpu"
    except Exception as e:
        logger.debug(f"探测 CUDA 设备失败: {e}")
        return "cpu"


def resolve_model_paths(config=None):
    """
    优先使用本地已缓存的模型绝对路径，实现 100% 离线秒级启动 (Item C4)。
    支持通过环境变量 MODELSCOPE_CACHE 或配置进行动态寻址。
    """
    cfg = config or {}
    custom_models = cfg.get("model_paths", {})

    cache_root = os.environ.get("MODELSCOPE_CACHE")
    if not cache_root:
        cache_root = os.path.join(os.path.expanduser("~"), ".cache", "modelscope")

    base = os.path.join(cache_root, "models", "iic")
    asr_local = os.path.join(base, "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch")
    vad_local = os.path.join(base, "speech_fsmn_vad_zh-cn-16k-common-pytorch")
    punc_local = os.path.join(base, "punc_ct-transformer_cn-en-common-vocab471067-large")

    asr_path = custom_models.get("asr") or (asr_local if os.path.exists(asr_local) else "paraformer-zh")
    vad_path = custom_models.get("vad") or (vad_local if os.path.exists(vad_local) else "fsmn-vad")
    punc_path = custom_models.get("punc") or (punc_local if os.path.exists(punc_local) else "ct-punc")

    all_cached = all(os.path.exists(p) for p in [asr_local, vad_local, punc_local])

    return {
        "asr": asr_path,
        "vad": vad_path,
        "punc": punc_path,
        "all_cached": all_cached
    }


def init_funasr_models(paths, config, device="cpu"):
    """
    初始化 FunASR AutoModel 并记录加载耗时 (Item G2)。
    """
    import torch
    from funasr import AutoModel

    cpu_threads = config.get("cpu_threads", 6)
    torch.set_num_threads(cpu_threads)
    logger.info(f"PyTorch 推理已配置: 设备={device}, CPU线程数={cpu_threads}")

    if paths["all_cached"]:
        logger.info("检测到完整本地模型缓存，直接从本地磁盘离线加载...")
    else:
        logger.info("部分模型未在本地缓存，将从 ModelScope 在线加载...")

    load_start = datetime.now()
    try:
        model = AutoModel(
            model=paths["asr"],
            vad_model=paths["vad"],
            vad_kwargs={"max_single_segment_time": config.get("vad_max_segment_ms", 30000)},
            punc_model=paths["punc"],
            device=device,
            ncpu=cpu_threads,
            trust_remote_code=config.get("model_trust_remote_code", False),
            disable_update=True
        )
        load_duration = (datetime.now() - load_start).total_seconds()
        logger.info(f"ASR + VAD + 标点恢复三模型初始化完成！(耗时 {load_duration:.1f} 秒)")
        return model
    except Exception as e:
        logger.error(f"模型加载失败: {e}")
        raise
