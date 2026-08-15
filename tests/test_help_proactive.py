"""求助答疑 — 检测求助（尤其编程）并主动答疑的触发链路。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.meta_thinking import parse_help_response, classify_help_request


class ClassifyHelpRequestTest(unittest.TestCase):
    def test_plain_chatter_not_help(self):
        for msg in ("今天晚饭吃了火锅", "明天陪我去超市", "哈哈哈", "在吗"):
            self.assertEqual(classify_help_request(msg), "", msg)

    def test_programming_help_is_program(self):
        cases = [
            "python 报错了，怎么解决",
            "js 一直报 error，救命",
            "npm install 报错怎么办",
            "docker 部署失败怎么搞",
            "这个代码 import 报错，有人会吗",
            "前端 vue 编译不过去",
        ]
        for msg in cases:
            self.assertEqual(classify_help_request(msg), "program", msg)

    def test_general_help_is_general(self):
        cases = [
            "求助，这个东西怎么用",
            "帮帮我，这道题不会做",
            "请问在线等很急",
            "怎么做好吃",
        ]
        for msg in cases:
            self.assertEqual(classify_help_request(msg), "general", msg)

    def test_too_short_not_help(self):
        self.assertEqual(classify_help_request("好"), "")
        self.assertEqual(classify_help_request(""), "")
        self.assertEqual(classify_help_request("  "), "")

    def test_program_word_marks_program(self):
        self.assertEqual(classify_help_request("运行报错 help!"), "program")
        self.assertEqual(classify_help_request("Python bug help!"), "program")
        self.assertEqual(classify_help_request("BUG 报错 help!"), "general")


class ParseHelpTest(unittest.TestCase):
    def test_active_parse(self):
        text = """内心：这是真求助，我能答
行动：主动答疑
是否需要联网：是
搜索关键词：python 报错 解决办法"""
        r = parse_help_response(text)
        self.assertEqual(r["action"], "主动答疑")
        self.assertTrue(r["need_web_search"])
        self.assertEqual(r["web_query"], "python 报错 解决办法")

    def test_pass_parse(self):
        text = """内心：闲聊，不用管
行动：不答
是否需要联网：否
搜索关键词：无"""
        r = parse_help_response(text)
        self.assertEqual(r["action"], "不答")
        self.assertFalse(r["need_web_search"])
        self.assertEqual(r["web_query"], "")

    def test_empty_input_safe(self):
        r = parse_help_response("")
        self.assertEqual(r["action"], "不答")
        self.assertFalse(r["need_web_search"])


if __name__ == "__main__":
    unittest.main()