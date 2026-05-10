"""
工部 · Agent 抽象基类
======================

参考 Qwen-Agent Agent(ABC) + heurist BaseAgent 设计。

核心抽象：
- Agent(ABC)      : 基类，定义 run() / _run() 工作流
- FunctionCallAgent : 函数调用 Agent（工具驱动）
- ChatAgent       : 纯聊天 Agent（无工具）

生命周期钩子（参考 heurist）：
- on_message()    : 收到消息时
- before_run()    : _run() 执行前
- after_run()     : _run() 执行后

工具注册（参考 Qwen-Agent）：
- function_list   : 支持 str / dict / Tool 三种模式
- get_functions() : 返回 OpenAI function schema

使用方式：
    agent = FunctionCallAgent(
        name="鸿钧",
        function_list=["shell", "file_read"],
        llm={"model": "MiniMax-M2.7"},
    )
    for response in agent.run([{"role": "user", "content": "你好"}]):
        print(response)
"""

from __future__ import annotations

import asyncio
import copy
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

from .models import Message, MessageRole
from .tools import TOOL_REGISTRY, ToolResult


# ── 消息辅助 ────────────────────────────────────────────────────────────────

def _normalize_messages(messages: List[Union[Dict, Message]]) -> List[Message]:
    """统一转换为 Message 对象列表"""
    result = []
    for msg in messages:
        if isinstance(msg, dict):
            result.append(Message(**msg))
        else:
            result.append(msg)
    return result


# ── Agent 基类 ─────────────────────────────────────────────────────────────

class Agent(ABC):
    """
    Agent 抽象基类。

    定义：
    - run()      : 外部入口（流式/非流式）
    - _run()     : 内部工作流（子类实现）
    - _call_llm(): 调用 LLM

    生命周期钩子（可override）：
    - on_message(message)   : 收到每条消息时
    - before_run(messages)   : _run() 执行前
    - after_run(messages, responses): _run() 执行后
    """

    def __init__(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        system_message: Optional[str] = None,
        llm: Optional[Union[dict, Callable]] = None,
        **kwargs,
    ):
        self.name = name or self.__class__.__name__
        self.description = description or ""
        self.system_message = system_message or ""
        self.llm = llm  # LLM 配置或 LLM 对象

    def run(
        self,
        messages: List[Union[Dict, Message]],
        stream: bool = True,
        **kwargs,
    ) -> Iterator[List[Message]]:
        """
        外部入口：将消息转为 Message 对象，注入 system_message，调用 _run()。

        Args:
            messages: 消息列表
            stream: 是否流式输出
            **kwargs: 透传给 _run()

        Yields:
            响应消息列表（流式片段）
        """
        msgs = _normalize_messages(messages)

        # 注入 system_message
        if self.system_message:
            system_msg = Message(role=MessageRole.SYSTEM.value, content=self.system_message)
            if not msgs or msgs[0].role != MessageRole.SYSTEM.value:
                msgs.insert(0, system_msg)
            else:
                msgs[0].content = self.system_message + "\n\n" + msgs[0].content

        # 生命周期钩子：before_run
        self.before_run(msgs)

        # 调用子类 _run()
        for response in self._run(msgs, stream=stream, **kwargs):
            yield response

        # 生命周期钩子：after_run
        self.after_run(msgs)

    @abstractmethod
    def _run(
        self,
        messages: List[Message],
        stream: bool = True,
        **kwargs,
    ) -> Iterator[List[Message]]:
        """
        子类实现工作流。

        Args:
            messages: 标准化后的消息列表（含 system_message）
            stream: 是否流式

        Yields:
            响应片段（每片段一条 assistant 消息）
        """
        raise NotImplementedError

    def _call_llm(
        self,
        messages: List[Message],
        functions: Optional[List[Dict]] = None,
        stream: bool = True,
        **kwargs,
    ) -> Iterator[Message]:
        """
        调用 LLM 的标准接口。

        子类可override使用不同的 LLM 接入方式。
        默认实现假设 self.llm 是 dict 配置或 LLM 对象。
        """
        if self.llm is None:
            yield Message(role=MessageRole.ASSISTANT.value, content="【系统】LLM 未配置")
            return

        if isinstance(self.llm, dict):
            # 懒加载 LLM（子类可提前初始化）
            from .llm import get_chat_model
            llm_obj = get_chat_model(self.llm)
        else:
            llm_obj = self.llm

        # 调用 LLM
        if hasattr(llm_obj, "chat"):
            # 将 List[Message] 转换为 list[dict]
            msg_dicts = [m.to_openai_dict() for m in messages]
            # 流式
            if stream:
                response = ""
                for chunk in llm_obj.chat(messages=msg_dicts, functions=functions, stream=True, **kwargs):
                    if isinstance(chunk, str):
                        response += chunk
                    elif hasattr(chunk, "content"):
                        response += chunk.content
                    yield Message(role=MessageRole.ASSISTANT.value, content=response)
            else:
                resp = llm_obj.chat(messages=msg_dicts, functions=functions, stream=False, **kwargs)
                content = resp.content if hasattr(resp, "content") else str(resp)
                yield Message(role=MessageRole.ASSISTANT.value, content=content)
        else:
            yield Message(role=MessageRole.ASSISTANT.value, content="【系统】LLM 对象不支持 chat() 方法")

    # ── 生命周期钩子（可override）─────────────────────────────────────

    def on_message(self, message: Message) -> None:
        """收到消息时调用（默认空实现）"""
        pass

    def before_run(self, messages: List[Message]) -> None:
        """_run() 执行前调用（默认空实现）"""
        pass

    def after_run(self, messages: List[Message]) -> None:
        """_run() 执行后调用（默认空实现）"""
        pass


# ── 函数调用 Agent ─────────────────────────────────────────────────────────

class FunctionCallAgent(Agent):
    """
    函数调用 Agent。

    工作流（ReAct 模式）：
      user message → LLM (with functions) → tool_call? → 执行工具 → LLM (with result) → response

    特性：
    - 自动检测 LLM 返回的 function_call
    - 通过 ToolRegistry 执行工具
    - 支持多轮工具调用循环
    """

    def __init__(
        self,
        function_list: Optional[List[Union[str, Dict, Any]]] = None,
        max_tool_calls: int = 10,
        tool_threshold: float = 0.3,
        **kwargs,
    ):
        """
        Args:
            function_list: 工具列表（str/dict/Tool 三种模式）
            max_tool_calls: 单次请求最大工具调用次数（防止死循环）
            tool_threshold: 触发工具调用的最低置信度
        """
        super().__init__(**kwargs)
        self.function_list = function_list or []
        self.max_tool_calls = max_tool_calls
        self.tool_threshold = tool_threshold

        # 工具注册表（当前 Agent 实例独有）
        self._tool_registry = TOOL_REGISTRY
        self._function_map: Dict[str, Any] = {}

        # 加载 function_list
        if self.function_list:
            self._load_tools(self.function_list)

    def _load_tools(self, function_list: List[Union[str, Dict, Any]]) -> None:
        """加载工具到当前 Agent（参考 Qwen-Agent _init_tool）"""
        for item in function_list:
            if isinstance(item, str):
                # 字符串：查找已注册工具
                tool = self._tool_registry.get(item)
                if tool:
                    func = self._tool_registry.get_func(item)
                    self._function_map[item] = func
                else:
                    # 尝试作为别名注册
                    pass
            elif isinstance(item, dict):
                name = item.get("name")
                if name:
                    self._function_map[name] = None  # 等待后续绑定
            elif hasattr(item, "name") and hasattr(item, "call"):
                # BaseTool-like 对象
                self._function_map[item.name] = item

    def get_functions(self) -> List[Dict]:
        """获取当前 Agent 的 OpenAI function schemas"""
        schemas = []
        for name, func_or_obj in self._function_map.items():
            if hasattr(func_or_obj, "to_openai_schema"):
                schemas.append(func_or_obj.to_openai_schema())
            else:
                tool = self._tool_registry.get(name)
                if tool:
                    schemas.append(tool.to_openai_schema())
        return schemas

    def _run(
        self,
        messages: List[Message],
        stream: bool = True,
        **kwargs,
    ) -> Iterator[List[Message]]:
        """
        ReAct 工作流：
        1. 调用 LLM（附 functions schema）
        2. 检测 function_call
        3. 执行工具
        4. 将结果加入消息
        5. 再次调用 LLM 直到无 function_call
        """
        messages = copy.deepcopy(messages)
        tool_call_count = 0
        max_loops = self.max_tool_calls

        while tool_call_count < max_loops:
            # 调用 LLM（带 function schemas）
            fn_schemas = self.get_functions()
            responses = list(self._call_llm(messages, functions=fn_schemas, stream=False))

            for rsp in responses:
                messages.append(rsp)
                yield [rsp]

            # 检测是否需要工具调用
            last_msg = messages[-1] if messages else None
            if not last_msg or last_msg.role != MessageRole.ASSISTANT.value:
                break

            # 解析 function_call
            func_name, func_args = self._detect_function_call(last_msg)
            if not func_name:
                # 无工具调用，退出循环
                break

            # 执行工具
            tool_call_count += 1
            tool_result = self._call_tool(func_name, func_args)
            result_msg = Message(
                role=MessageRole.TOOL.value,
                content=str(tool_result.content if hasattr(tool_result, "content") else tool_result),
                name=func_name,
                tool_name=func_name,
            )
            messages.append(result_msg)
            yield [result_msg]

        # 最终回复已在上面的循环中 yield

    def _detect_function_call(self, message: Message) -> Tuple[Optional[str], Optional[dict]]:
        """
        从 assistant 消息中检测 function_call。

        支持两种格式：
        - function_call 属性（内部 LLM 格式）
        - content 中的 JSON 块

        Returns:
            (函数名, 参数 dict) 或 (None, None)
        """
        import json, re

        # 方式1：从 message.function_call（LLM 内部格式）
        if hasattr(message, "function_call") and message.function_call:
            fc = message.function_call
            name = fc.name if hasattr(fc, "name") else None
            args_str = fc.arguments if hasattr(fc, "arguments") else "{}"
            if name and args_str:
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    return name, args
                except json.JSONDecodeError:
                    return name, {}

        # 方式2：从 content 解析 ```json ... ``` 块
        content = message.content or ""
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(1))
                if isinstance(obj, dict) and "name" in obj:
                    return obj["name"], obj.get("arguments", {})
            except json.JSONDecodeError:
                pass

        return None, None

    def _call_tool(self, tool_name: str, args: Dict) -> ToolResult:
        """调用工具"""
        if tool_name not in self._function_map:
            # 尝试从全局注册表查找
            func = self._tool_registry.get_func(tool_name)
            if func:
                self._function_map[tool_name] = func
            else:
                return ToolResult(
                    tool_name=tool_name,
                    status="unavailable",
                    content=None,
                    error=f"工具 {tool_name} 不存在",
                )

        func_or_obj = self._function_map[tool_name]

        if hasattr(func_or_obj, "call"):
            # BaseTool-like 对象
            return func_or_obj.call(**args)
        elif callable(func_or_obj):
            # 普通函数
            try:
                if asyncio.iscoroutinefunction(func_or_obj):
                    # 异步函数需要事件循环（简化处理：同步执行）
                    result = func_or_obj(**args)
                else:
                    result = func_or_obj(**args)
                return ToolResult(
                    tool_name=tool_name,
                    status="success",
                    content=result,
                )
            except Exception as e:
                return ToolResult(
                    tool_name=tool_name,
                    status="failed",
                    content=None,
                    error=str(e),
                )
        else:
            return ToolResult(
                tool_name=tool_name,
                status="unavailable",
                content=None,
                error=f"工具 {tool_name} 未绑定执行函数",
            )


# ── 聊天 Agent（无工具）───────────────────────────────────────────────────

class ChatAgent(Agent):
    """
    纯聊天 Agent。

    工作流：user message → LLM → response
    无工具调用，适合简单问答。
    """

    def _run(
        self,
        messages: List[Message],
        stream: bool = True,
        **kwargs,
    ) -> Iterator[List[Message]]:
        for response in self._call_llm(messages, stream=stream, **kwargs):
            yield [response]
