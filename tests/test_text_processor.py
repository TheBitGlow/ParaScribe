"""
tests/test_text_processor.py - 核心文本清洗与格式化单元测试集 (Item H1)
覆盖 A1(标点冲突消除)、A2(废词过滤与保护)、A3(空白清洗)、
A4(叠词保护与口吃修复)、A5(SRT行折叠)、A6(自定义替换) 及时间戳工具。
"""
import sys
import unittest
from pathlib import Path

# 将项目根目录添加到 sys.path
root_dir = str(Path(__file__).parent.parent.resolve())
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from v2t.text_processor import (
    clean_and_improve_text,
    format_ms,
    ms_to_srt_time,
    wrap_subtitle_text,
    split_into_sentences_keeping_punct,
    format_paragraphs_semantic
)

CONFIG_DEFAULT = {
    "remove_filler_words": True,
    "fix_stutter_repeats": True,
    "convert_chinese_numbers": False,
    "max_paragraph_sentences": 4,
    "max_line_length": 60,
    "semantic_pause_threshold_ms": 1500,
}


class TestTextProcessor(unittest.TestCase):

    def test_a1_remove_comma_period_conflict(self):
        """Item A1: 消除 '，。' 标点冲突"""
        result = clean_and_improve_text("你好，。世界", CONFIG_DEFAULT)
        self.assertNotIn("，。", result)
        self.assertIn("你好。世界", result)

    def test_a1_trailing_comma_gets_period(self):
        """Item A1: 句子尾部逗号在补充句号时应自动规整为单个句号"""
        result = clean_and_improve_text("你要学好几个月，", CONFIG_DEFAULT)
        self.assertTrue(result.endswith("。"))
        self.assertNotIn("，。", result)
        self.assertEqual(result, "你要学好几个月。")

    def test_a1_multiple_punctuation_cleanup(self):
        """Item A1: 多重顿号、问号、感叹号与逗号冲突规整"""
        result = clean_and_improve_text("真的吗？！，", CONFIG_DEFAULT)
        self.assertNotIn("，", result)
        self.assertTrue(result.endswith("？！"))

    def test_a2_filler_removal_basic(self):
        """Item A2: 基础口语废词过滤"""
        result = clean_and_improve_text("那个，人工智能很重要。", CONFIG_DEFAULT)
        self.assertNotIn("那个", result)
        self.assertEqual(result, "人工智能很重要。")

    def test_a2_expanded_filler_removal(self):
        """Item A2: 扩充的口语废词（如'就是吧'、'所以说'）过滤"""
        result = clean_and_improve_text("就是吧你要学好几个月。", CONFIG_DEFAULT)
        self.assertNotIn("就是吧", result)
        self.assertEqual(result, "你要学好几个月。")

        result2 = clean_and_improve_text("所以说，模型需要训练。", CONFIG_DEFAULT)
        self.assertNotIn("所以说", result2)
        self.assertEqual(result2, "模型需要训练。")

    def test_a2_filler_protection(self):
        """Item A2: 保护正常医学/专业词汇（如'呃逆'）不被误删"""
        result = clean_and_improve_text("患者出现频繁呃逆现象。", CONFIG_DEFAULT)
        self.assertIn("呃逆", result)
        self.assertEqual(result, "患者出现频繁呃逆现象。")

    def test_a3_space_cleanup(self):
        """Item A3: 全角空白、多重空格与中文标点两侧空格清理"""
        result = clean_and_improve_text("你好  世界", CONFIG_DEFAULT)
        self.assertNotIn("  ", result)

        result_punct = clean_and_improve_text("你好 ，世界 。", CONFIG_DEFAULT)
        self.assertEqual(result_punct, "你好，世界。")

    def test_a4_stutter_repeat_compression(self):
        """Item A4: 3次以上同字重复压缩为2个（保留叠词感）"""
        result = clean_and_improve_text("哈哈哈哈很有意思。", CONFIG_DEFAULT)
        self.assertIn("哈哈", result)
        self.assertNotIn("哈哈哈哈", result)

    def test_a4_two_char_phrase_dedup(self):
        """Item A4: 2字符短语重复消除"""
        result = clean_and_improve_text("然后然后我们开始测试。", CONFIG_DEFAULT)
        self.assertNotIn("然后然后", result)
        self.assertIn("然后我们开始测试。", result)

    def test_a4_aabb_phrase_protection(self):
        """Item A4: 保护 AABB 形式正常汉语成语/修辞不被误去重"""
        result = clean_and_improve_text("大家开开心心去上课。", CONFIG_DEFAULT)
        self.assertIn("开开心心", result)

    def test_a5_wrap_subtitle_text(self):
        """Item A5: SRT 字幕长度超限折行"""
        short_text = "这是一段很短的文字"
        self.assertEqual(wrap_subtitle_text(short_text, max_chars_per_line=21), short_text)

        long_text = "这是一个非常非常长而且需要被折行的字幕，我们希望它在标点处折行。"
        wrapped = wrap_subtitle_text(long_text, max_chars_per_line=21)
        self.assertIn("\n", wrapped)
        lines = wrapped.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("，"))

    def test_a6_custom_replacements(self):
        """Item A6: 自定义 ASR 纠错替换表"""
        rules = [("指定需要", "肯定需要"), ("岁岁右右", "岁左右")]
        result = clean_and_improve_text("这个指定需要学习。", CONFIG_DEFAULT, replacements=rules)
        self.assertIn("肯定需要", result)
        self.assertNotIn("指定需要", result)

    def test_format_ms(self):
        """时间戳 [MM:SS] 与 [HH:MM:SS] 格式化"""
        self.assertEqual(format_ms(0), "[00:00]")
        self.assertEqual(format_ms(65000), "[01:05]")
        self.assertEqual(format_ms(3665000), "[01:01:05]")

    def test_ms_to_srt_time(self):
        """标准 SRT 时间戳 HH:MM:SS,mmm 格式化"""
        self.assertEqual(ms_to_srt_time(0), "00:00:00,000")
        self.assertEqual(ms_to_srt_time(3661500), "01:01:01,500")

    def test_split_sentences(self):
        """句子切分与标点保留"""
        text = "第一句话！第二句话？第三句话。"
        sents = split_into_sentences_keeping_punct(text)
        self.assertEqual(len(sents), 3)
        self.assertEqual(sents[0], "第一句话！")
        self.assertEqual(sents[1], "第二句话？")
        self.assertEqual(sents[2], "第三句话。")

    def test_format_paragraphs_semantic_pause(self):
        """基于停顿间隔的语义换段"""
        sentence_info = [
            {"text": "这是第一句。", "start": 0, "end": 2000},
            {"text": "停顿超长这是第二句。", "start": 4000, "end": 6000},  # 停顿 2000ms > 1500ms
        ]
        paras = format_paragraphs_semantic(sentence_info, CONFIG_DEFAULT)
        self.assertIn("\n\n", paras)


if __name__ == "__main__":
    unittest.main()
