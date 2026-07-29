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
Canvas 引擎中间件插件架构 —— 在 Canvas.run() 执行链中注入可插拔中间件钩子。

解决的核心问题：
  Canvas.run() 的整个执行路径是硬编码的（node_started → _run_batch → 后处理），
  如果要添加功能（审计、监控、限流、日志增强等），必须直接修改 canvas.py 源码。
  本中间件架构将所有扩展点抽象为可插拔钩子，实现关注点分离。

设计思路：
  借鉴 Web 框架（Flask/Werkzeug）的中间件链模式，将 Canvas 的执行生命周期
  划分为 5 个钩子点，每个中间件可以在其中任一点注入自定义逻辑。

钩子点（MiddlewareHook）：
  - BEFORE_WORKFLOW: 工作流启动前，可做参数校验、权限检查、配额扣减
  - AFTER_NODE:      每个组件执行完毕后，可做结果审计、耗时记录、断点保存
  - BEFORE_TOOL:     Agent 调用工具前，可做参数脱敏、工具鉴权、速率限制
  - AFTER_TOOL:      工具返回结果后，可做结果脱敏、审计落盘、缓存写入
  - AFTER_WORKFLOW:  工作流结束后，可做资源清理、摘要生成、回调通知

与 Canvas.run() 的集成方式：
  在 Canvas.__init__() 中注册中间件管理器，run() 的关键位置通过
  await middleware_manager.execute(hook, context) 触发中间件链。
"""

import time
import logging
from enum import Enum
from typing import Any, Optional


class MiddlewareHook(Enum):
    BEFORE_WORKFLOW = "before_workflow"
    AFTER_NODE = "after_node"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    AFTER_WORKFLOW = "after_workflow"


class CanvasMiddleware:
    """
    中间件基类。所有自定义中间件必须继承此类。

    示例：
        class AuditMiddleware(CanvasMiddleware):
            async def on_node_finished(self, ctx, node_id, node_type, elapsed, error):
                await audit_db.write(node_id, node_type, elapsed, error)
    """

    @property
    def name(self) -> str:
        return self.__class__.__name__

    async def on_workflow_start(self, ctx: dict) -> Optional[dict]:
        return None

    async def on_node_finished(self, ctx: dict, node_id: str, node_type: str,
                                elapsed: float, error: Optional[str]) -> Optional[dict]:
        return None

    async def on_before_tool_call(self, ctx: dict, tool_name: str,
                                   arguments: dict) -> Optional[dict]:
        return None

    async def on_after_tool_call(self, ctx: dict, tool_name: str,
                                  arguments: dict, result: Any,
                                  elapsed: float, error: Optional[str]) -> Optional[dict]:
        return None

    async def on_workflow_finish(self, ctx: dict, success: bool,
                                  elapsed: float) -> Optional[dict]:
        return None


class MiddlewareManager:
    """
    中间件链管理器。按注册顺序依次执行中间件。

    用法：
        manager = MiddlewareManager(canvas_instance)
        manager.register(AuditMiddleware())
        manager.register(RateLimitMiddleware())

        # 在 Canvas.run() 中：
        await manager.execute(MiddlewareHook.BEFORE_WORKFLOW, {...})
        # ... 执行组件 ...
        await manager.execute(MiddlewareHook.AFTER_NODE, {...})
    """

    def __init__(self, canvas: Any):
        self._canvas = canvas
        self._middlewares: list[CanvasMiddleware] = []

    def register(self, *middlewares: CanvasMiddleware):
        for m in middlewares:
            self._middlewares.append(m)
            logging.info(f"[MiddlewareManager] Registered '{m.name}' ({len(self._middlewares)} total)")

    def unregister(self, name: str):
        before = len(self._middlewares)
        self._middlewares = [m for m in self._middlewares if m.name != name]
        removed = before - len(self._middlewares)
        if removed:
            logging.info(f"[MiddlewareManager] Unregistered '{name}'")

    def list_middlewares(self) -> list[str]:
        return [m.name for m in self._middlewares]

    async def execute(self, hook: MiddlewareHook, ctx: dict) -> dict:
        for mw in self._middlewares:
            st = time.perf_counter()
            try:
                if hook == MiddlewareHook.BEFORE_WORKFLOW:
                    result = await mw.on_workflow_start(ctx)
                elif hook == MiddlewareHook.AFTER_NODE:
                    result = await mw.on_node_finished(
                        ctx, ctx.get("node_id"), ctx.get("node_type"),
                        ctx.get("elapsed", 0), ctx.get("error")
                    )
                elif hook == MiddlewareHook.BEFORE_TOOL:
                    result = await mw.on_before_tool_call(
                        ctx, ctx.get("tool_name"), ctx.get("arguments", {})
                    )
                elif hook == MiddlewareHook.AFTER_TOOL:
                    result = await mw.on_after_tool_call(
                        ctx, ctx.get("tool_name"), ctx.get("arguments", {}),
                        ctx.get("result"), ctx.get("elapsed", 0), ctx.get("error")
                    )
                elif hook == MiddlewareHook.AFTER_WORKFLOW:
                    result = await mw.on_workflow_finish(
                        ctx, ctx.get("success", True), ctx.get("elapsed", 0)
                    )
                else:
                    result = None

                elapsed = time.perf_counter() - st
                if elapsed > 0.1:
                    logging.debug(f"[MiddlewareManager] {mw.name}.{hook.value} took {elapsed*1000:.1f}ms")

                if result is not None:
                    if isinstance(result, dict):
                        ctx.update(result)

            except Exception as e:
                logging.error(f"[MiddlewareManager] {mw.name}.{hook.value} failed: {e}")

        return ctx

    def clear(self):
        self._middlewares.clear()