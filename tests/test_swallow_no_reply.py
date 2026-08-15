"""[[NO_REPLY]] 真沉默 — v4.26 LLMResponse 对象兼容回归测试。"""

import asyncio
import ast
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger("astrbot-test")
logging.basicConfig(level=logging.CRITICAL)

class _SwallowLogger:
    @staticmethod
    def info(*args, **kwargs):
        pass

    @staticmethod
    def warning(*args, **kwargs):
        pass


class PlainPart:
    """模拟 BaseMessageComponent 的文本部件。"""

    def __init__(self, text: str = ""):
        self.text = text

    def __repr__(self):
        return f"PlainPart({self.text!r})"


class FakeMessageChain:
    """模拟 astrbot.core.message.message_event_result.MessageChain。"""

    def __init__(self, chain=None):
        self.chain = chain if chain is not None else []

    def get_plain_text(self) -> str:
        return "".join(getattr(p, "text", "") for p in self.chain)


class FakeLLMResponse:
    """模拟 astrbot.core.provider.entities.LLMResponse（v4.26+）。"""

    def __init__(self, role="assistant", result_chain=None, completion_text=None):
        self.role = role
        self.result_chain = result_chain
        if result_chain is not None:
            self._completion_text = ""
        else:
            self._completion_text = completion_text or ""

    @property
    def completion_text(self) -> str:
        if self.result_chain is not None:
            return self.result_chain.get_plain_text()
        return self._completion_text


def _load_swallow():
    """从 main.py 提取 swallow_no_reply 函数体（用假 Logger 替换 logger）执行。"""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "swallow_no_reply":
            fn = node
            break
    if fn is None:
        raise RuntimeError("swallow_no_reply not found in main.py")

    fn.decorator_list = []
    mod_source = ast.unparse(fn)
    ns = {"logger": _SwallowLogger, "AstrMessageEvent": object}
    exec(mod_source, ns)
    return ns["swallow_no_reply"]


_swallow = _load_swallow()


def _run(resp):
    return asyncio.new_event_loop().run_until_complete(
        _swallow(self=None, event=None, resp=resp)
    )


def test_llmresponse_pure_marker_clears_chain():
    """v4.26 纯 [[NO_REPLY]] → result_chain 清空，实现真沉默。"""
    resp = FakeLLMResponse(result_chain=FakeMessageChain([PlainPart("[[NO_REPLY]]")]))
    _run(resp)
    assert resp.result_chain.chain == []


def test_llmresponse_with_marker_part_is_removed_keeping_rest():
    """含标记的部件被移除，其余部件保留。"""
    resp = FakeLLMResponse(result_chain=FakeMessageChain(
        [PlainPart("好的[[NO_REPLY]]呀"), PlainPart("继续聊")]
    ))
    _run(resp)
    assert [p.text for p in resp.result_chain.chain] == ["继续聊"]


def test_llmresponse_no_marker_untouched():
    """无标记 → 原样返回。"""
    resp = FakeLLMResponse(result_chain=FakeMessageChain([PlainPart("你好")]))
    _run(resp)
    assert [p.text for p in resp.result_chain.chain] == ["你好"]


def test_llmresponse_no_chain_completion_text_only():
    """无 result_chain，纯标记走 completion_text 清空。"""
    resp = FakeLLMResponse(completion_text="[[NO_REPLY]]")
    _run(resp)
    assert resp._completion_text == ""


def test_llmresponse_no_chain_completion_text_keeps_remaining():
    """无 result_chain，标记外仍有内容 → 只去掉标记。"""
    resp = FakeLLMResponse(completion_text="给你说个事[[NO_REPLY]]")
    _run(resp)
    assert resp._completion_text == "给你说个事"


def test_legacy_list_api_compat():
    """旧版列表兼容：整体标记返回空列表。"""

    class OldPart:
        def __init__(self, text):
            self.text = text

    out = _run([OldPart("[[NO_REPLY]]")])
    assert out == []
    out = _run([OldPart("a"), OldPart("[[NO_REPLY]]")])
    assert len(out) == 1


def test_none_resp_is_passthrough():
    assert _run(None) is None