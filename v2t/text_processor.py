"""
v2t.text_processor - 文本精细化后处理与语义分段模块
实现口语废词过滤、ASR叠字修复、自定义词汇替换、标点冲突消除、时间戳格式化与语义分段。
"""
import os
import re
import logging

logger = logging.getLogger("ParaScribe")

# 模块级纠错替换表缓存 (Item A6)
_REPLACEMENTS_CACHE = {}

# 扩充的口语废词短语表 (Item A2)
DEFAULT_FILLER_PHRASES = [
    # 基础常见废词
    "那个", "这个", "就是说", "怎么说呢", "然后呢", "对吧",
    # 课堂与演讲高频口头禅
    "就是嘛", "就是吧", "所以说", "也就是说", "你看啊", "你看",
    "是不是", "对不对", "你知道吧", "你想啊", "你想想",
    "说白了", "说实话", "反正就是", "其实就是",
    "我跟你说", "跟大家说"
]


def load_replacements(replacements_file="replacements.txt"):
    """
    加载用户自定义的 ASR 纠错替换规则 (Item A6)。
    文件格式: 错误词 -> 正确词
    支持 # 注释与空行，带模块级内存缓存。
    """
    global _REPLACEMENTS_CACHE
    if not replacements_file:
        return []

    if replacements_file in _REPLACEMENTS_CACHE:
        return _REPLACEMENTS_CACHE[replacements_file]

    rules = []
    if os.path.exists(replacements_file):
        try:
            with open(replacements_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if " -> " in line:
                        src, dst = line.split(" -> ", 1)
                        if src.strip():
                            rules.append((src.strip(), dst.strip()))
            logger.debug(f"已从 {replacements_file} 加载 {len(rules)} 条纠错替换规则")
        except Exception as e:
            logger.warning(f"读取替换表文件失败: {e}")

    _REPLACEMENTS_CACHE[replacements_file] = rules
    return rules


def format_ms(ms):
    """将毫秒转换为 [MM:SS] 或 [HH:MM:SS] 格式"""
    total_seconds = int(max(0, ms) // 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"
    return f"[{minutes:02d}:{seconds:02d}]"


def ms_to_srt_time(ms):
    """将毫秒转换为标准 SRT 时间戳格式: HH:MM:SS,mmm"""
    total_ms = int(max(0, ms))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    seconds = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def wrap_subtitle_text(text, max_chars_per_line=21):
    """
    SRT 字幕行长度控制与智能折行 (Item A5)。
    将超长字幕折行为最多 2 行，每行不超过 max_chars_per_line 字符，优先在标点处断行。
    """
    if not text or len(text) <= max_chars_per_line:
        return text

    # 寻找中心附近的最佳断行点
    mid = len(text) // 2
    best_break = -1

    # 优先在标点后断行 (在 mid-6 到 mid+6 窗口中搜索)
    search_start = max(1, mid - 6)
    search_end = min(len(text) - 1, mid + 7)
    for i in range(search_start, search_end):
        if text[i] in '，、；：。！？ ':
            best_break = i + 1
            break

    # 若未找到标点，直接在中点切分
    if best_break == -1:
        best_break = mid

    line1 = text[:best_break].strip()
    line2 = text[best_break:].strip()
    if line2:
        return f"{line1}\n{line2}"
    return line1


def clean_and_improve_text(text, config=None, replacements=None):
    """
    文本精细化后处理流水线：
    1. 自定义 ASR 纠错替换表 (Item A6)
    2. 扩充的口语废词精准过滤 (Item A2)
    3. 合法叠词保护与口吃修复 (Item A4)
    4. 全角空格与标点空白清理 (Item A3)
    5. 彻底解决 '，。' 标点冲突 (Item A1)
    """
    if not text or not text.strip():
        return ""

    cfg = config or {}
    text = text.strip()

    # 1. 应用自定义 ASR 纠错替换表 (Item A6)
    rule_list = replacements
    if rule_list is None:
        rep_file = cfg.get("replacements_file", "replacements.txt")
        rule_list = load_replacements(rep_file)

    for src, dst in rule_list:
        text = text.replace(src, dst)

    # 2. 精准清理口语废词（保护“呃逆”等正常词汇） (Item A2)
    if cfg.get("remove_filler_words", True):
        user_phrases = cfg.get("filler_words") or DEFAULT_FILLER_PHRASES
        # 按长度降序排序以进行最长匹配
        sorted_phrases = sorted(user_phrases, key=len, reverse=True)
        filler_phrase_pattern = (
            r'(?:^|(?<=[，。！？；：\s]))(?:'
            + '|'.join(re.escape(p) for p in sorted_phrases)
            + r')[，、]?\s*'
        )
        text = re.sub(filler_phrase_pattern, '', text)

        # 独立或句首的单字语气助词
        filler_char_pattern = r'(?:^|(?<=[，。！？；：\s]))[呃嗯啊哎噢嗨]+(?=[，、。\s]|$)'
        text = re.sub(filler_char_pattern, '', text)

        # 保护非文字前后的孤立“呃”（严禁误删“呃逆”）
        text = re.sub(r'(?<![a-zA-Z\u4e00-\u9fa5])呃(?![逆a-zA-Z\u4e00-\u9fa5])', '', text)

    # 3. 修复口吃与叠字错乱，并豁免合法叠词 (Item A4)
    if cfg.get("fix_stutter_repeats", True):
        # 常见历史模式兜底
        text = re.sub(r'岁岁右右', '岁左右', text)
        text = re.sub(r'一一日', '一日', text)
        text = re.sub(r'标标求', '目标要求', text)

        # 同字连续出现 3 次及以上压缩为 2 个（保留“哈哈”、“慢慢”等合法叠词感知）
        text = re.sub(r'([\u4e00-\u9fa5])\1{2,}', r'\1\1', text)

        # 仅针对 2 字符的词组连续重复进行消除（如“然后然后”->“然后”，跳过 4 字以保护 AABB 结构）
        text = re.sub(r'([\u4e00-\u9fa5]{2})\1', r'\1', text)

    # 4. 可选：中文数字转换
    if cfg.get("convert_chinese_numbers", False):
        try:
            import cn2an
            text = cn2an.transform(text, "cn2an")
        except Exception:
            pass

    # 5. 空格与全角空白规整 (Item A3)
    text = re.sub(r'[ \t\u3000]{2,}', ' ', text)
    text = re.sub(r'\s*([，。！？；：、])\s*', r'\1', text)

    # 6. 标点符号规整与冲突消除 (Item A1 - 核心修复)
    # 6a. 首先剥离行尾可能遗留的非终止标点（逗号、顿号、分号等）
    text = re.sub(r'[，、；：]+$', '', text)
    # 6b. 消除各种多重标点冲突（例如 "，。" -> "。"、"。，" -> "。"）
    text = re.sub(r'[，、；：]+([。！？])', r'\1', text)
    text = re.sub(r'([。！？])[，、；：]+', r'\1', text)
    # 6c. 消除连续重复同类标点
    text = re.sub(r'([。，！？；：])\1+', r'\1', text)
    # 6d. 消除标点后的空白
    text = re.sub(r'([，。！？；：])\s+', r'\1', text)
    # 6e. 若尾部无有效终止标点，则补全句号
    if text and not re.search(r'[。！？]$', text):
        text = re.sub(r'[，、；：]+$', '', text)
        text += '。'

    return text.strip()


def split_into_sentences_keeping_punct(text):
    """按句末标点切分句子，同时保留句号、问号、感叹号"""
    if not text:
        return []
    parts = re.split(r'([。！？])', text)
    sentences = []
    for i in range(0, len(parts) - 1, 2):
        sent = parts[i].strip()
        punct = parts[i + 1]
        if sent:
            sentences.append(sent + punct)
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1].strip() + "。")
    return sentences


def format_paragraphs_semantic(sentence_info, config=None, replacements=None):
    """
    基于 VAD 语音停顿间隙 + 句子数量的双重语义分段策略 (Item A3 优化空白)。
    当两句之间停顿超过阈值（如 1500ms），表明说话人换话题或有明显思维停顿，强制换段。
    同时控制单段最大句子数与行长。
    """
    if not sentence_info:
        return ""

    cfg = config or {}
    pause_threshold_ms = cfg.get("semantic_pause_threshold_ms", 1500)
    max_sentences = cfg.get("max_paragraph_sentences", 4)
    max_line_length = cfg.get("max_line_length", 60)

    paragraphs = []
    current_sentences = []
    current_length = 0

    for i, item in enumerate(sentence_info):
        raw_text = item.get("text", "").strip()
        if not raw_text:
            continue
        cleaned = clean_and_improve_text(raw_text, cfg, replacements=replacements)
        if not cleaned:
            continue

        current_sentences.append(cleaned)
        current_length += len(cleaned)

        # 判断换段条件
        should_break = False
        if i + 1 < len(sentence_info):
            next_start = sentence_info[i + 1].get("start", 0)
            cur_end = item.get("end", next_start)
            gap = next_start - cur_end
            if gap >= pause_threshold_ms:
                should_break = True

        if len(current_sentences) >= max_sentences or current_length >= max_line_length * 2:
            should_break = True

        if should_break and current_sentences:
            para_text = "".join(current_sentences)
            # 段落内部全角空白与连续空格清洗 (Item A3)
            para_text = re.sub(r'\s{2,}', ' ', para_text)
            para_text = re.sub(r'\s*([，。！？；：、])\s*', r'\1', para_text)
            paragraphs.append(para_text.strip())
            current_sentences = []
            current_length = 0

    if current_sentences:
        para_text = "".join(current_sentences)
        para_text = re.sub(r'\s{2,}', ' ', para_text)
        para_text = re.sub(r'\s*([，。！？；：、])\s*', r'\1', para_text)
        paragraphs.append(para_text.strip())

    return "\n\n".join(paragraphs)


def format_paragraphs(text, config=None):
    """后备纯文本分段格式化（无时间戳信息时使用）"""
    cfg = config or {}
    max_sentences = cfg.get("max_paragraph_sentences", 4)
    max_line_length = cfg.get("max_line_length", 60)

    sentences = split_into_sentences_keeping_punct(text)
    paragraphs = []
    current_paragraph = []
    current_length = 0

    for sentence in sentences:
        sentence_length = len(sentence)
        if (len(current_paragraph) >= max_sentences or
                current_length + sentence_length > max_line_length * 2):
            if current_paragraph:
                paragraphs.append(''.join(current_paragraph))
                current_paragraph = []
                current_length = 0

        current_paragraph.append(sentence)
        current_length += sentence_length

    if current_paragraph:
        paragraphs.append(''.join(current_paragraph))

    return '\n\n'.join(paragraphs)


def format_from_sentence_info(sentence_info, config=None, replacements=None):
    """基于毫秒级时间戳构建带时间段标记的文稿"""
    if not sentence_info:
        return ""

    cfg = config or {}
    max_sentences = cfg.get("max_paragraph_sentences", 4)
    blocks = []
    cur_sents = []
    block_start_ms = None
    block_end_ms = None

    for item in sentence_info:
        raw_s = item.get("text", "").strip()
        if not raw_s:
            continue
        cleaned_s = clean_and_improve_text(raw_s, cfg, replacements=replacements)
        if not cleaned_s:
            continue

        start_ms = item.get("start", 0)
        end_ms = item.get("end", start_ms)

        if block_start_ms is None:
            block_start_ms = start_ms
        block_end_ms = end_ms
        cur_sents.append(cleaned_s)

        if len(cur_sents) >= max_sentences:
            ts_label = f"{format_ms(block_start_ms)} - {format_ms(block_end_ms)}"
            blocks.append(f"{ts_label} {''.join(cur_sents)}")
            cur_sents = []
            block_start_ms = None

    if cur_sents and block_start_ms is not None:
        ts_label = f"{format_ms(block_start_ms)} - {format_ms(block_end_ms)}"
        blocks.append(f"{ts_label} {''.join(cur_sents)}")

    return "\n\n".join(blocks)
