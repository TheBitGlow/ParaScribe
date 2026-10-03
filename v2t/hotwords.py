"""
v2t.hotwords - 领域热词加载与管理模块
实现热词文件静态缓存（Item G3）与媒体文件名特征词智能动态提取。
"""
import os
import re
import logging
from pathlib import Path

logger = logging.getLogger("ParaScribe")

# 模块级热词文件静态缓存，避免多文件批量转录重复磁盘 I/O (Item G3)
_HOTWORDS_FILE_CACHE = {}


def load_hotwords(audio_path="", hotwords_file="hotwords.txt"):
    """
    加载领域热词（支持热词文件缓存 + 从音视频文件名提取关键词）。
    返回以空格分隔的热词字符串，直接供给 FunASR SeACo-Paraformer 使用。
    """
    global _HOTWORDS_FILE_CACHE

    # 1. 缓存加载热词文件
    if hotwords_file not in _HOTWORDS_FILE_CACHE:
        file_hotwords = set()
        if os.path.exists(hotwords_file):
            try:
                with open(hotwords_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            file_hotwords.add(line)
                logger.debug(f"已从 {hotwords_file} 加载 {len(file_hotwords)} 个热词")
            except Exception as e:
                logger.warning(f"读取热词文件失败: {e}")
        _HOTWORDS_FILE_CACHE[hotwords_file] = file_hotwords

    # 复制基础热词集合，避免被单个文件的文件名词污染
    hotwords = set(_HOTWORDS_FILE_CACHE[hotwords_file])

    # 2. 从文件名智能提取中文词组加入临时热词
    if audio_path:
        stem = Path(audio_path).stem
        cn_words = re.findall(r'[\u4e00-\u9fa5]{2,8}', stem)
        stopwords = {"录音", "课堂", "课堂录音", "会议", "音频", "视频", "录像", "新建", "副本"}
        for w in cn_words:
            if w not in stopwords:
                hotwords.add(w)

    return " ".join(sorted(hotwords))
