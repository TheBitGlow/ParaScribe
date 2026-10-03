"""
funasr_patches.py - FunASR 运行时内存优化 Monkey-Patch
解决 16GB 内存机型在加载 Paraformer/VAD/Punc 多个大模型时的 OOM 崩溃问题。
确保在 pip upgrade funasr 或环境变动后仍然自动注入优化，防止内存翻倍。
"""
import sys
import logging
import gc

logger = logging.getLogger("ParaScribe.patches")

_PATCHES_APPLIED = False


def apply_funasr_memory_patches():
    """
    在模型加载前调用，运行时注入 mmap=True 和 assign=True 补丁。
    若 site-packages 已包含优化，也能安全平滑向下兼容。
    """
    global _PATCHES_APPLIED
    if _PATCHES_APPLIED:
        return True

    try:
        import torch
        import funasr.train_utils.load_pretrained_model as lpm

        def patched_load_pretrained_model(
            path: str,
            model: torch.nn.Module,
            ignore_init_mismatch: bool = True,
            map_location: str = "cpu",
            oss_bucket=None,
            scope_map=None,
            excludes=None,
            **kwargs,
        ):
            obj = model
            dst_state = obj.state_dict()

            if oss_bucket is None:
                try:
                    ori_state = torch.load(path, map_location=map_location, mmap=True)
                except Exception:
                    ori_state = torch.load(path, map_location=map_location)
            else:
                from io import BytesIO
                buffer = BytesIO(oss_bucket.get_object(path).read())
                ori_state = torch.load(buffer, map_location=map_location)

            src_state = ori_state
            src_state = src_state["state_dict"] if "state_dict" in src_state else src_state
            src_state = src_state["model_state_dict"] if "model_state_dict" in src_state else src_state
            src_state = src_state["model"] if "model" in src_state else src_state

            if scope_map is None:
                scope_map = []
            elif isinstance(scope_map, str):
                scope_map = scope_map.split(",")
            scope_map = list(scope_map) + ["module.", "None"]

            if excludes is not None and isinstance(excludes, str):
                excludes = excludes.split(",")

            for k in list(dst_state.keys()):
                if excludes is not None:
                    if any(k.startswith(k_ex) for k_ex in excludes):
                        continue

                k_src = k
                if scope_map is not None:
                    for i in range(0, len(scope_map), 2):
                        src_prefix = scope_map[i] if scope_map[i].lower() != "none" else ""
                        dst_prefix = scope_map[i + 1] if scope_map[i + 1].lower() != "none" else ""

                        if dst_prefix == "" and (src_prefix + k) in src_state:
                            k_src = src_prefix + k
                        elif k.startswith(dst_prefix) and k.replace(dst_prefix, src_prefix, 1) in src_state:
                            k_src = k.replace(dst_prefix, src_prefix, 1)

                if k_src in src_state:
                    if not (ignore_init_mismatch and dst_state[k].shape != src_state[k_src].shape):
                        dst_state[k] = src_state[k_src]

            try:
                flag = obj.load_state_dict(dst_state, strict=True, assign=True)
            except (TypeError, RuntimeError):
                flag = obj.load_state_dict(dst_state, strict=True)

            del dst_state, src_state, ori_state
            gc.collect()
            return flag

        lpm.load_pretrained_model = patched_load_pretrained_model
        _PATCHES_APPLIED = True
        logger.info("已成功激活 FunASR 零拷贝(mmap)与内存复用(assign)运行时优化补丁")
        return True
    except Exception as e:
        logger.warning(f"激活 FunASR 内存补丁时异常: {e}")
        return False
