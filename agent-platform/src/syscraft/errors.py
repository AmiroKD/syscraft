"""异常 → 对外文案的统一出口。

未分类异常的 str() 可能携带绝对路径、SQL 片段、文件名、堆栈摘要——而 SSE error
事件与会话历史对任何登录用户可见，这些细节必须留在服务端日志里。只有抛出点
已经控制过措辞与内容的错误类型才透传原文，其余一律收敛成模糊文案。
"""

from __future__ import annotations

from syscraft.agent.llm import LLMError
from syscraft.tools.spring_client import SpringError

# str() 本来就是给人看的错误类型：SpringError 是产物侧的业务报错，
# LLMError 的消息在抛出点已拼成可读文案
_USER_FACING: tuple[type[BaseException], ...] = (SpringError, LLMError)

GENERIC = "处理失败，请稍后重试；若反复出现请联系管理员（详情见服务端日志）。"


def user_message(error: BaseException) -> str:
    """把异常映射成可对外展示的文案。"""
    if isinstance(error, _USER_FACING):
        text = str(error)
        return text if text else GENERIC
    return GENERIC
