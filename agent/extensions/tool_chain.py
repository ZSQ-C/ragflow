#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""
工具链编排引擎 —— 在 LLMToolPluginCallSession 基础上实现多工具组合调用。

解决的核心问题：
  当前的 LLMToolPluginCallSession 每次只执行单个工具调用，LLM 需要多次
  推理才能完成多步工具操作（A→B→C）。这导致：
  1. 串行执行耗时长（每次工具调用都需要一次 LLM 推理）
  2. 无法并发调用无依赖关系的工具
  3. 缺乏失败降级策略（某个工具不可用时，Agent 直接报错）

设计思路：
  ToolChain 将多个工具编排为有向无环图（DAG），支持三种执行模式：
  - SEQUENTIAL:  串行链式 A→B→C，前一个工具的输出作为后一个的输入
  - PARALLEL:    并发执行多个工具，结果合并后返回
  - FALLBACK:    主工具失败时自动降级到备用工具（可选链式）
  执行结果通过 OutputMerger 统一合并，与 _retrieve_chunks() 兼容。

与 LLMToolPluginCallSession 的集成方式：
  在 Agent 初始化时，将 ToolChain 实例也注册到 tools_map 中。
  当 LLM 调用链式工具时，ToolChain 自动解析依赖关系并分步执行。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class ChainMode(Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    FALLBACK = "fallback"


@dataclass
class ToolChainStep:
    """
    工具链中的一步。

    tool_name:     tools_map 中注册的工具名称
    arguments:     固定参数字典（变量部分由上游输出动态注入）
    depends_on:    依赖的上游步骤名称列表，用于构建执行 DAG
    fallback_to:   本步骤失败时的降级步骤名（仅 SEQUENTIAL 模式有效）
    timeout:       本步骤的执行超时（秒）
    """
    name: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    fallback_to: Optional[str] = None
    timeout: float = 30.0


@dataclass
class StepResult:
    name: str
    success: bool
    output: Any
    elapsed: float
    error: Optional[str] = None


class OutputMerger:
    """
    多工具输出合并器。
    将链式执行的结果合并为兼容 _retrieve_chunks() 的统一格式。

    合并策略：
      - CONCAT:   简单拼接（默认）
      - MERGE:    按 key 合并字典
      - SELECT:   从多个结果中选择得分最高的（由 score_fn 决定）
    """

    CONCAT = "concat"
    MERGE = "merge"
    SELECT = "select"

    @staticmethod
    def concat(results: list[StepResult]) -> dict:
        combined = []
        for r in results:
            if isinstance(r.output, dict):
                combined.append(r.output)
            elif isinstance(r.output, list):
                combined.extend(r.output)
            elif isinstance(r.output, str):
                combined.append({"content": r.output})
        return {"results": combined, "total": len(combined)}

    @staticmethod
    def merge(results: list[StepResult]) -> dict:
        merged = {}
        for r in results:
            if isinstance(r.output, dict):
                merged.update(r.output)
        return merged

    @staticmethod
    def select(results: list[StepResult], score_fn: Callable = lambda x: 1.0) -> Optional[StepResult]:
        if not results:
            return None
        return max(results, key=lambda r: score_fn(r.output))


class ToolChain:
    """
    工具链编排引擎。

    用法：
        chain = ToolChain(
            steps=[
                ToolChainStep(name="search", tool_name="google_search",
                              arguments={"q": "{sys.query}"}),
                ToolChainStep(name="crm", tool_name="crm_query",
                              arguments={"query_type": "customer_info",
                                         "customer_id": "{search.result}"},
                              depends_on=["search"]),
            ],
            mode=ChainMode.SEQUENTIAL,
            merger=OutputMerger.CONCAT,
        )

        # 注册到 tools_map
        tools_map["customer_pipeline"] = chain
        # LLM 调用 customer_pipeline 时，自动执行 search→crm 链
    """

    def __init__(
        self,
        steps: list[ToolChainStep],
        mode: ChainMode = ChainMode.SEQUENTIAL,
        merger: str = OutputMerger.CONCAT,
        name: str = "tool_chain",
        description: str = "",
        global_timeout: float = 120.0,
    ):
        self._steps = steps
        self._mode = mode
        self._merger = merger
        self._name = name
        self._description = description or f"ToolChain with {len(steps)} steps ({mode.value})"
        self._global_timeout = global_timeout
        self._tools_map: dict[str, Any] = {}
        self._result_cache: dict[str, StepResult] = {}

    def bind_tools(self, tools_map: dict[str, Any]):
        self._tools_map = tools_map

    @property
    def name(self) -> str:
        return self._name

    def get_meta(self) -> dict:
        params = {}
        for step in self._steps:
            for k, v in step.arguments.items():
                params[k] = {"type": "string", "description": f"Argument for step '{step.name}'"}

        return {
            "type": "function",
            "function": {
                "name": self._name,
                "description": self._description,
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": [],
                },
            },
        }

    def invoke(self, **kwargs) -> Any:
        return asyncio.run(self.invoke_async(**kwargs))

    async def invoke_async(self, **kwargs) -> Any:
        self._result_cache.clear()

        if self._mode == ChainMode.SEQUENTIAL:
            return await self._execute_sequential(**kwargs)
        elif self._mode == ChainMode.PARALLEL:
            return await self._execute_parallel(**kwargs)
        elif self._mode == ChainMode.FALLBACK:
            return await self._execute_fallback(**kwargs)
        else:
            raise ValueError(f"Unknown chain mode: {self._mode}")

    async def _execute_sequential(self, **kwargs) -> dict:
        """串行执行：按 steps 顺序执行，前一个的输出注入后一个的输入。"""
        context = dict(kwargs)
        results: list[StepResult] = []

        for step in self._steps:
            # 动态注入依赖步骤的输出
            resolved_args = self._resolve_arguments(step.arguments, context)

            st = time.perf_counter()
            success = False
            output = None
            error = None

            try:
                output = await self._call_tool(step.tool_name, resolved_args, step.timeout)
                success = True
            except Exception as e:
                error = str(e)
                logging.warning(f"[ToolChain] Step '{step.name}' failed: {e}")
                if step.fallback_to:
                    fallback_step = self._find_step(step.fallback_to)
                    if fallback_step:
                        fb_args = self._resolve_arguments(fallback_step.arguments, context)
                        try:
                            output = await self._call_tool(
                                fallback_step.tool_name, fb_args, fallback_step.timeout
                            )
                            success = True
                            logging.info(f"[ToolChain] Step '{step.name}' fallback to '{step.fallback_to}' succeeded")
                        except Exception as fb_e:
                            error = f"{error} | fallback failed: {fb_e}"

            elapsed = time.perf_counter() - st
            result = StepResult(
                name=step.name, success=success,
                output=output, elapsed=elapsed, error=error,
            )
            results.append(result)
            self._result_cache[step.name] = result

            # 将输出注入上下文供下游使用
            if success:
                context[step.name] = output
                context[step.name + ".result"] = output

        return self._merge_results(results)

    async def _execute_parallel(self, **kwargs) -> dict:
        """并行执行：解析 DAG 依赖，无依赖的步骤并发执行。"""
        # 构建依赖图
        all_step_names = {s.name for s in self._steps}
        deps: dict[str, set] = {}
        for step in self._steps:
            deps[step.name] = set(step.depends_on) & all_step_names

        completed: dict[str, StepResult] = {}
        results: list[StepResult] = []

        while len(completed) < len(self._steps):
            # 找出所有依赖已满足的步骤
            ready = [
                s for s in self._steps
                if s.name not in completed and deps[s.name].issubset(set(completed.keys()))
            ]
            if not ready and len(completed) < len(self._steps):
                raise RuntimeError(
                    f"Circular dependency detected in tool chain. "
                    f"Completed: {list(completed.keys())}"
                )

            # 并发执行 ready 的步骤
            async def run_step(step: ToolChainStep) -> StepResult:
                context = dict(kwargs)
                for dep_name, dep_result in completed.items():
                    context[dep_name] = dep_result.output
                    context[dep_name + ".result"] = dep_result.output
                resolved_args = self._resolve_arguments(step.arguments, context)
                st = time.perf_counter()
                try:
                    output = await self._call_tool(step.tool_name, resolved_args, step.timeout)
                    elapsed = time.perf_counter() - st
                    return StepResult(name=step.name, success=True, output=output, elapsed=elapsed)
                except Exception as e:
                    elapsed = time.perf_counter() - st
                    return StepResult(name=step.name, success=False, output=None, elapsed=elapsed, error=str(e))

            batch_results = await asyncio.gather(*[run_step(s) for s in ready], return_exceptions=True)
            for sr in batch_results:
                if isinstance(sr, StepResult):
                    completed[sr.name] = sr
                    results.append(sr)
                    self._result_cache[sr.name] = sr

        return self._merge_results(results)

    async def _execute_fallback(self, **kwargs) -> Any:
        """降级执行：尝试主工具链，失败后自动降级到备用。"""
        errors = []

        for step in self._steps:
            st = time.perf_counter()
            try:
                resolved_args = self._resolve_arguments(step.arguments, kwargs)
                output = await self._call_tool(step.tool_name, resolved_args, step.timeout)
                elapsed = time.perf_counter() - st
                result = StepResult(name=step.name, success=True, output=output, elapsed=elapsed)
                self._result_cache[step.name] = result
                return output
            except Exception as e:
                elapsed = time.perf_counter() - st
                errors.append(f"{step.tool_name}: {e}")
                result = StepResult(name=step.name, success=False, output=None, elapsed=elapsed, error=str(e))
                self._result_cache[step.name] = result
                continue

        raise RuntimeError(f"All fallback steps failed: {'; '.join(errors)}")

    def _resolve_arguments(self, args: dict, context: dict) -> dict:
        resolved = {}
        for k, v in args.items():
            if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
                ref_key = v[1:-1]
                if ref_key.startswith("sys."):
                    resolved[k] = context.get(ref_key, "")
                elif "." in ref_key:
                    step_name, attr = ref_key.split(".", 1)
                    step_result = self._result_cache.get(step_name)
                    if step_result and step_result.success:
                        resolved[k] = self._extract_attr(step_result.output, attr)
                    else:
                        resolved[k] = context.get(ref_key, "")
                else:
                    resolved[k] = context.get(ref_key, context.get(v, v))
            else:
                resolved[k] = context.get(k, v)
        return resolved

    def _extract_attr(self, obj: Any, attr: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(attr, obj)
        if hasattr(obj, attr):
            return getattr(obj, attr)
        return obj

    async def _call_tool(self, tool_name: str, args: dict, call_timeout: float) -> Any:
        tool = self._tools_map.get(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found in tools_map")

        if hasattr(tool, "invoke_async") and asyncio.iscoroutinefunction(tool.invoke_async):
            return await asyncio.wait_for(tool.invoke_async(**args), timeout=call_timeout)
        elif hasattr(tool, "invoke"):
            from common.misc_utils import thread_pool_exec
            return await asyncio.wait_for(
                thread_pool_exec(tool.invoke, **args), timeout=call_timeout
            )
        else:
            raise ValueError(f"Tool '{tool_name}' has neither invoke_async nor invoke")

    def _find_step(self, name: str) -> Optional[ToolChainStep]:
        for s in self._steps:
            if s.name == name or s.tool_name == name:
                return s
        return None

    def _merge_results(self, results: list[StepResult]) -> dict:
        successful = [r for r in results if r.success]
        if not successful:
            errors = {r.name: r.error for r in results if r.error}
            raise RuntimeError(f"All steps failed: {errors}")

        if self._merger == OutputMerger.CONCAT:
            return OutputMerger.concat(successful)
        elif self._merger == OutputMerger.MERGE:
            return OutputMerger.merge(successful)
        elif self._merger == OutputMerger.SELECT:
            selected = OutputMerger.select(successful)
            return {"selected": selected.output if selected else None}
        else:
            return OutputMerger.concat(successful)

    def get_step_result(self, step_name: str) -> Optional[StepResult]:
        return self._result_cache.get(step_name)