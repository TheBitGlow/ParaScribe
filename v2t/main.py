"""
v2t.main - Voice2Text 核心调度主流程
实现双层进度反馈(Item E1)、分片重叠切片与去重(Item B1)、断点版本控制(Item F3)、
以及各功能模块的完整端到端编排。
"""
import os
import sys
import gc
import json
from datetime import datetime
from pathlib import Path
from tqdm import tqdm

from v2t.config import (
    DEFAULT_CONFIG,
    PARTIAL_FORMAT_VERSION,
    VIDEO_EXTS,
    ALL_MEDIA_EXTS,
    parse_args,
    build_config,
    setup_logging
)
from v2t.ffmpeg_utils import (
    ensure_ffmpeg,
    get_audio_duration_sec,
    preprocess_audio_chunk,
    scan_media_files,
    cleanup_stale_temp_files,
    check_disk_space
)
from v2t.hotwords import load_hotwords
from v2t.text_processor import (
    format_ms,
    load_replacements,
    clean_and_improve_text,
    format_paragraphs,
    format_paragraphs_semantic,
    format_from_sentence_info
)
from v2t.exporters import (
    get_audio_info,
    export_srt,
    export_enhanced_docx,
    write_summary_json
)
from v2t.transcriber import (
    detect_device,
    resolve_model_paths,
    init_funasr_models
)


def main(args_list=None):
    """言迹 (ParaScribe) 核心调度主流程"""
    # 1. 配置加载与日志初始化 (优先解析参数，支持 --help 秒级响应)
    args = parse_args(args_list)
    config = build_config(args)
    logger = setup_logging(config.get("log_level", "INFO"))

    # 2. 注入 FunASR 零拷贝与内存优化补丁
    try:
        from funasr_patches import apply_funasr_memory_patches
        apply_funasr_memory_patches()
    except Exception:
        pass

    logger.info("=== 言迹 (ParaScribe) 高准度音视频转文字工具启动 ===")

    # 2. 启动时清理历史残留临时文件 (Item F2)
    cleanup_stale_temp_files()

    # 3. 检查并确保 FFmpeg 工具链 (Item C3, F1)
    ffmpeg_exe, ffprobe_exe = ensure_ffmpeg(config)

    # 4. 设备检测与模型初始化 (Item G1, G2, C4)
    target_device = detect_device(config.get("device", "auto"))
    paths = resolve_model_paths(config)
    try:
        model = init_funasr_models(paths, config, device=target_device)
    except Exception as e:
        logger.error(f"模型加载失败，程序退出: {e}")
        sys.exit(1)

    # 5. 扫描媒体文件
    media_files = scan_media_files(args.files)
    if not media_files:
        logger.warning(f"未找到待处理的音视频文件（支持格式: {', '.join(sorted(ALL_MEDIA_EXTS))}）")
        sys.exit(0)

    logger.info(f"发现 {len(media_files)} 个音视频文件:")
    for mf in media_files:
        info = get_audio_info(mf)
        media_type = "[视频]" if mf.suffix.lower() in VIDEO_EXTS else "[音频]"
        logger.info(f"  {media_type}: {info['filename']} ({info['size_mb']} MB)")

    # 6. 预加载自定义 ASR 纠错替换表 (Item A6)
    rep_rules = load_replacements(config.get("replacements_file", "replacements.txt"))
    if rep_rules:
        logger.info(f"已加载自定义 ASR 纠错替换规则: {len(rep_rules)} 条")

    success_count = 0
    failed_files = []
    file_results = []
    overall_start = datetime.now()

    # 7. 文件循环（外层进度条，Item E1）
    with tqdm(media_files, desc="总转录进度", position=0) as file_pbar:
        for media_path in file_pbar:
            media_path_obj = Path(media_path)
            media_name = media_path_obj.name
            stem = media_path_obj.stem

            # 输出目录设置与磁盘空间预检 (默认定向至 '已生成' 目录)
            target_out_dir = config.get("output_dir") or "已生成"
            out_dir = Path(target_out_dir)
            os.makedirs(out_dir, exist_ok=True)
            check_disk_space(out_dir, estimated_size_mb=100)

            txt_path = out_dir / f"{stem}.txt"
            timeline_path = out_dir / f"{stem}_带时间戳.txt"
            srt_path = out_dir / f"{stem}.srt"
            docx_path = out_dir / f"{stem}.docx"
            partial_path = out_dir / f"{stem}.partial.json"

            if txt_path.exists() and not args.overwrite:
                file_pbar.set_description(f"跳过已存在: {media_name}")
                logger.info(f"跳过（已完成，指定 --overwrite 强制重转）: {txt_path.name}")
                success_count += 1
                file_results.append({
                    "file": str(media_path),
                    "status": "skipped",
                    "outputs": [str(txt_path)]
                })
                continue

            audio_info = get_audio_info(media_path)
            file_pbar.set_description(f"处理中: {media_name} ({audio_info['size_mb']}MB)")

            # 加载领域热词 (带缓存 Item G3)
            hotword_str = load_hotwords(media_path, config.get("hotwords_file", "hotwords.txt"))
            if hotword_str:
                logger.info(f"已加载领域热词 ({len(hotword_str.split())} 个): {hotword_str[:80]}...")

            file_success = False
            for attempt in range(config.get("retry_attempts", 2) + 1):
                temp_wav = None
                try:
                    start_time = datetime.now()
                    total_dur = get_audio_duration_sec(media_path, ffmpeg_exe, ffprobe_exe)
                    chunk_sec = config.get("chunk_duration_sec", 900)
                    overlap_sec = config.get("chunk_overlap_sec", 10)  # Item B1

                    # 分片重叠步长规划 (Item B1)
                    step_sec = max(30, chunk_sec - overlap_sec)
                    if total_dur <= 0:
                        chunk_starts = [0]
                    else:
                        chunk_starts = []
                        pos = 0
                        while pos < total_dur:
                            chunk_starts.append(pos)
                            pos += step_sec

                    dur_desc = f"{total_dur/60:.1f} 分钟" if total_dur > 0 else "未知时长"
                    logger.info(
                        f"开始处理: {media_name} (总时长: {dur_desc}, 共划分为 {len(chunk_starts)} 个分片，重叠 {overlap_sec}秒)"
                    )

                    all_raw_texts = []
                    all_sentence_info = []
                    start_chunk_idx = 0

                    # 检查断点续传文件版本与合法性 (Item F3)
                    if partial_path.exists() and not args.overwrite:
                        try:
                            with open(partial_path, "r", encoding="utf-8") as pf:
                                partial_data = json.load(pf)
                                if partial_data.get("version") == PARTIAL_FORMAT_VERSION:
                                    chunks_done = partial_data.get("chunks_done", 0)
                                    if 0 < chunks_done <= len(chunk_starts):
                                        start_chunk_idx = chunks_done
                                        all_raw_texts = partial_data.get("all_raw_texts", [])
                                        all_sentence_info = partial_data.get("all_sentence_info", [])
                                        logger.info(f"检测到断点进度 (v{PARTIAL_FORMAT_VERSION})，从第 {chunks_done+1}/{len(chunk_starts)} 个分片恢复继续...")
                                else:
                                    logger.warning("断点文件版本不匹配，将从头重新处理该媒体")
                        except Exception as p_err:
                            logger.warning(f"读取断点文件失败，将从头重新处理: {p_err}")
                            start_chunk_idx = 0
                            all_raw_texts = []
                            all_sentence_info = []

                    # 分片循环（内层进度条，Item E1）
                    with tqdm(range(start_chunk_idx, len(chunk_starts)),
                              desc=f"  分片进度",
                              position=1,
                              leave=False,
                              unit="片") as chunk_pbar:

                        for idx in chunk_pbar:
                            c_start = chunk_starts[idx]
                            # 精确计算末尾分片时长，避免超出音轨结尾 (Item B2)
                            if total_dur > 0:
                                c_dur = min(chunk_sec, total_dur - c_start)
                                c_end = c_start + c_dur
                            else:
                                c_dur = chunk_sec
                                c_end = c_start + c_dur

                            logger.info(
                                f"  -> [{idx+1}/{len(chunk_starts)}] 正在切片与增强 "
                                f"{format_ms(c_start*1000)} ~ {format_ms(c_end*1000)} (时长: {c_dur:.1f}s)..."
                            )

                            temp_wav = preprocess_audio_chunk(
                                media_path,
                                ffmpeg_exe,
                                start_sec=c_start,
                                duration_sec=c_dur,
                                enable_denoise=config.get("enable_audio_preprocess", True),
                                ffmpeg_version_major=config.get("_ffmpeg_version_major", 99)
                            )

                            result = model.generate(
                                input=temp_wav,
                                batch_size_s=config.get("batch_size_s", 300),
                                hotword=hotword_str,
                                sentence_timestamp=True,
                                merge_vad=True,
                                merge_length_s=15
                            )

                            if temp_wav and os.path.exists(temp_wav):
                                try:
                                    os.remove(temp_wav)
                                except OSError:
                                    pass
                            temp_wav = None

                            if result:
                                c_text = result[0].get("text", "").strip()
                                if c_text:
                                    all_raw_texts.append(c_text)
                                c_sents = result[0].get("sentence_info", [])
                                offset_ms = int(c_start * 1000)

                                for s_item in c_sents:
                                    new_item = dict(s_item)
                                    new_item["start"] = s_item.get("start", 0) + offset_ms
                                    new_item["end"] = s_item.get("end", 0) + offset_ms

                                    # 重叠分片边界句子去重逻辑 (Item B1)
                                    if all_sentence_info:
                                        last_sent = all_sentence_info[-1]
                                        o_start = max(last_sent["start"], new_item["start"])
                                        o_end = min(last_sent["end"], new_item["end"])
                                        o_dur = max(0, o_end - o_start)
                                        sent_dur = max(1, new_item["end"] - new_item["start"])
                                        # 如果重合时长占比超过 50%，视为重叠切片重复句予以剔除
                                        if o_dur / sent_dur > 0.5:
                                            continue

                                    all_sentence_info.append(new_item)

                            # 更新分片进度条后缀 (Item E1)
                            cur_chars = sum(len(t) for t in all_raw_texts)
                            chunk_pbar.set_postfix({
                                "已识别字数": f"{cur_chars}",
                                "时间": format_ms(c_start * 1000)
                            })

                            # 保存断点文件 (带版本号 Item F3)
                            try:
                                with open(partial_path, "w", encoding="utf-8") as pf:
                                    json.dump({
                                        "version": PARTIAL_FORMAT_VERSION,
                                        "chunks_done": idx + 1,
                                        "total_chunks": len(chunk_starts),
                                        "all_raw_texts": all_raw_texts,
                                        "all_sentence_info": all_sentence_info,
                                        "total_dur": total_dur
                                    }, pf, ensure_ascii=False)
                            except Exception:
                                pass

                            del result
                            gc.collect()

                    # 多分片拼接时确保标点合理分隔 (Item B3)
                    raw_text = ""
                    for i, chunk_t in enumerate(all_raw_texts):
                        if i > 0 and raw_text and not raw_text.endswith(('。', '！', '？', '；', '，')):
                            raw_text += '，'
                        raw_text += chunk_t
                    raw_text = raw_text.strip()

                    sentence_info = all_sentence_info

                    if not raw_text:
                        logger.warning(f"转录结果为空: {media_name}")
                        continue

                    # 文本精细化后处理 (Item A1, A2, A3, A4, A6)
                    cleaned_text = clean_and_improve_text(raw_text, config, replacements=rep_rules)
                    output_format = config.get("output_format", "paragraphs")

                    # 主文稿格式化
                    if output_format == "raw":
                        final_text = cleaned_text
                    elif output_format == "timestamps" and sentence_info:
                        final_text = format_from_sentence_info(sentence_info, config, replacements=rep_rules)
                    else:
                        if sentence_info:
                            final_text = format_paragraphs_semantic(sentence_info, config, replacements=rep_rules)
                        else:
                            final_text = format_paragraphs(cleaned_text, config)

                    # 1. 写入主文稿
                    with open(txt_path, "w", encoding=config.get("output_encoding", "utf-8")) as f:
                        f.write(final_text)
                    logger.info(f"已生成主文稿: {txt_path.name}")
                    generated_files = [str(txt_path)]

                    # 2. 额外保存带毫秒级时间戳的文稿
                    ts_text = ""
                    if config.get("also_save_timestamp_version", True) and sentence_info:
                        ts_text = format_from_sentence_info(sentence_info, config, replacements=rep_rules)
                        with open(timeline_path, "w", encoding=config.get("output_encoding", "utf-8")) as f:
                            f.write(ts_text)
                        logger.info(f"已同步生成精确时间戳文稿: {timeline_path.name}")
                        generated_files.append(str(timeline_path))

                    # 3. 导出标准 SRT 字幕文件 (Item A5 折行)
                    if config.get("export_srt", True) and sentence_info:
                        if export_srt(sentence_info, srt_path, config, replacements=rep_rules):
                            logger.info(f"已同步生成标准 SRT 字幕: {srt_path.name}")
                            generated_files.append(str(srt_path))

                    # 4. 导出排版好的 Word (.docx) 文档
                    processing_time = (datetime.now() - start_time).total_seconds()
                    if config.get("export_word_docx", True):
                        if export_enhanced_docx(
                            docx_path=docx_path,
                            base_name=stem,
                            media_file=media_path,
                            media_info=audio_info,
                            total_dur=total_dur,
                            final_text=final_text,
                            ts_text=ts_text,
                            sentence_info=sentence_info,
                            processing_time=processing_time
                        ):
                            generated_files.append(str(docx_path))

                    # 清理断点续传文件
                    if partial_path.exists():
                        try:
                            os.remove(partial_path)
                        except OSError:
                            pass

                    # 控制台质量预览（前 300 字符）
                    preview_len = min(300, len(final_text))
                    preview_text = final_text[:preview_len].replace("\n", " ")
                    logger.info(f"=== 转录预览 ({preview_len}/{len(final_text)}字) ===\n{preview_text}...")
                    logger.info(f"[成功] 处理完成: {media_name} (耗时: {processing_time:.1f}秒)")

                    success_count += 1
                    file_success = True
                    file_results.append({
                        "file": str(media_path),
                        "status": "success",
                        "duration_sec": total_dur,
                        "processing_time_sec": processing_time,
                        "chars_count": len(final_text),
                        "outputs": generated_files
                    })
                    break

                except Exception as e:
                    error_msg = f"处理失败 {media_name} (尝试 {attempt + 1}): {str(e)}"
                    if attempt == config.get("retry_attempts", 2):
                        logger.error(error_msg)
                        failed_files.append((str(media_path), str(e)))
                        file_results.append({
                            "file": str(media_path),
                            "status": "failed",
                            "error": str(e)
                        })
                    else:
                        logger.warning(error_msg)
                finally:
                    if temp_wav and os.path.exists(temp_wav):
                        try:
                            os.remove(temp_wav)
                        except OSError:
                            pass

    total_elapsed = (datetime.now() - overall_start).total_seconds()

    # 8. 输出结构化任务总汇报 JSON (Item E2)
    target_out_dir = config.get("output_dir") or "已生成"
    out_dir = Path(target_out_dir)
    os.makedirs(out_dir, exist_ok=True)
    summary_path = out_dir / "parascribe_summary.json"
    summary_data = {
        "timestamp": datetime.now().isoformat(),
        "total_files": len(media_files),
        "success_count": success_count,
        "failed_count": len(failed_files),
        "total_elapsed_sec": round(total_elapsed, 2),
        "device_used": target_device,
        "results": file_results
    }
    write_summary_json(summary_path, summary_data)

    logger.info("================ 转录处理汇报 ================")
    logger.info(f"总计完成: {success_count}/{len(media_files)} 个媒体文件 (总耗时: {total_elapsed:.1f}秒)")
    if failed_files:
        logger.error(f"失败文件 ({len(failed_files)} 个):")
        for file_path, error in failed_files:
            logger.error(f"  - {file_path}: {error}")
    logger.info("==============================================")

    # 9. 可选自动打开输出目录 (Item E3)
    if config.get("auto_open_dir", False) and success_count > 0 and sys.platform == "win32":
        try:
            target_open = Path(config.get("output_dir") or "已生成").resolve()
            os.startfile(str(target_open))
        except Exception:
            pass


if __name__ == "__main__":
    main()
