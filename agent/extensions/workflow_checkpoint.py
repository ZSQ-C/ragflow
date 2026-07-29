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
工作流检查点与断点恢复系统。

解决的核心问题：
  Canvas.run() 的执行是无状态的——如果执行过程中进程崩溃或服务重启，
  所有已完成的组件工作全部丢失，工作流必须从头开始执行。
  对于多轮 Agent 推理（涉及多次 LLM 调用和工具调用），这种损失尤其惨重。

设计思路：
  - 检查点（Checkpoint）：在 Canvas.run() 的 after_node 钩子处定期保存
    当前执行上下文（已完成的组件输出、当前 path 索引、变量状态）
  - 断点恢复（Resume）：从最近一个检查点恢复执行，跳过已完成的组件
  - 存储引擎：支持 Redis（默认）和本地文件两种后端
  - 与 MiddlewareManager 配合：通过 AFTER_NODE 钩子自动触发检查点保存

数据流：
  Canvas.run()
    → 组件 A 执行完毕
    → MiddlewareManager.execute(AFTER_NODE)
      → 检查点中间件保存快照到 Redis
    → 组件 B 执行完毕
    → ...
    → 进程崩溃，恢复时从 Redis 读取最新快照
    → Canvas.resume_from_checkpoint() 重建上下文并跳转到中断位置
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class WorkflowCheckpoint:
    """
    工作流执行快照。

    workflow_id:  唯一标识一次工作流执行
    path_index:   当前执行到的 path 索引位置
    completed:    已执行完成的组件 ID 列表
    component_outputs:  已执行组件的输出（用于变量恢复）
    globals:      全局变量快照
    created_at:   快照创建时间
    version:      快照格式版本
    """
    workflow_id: str
    path_index: int
    completed: list[str]
    component_outputs: dict[str, dict[str, Any]]
    globals: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    version: int = 2


class CheckpointStore:
    """
    检查点存储引擎。

    支持两种后端：
      - Redis（默认）：利用现有 REDIS_CONN，设置 TTL 自动过期
      - Local：本地文件存储（适用于无 Redis 环境或测试场景）
    """

    def __init__(self, backend: str = "redis", ttl: int = 3600, local_dir: str = ""):
        self._backend = backend
        self._ttl = ttl
        self._local_dir = local_dir or os.path.join(
            os.path.dirname(__file__), ".checkpoints"
        )
        self._redis = None
        self._local_lock = asyncio.Lock()

        if backend == "redis":
            try:
                from rag.utils.redis_conn import REDIS_CONN
                self._redis = REDIS_CONN
            except Exception:
                logging.warning("[CheckpointStore] Redis unavailable, falling back to local")
                self._backend = "local"

    async def save(self, key: str, checkpoint: WorkflowCheckpoint) -> bool:
        try:
            data = {
                "workflow_id": checkpoint.workflow_id,
                "path_index": checkpoint.path_index,
                "completed": checkpoint.completed,
                "component_outputs": checkpoint.component_outputs,
                "globals": checkpoint.globals,
                "created_at": checkpoint.created_at,
                "version": checkpoint.version,
            }
            serialized = json.dumps(data, ensure_ascii=False, default=str)

            if self._backend == "redis" and self._redis:
                return await self._redis_save(key, serialized)
            else:
                return await self._local_save(key, serialized)
        except Exception as e:
            logging.error(f"[CheckpointStore] Save failed for {key}: {e}")
            return False

    async def load(self, key: str) -> Optional[WorkflowCheckpoint]:
        try:
            if self._backend == "redis" and self._redis:
                raw = await self._redis_load(key)
            else:
                raw = await self._local_load(key)

            if not raw:
                return None

            data = json.loads(raw)
            return WorkflowCheckpoint(
                workflow_id=data["workflow_id"],
                path_index=data["path_index"],
                completed=data["completed"],
                component_outputs=data.get("component_outputs", {}),
                globals=data.get("globals", {}),
                created_at=data.get("created_at", 0),
                version=data.get("version", 1),
            )
        except Exception as e:
            logging.error(f"[CheckpointStore] Load failed for {key}: {e}")
            return None

    async def delete(self, key: str) -> bool:
        try:
            if self._backend == "redis" and self._redis:
                return await self._redis_delete(key)
            else:
                return await self._local_delete(key)
        except Exception as e:
            logging.error(f"[CheckpointStore] Delete failed for {key}: {e}")
            return False

    async def _redis_save(self, key: str, serialized: str) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._redis.set(f"checkpoint:{key}", serialized, self._ttl)
        )

    async def _redis_load(self, key: str) -> Optional[str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._redis.get(f"checkpoint:{key}")
        )

    async def _redis_delete(self, key: str) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._redis.delete(f"checkpoint:{key}")
        )

    async def _local_save(self, key: str, serialized: str) -> bool:
        async with self._local_lock:
            safe_key = key.replace(":", "_").replace("/", "_")
            os.makedirs(self._local_dir, exist_ok=True)
            path = os.path.join(self._local_dir, f"{safe_key}.json")

            def _write():
                with open(path, "w", encoding="utf-8") as f:
                    f.write(serialized)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _write)
            return True

    async def _local_load(self, key: str) -> Optional[str]:
        async with self._local_lock:
            safe_key = key.replace(":", "_").replace("/", "_")
            path = os.path.join(self._local_dir, f"{safe_key}.json")

            def _exists():
                return os.path.exists(path)
            loop = asyncio.get_running_loop()
            exists = await loop.run_in_executor(None, _exists)
            if not exists:
                return None

            def _read():
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _read)

    async def _local_delete(self, key: str) -> bool:
        async with self._local_lock:
            safe_key = key.replace(":", "_").replace("/", "_")
            path = os.path.join(self._local_dir, f"{safe_key}.json")

            def _exists_and_delete():
                if os.path.exists(path):
                    os.remove(path)
                    return True
                return False
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _exists_and_delete)


class WorkflowCheckpointer:
    """
    工作流检查点管理器 —— 作为 CanvasMiddleware 注册到 MiddlewareManager 中。

    用法：
        checkpointer = WorkflowCheckpointer(
            store=CheckpointStore(backend="redis"),
            interval=3,  # 每执行 3 个组件保存一次检查点
        )

        # 作为中间件注册到 Canvas
        canvas.middleware_manager.register(checkpointer)

        # Canvas.run() 中通过 AFTER_NODE 钩子自动触发保存
        # 恢复时：
        checkpoint = await checkpointer.load("workflow_id_xxx")
        canvas = await checkpointer.resume(checkpoint, dsl_str, ...)

    设计要点：
      - 每 N 个组件保存一次快照（由 interval 控制），避免频繁 I/O
      - 快照包含：当前 path 索引、已完成组件列表、组件输出缓存、全局变量
      - 通过 Canvas 的 get_variable_value() / set_variable_value() 接口重建上下文
    """

    def __init__(
        self,
        store: Optional[CheckpointStore] = None,
        interval: int = 3,
    ):
        self._store = store or CheckpointStore()
        self._interval = interval
        self._canvas: Any = None
        self._component_count = 0
        self._checkpoint_key: str = ""

    def bind_canvas(self, canvas: Any):
        self._canvas = canvas
        self._checkpoint_key = f"workflow:{getattr(canvas, 'task_id', 'unknown')}"

    async def on_workflow_start(self, ctx: dict) -> Optional[dict]:
        self._component_count = 0
        if self._canvas:
            self._checkpoint_key = f"workflow:{getattr(self._canvas, 'task_id', 'unknown')}"
        return None

    async def on_node_finished(self, ctx: dict, node_id: str, node_type: str,
                                elapsed: float, error: Optional[str]) -> Optional[dict]:
        if not self._canvas:
            return None

        self._component_count += 1
        if self._component_count % self._interval != 0:
            return None

        checkpoint = self._capture_checkpoint()
        saved = await self._store.save(self._checkpoint_key, checkpoint)
        if saved:
            logging.info(
                f"[WorkflowCheckpointer] Saved checkpoint at component #{self._component_count} "
                f"({node_id}) for workflow {self._checkpoint_key}"
            )
        return None

    async def on_workflow_finish(self, ctx: dict, success: bool, elapsed: float) -> Optional[dict]:
        if self._checkpoint_key:
            await self._store.delete(self._checkpoint_key)
            logging.info(f"[WorkflowCheckpointer] Deleted checkpoint for {self._checkpoint_key}")
        return None

    def _capture_checkpoint(self) -> WorkflowCheckpoint:
        canvas = self._canvas
        completed = []
        component_outputs = {}

        path = getattr(canvas, "path", [])
        components = getattr(canvas, "components", {})

        for cid in path:
            comp = components.get(cid)
            if comp:
                obj = comp.get("obj")
                if obj:
                    output = obj.output()
                    if output:
                        completed.append(cid)
                        component_outputs[cid] = output

        return WorkflowCheckpoint(
            workflow_id=self._checkpoint_key,
            path_index=len(completed),
            completed=completed,
            component_outputs=component_outputs,
            globals=dict(getattr(canvas, "globals", {})),
        )

    async def save_checkpoint(self) -> bool:
        if not self._canvas:
            return False
        checkpoint = self._capture_checkpoint()
        return await self._store.save(self._checkpoint_key, checkpoint)

    async def load_checkpoint(self, workflow_key: str) -> Optional[WorkflowCheckpoint]:
        return await self._store.load(workflow_key)

    @staticmethod
    async def resume(
        checkpoint: WorkflowCheckpoint,
        canvas: Any,
    ) -> Any:
        """
        从检查点恢复工作流执行。

        resume 流程：
          1. 重建 Canvas 的 globals 变量
          2. 跳过 path 中已完成的组件（通过调用它们的 resume() 恢复输出）
          3. 从 path[checkpoint.path_index] 开始继续执行
        """
        logging.info(
            f"[WorkflowCheckpointer] Resuming workflow {checkpoint.workflow_id} "
            f"from path index {checkpoint.path_index}"
        )

        # 1. 恢复全局变量
        for key, val in checkpoint.globals.items():
            canvas.set_variable_value(key, val)

        # 2. 恢复已完成的组件输出
        components = getattr(canvas, "components", {})
        for cid, outputs in checkpoint.component_outputs.items():
            comp = components.get(cid, {}).get("obj")
            if comp:
                for key, val in outputs.items():
                    if val is not None:
                        comp.set_output(key, val)

        # 3. 截断 path，只保留未完成的部分
        path = getattr(canvas, "path", [])
        resume_idx = checkpoint.path_index
        if resume_idx < len(path):
            canvas.path = path[resume_idx:]
            logging.info(
                f"[WorkflowCheckpointer] Skipped {resume_idx} completed components, "
                f"resuming from {canvas.path[0]}"
            )
        else:
            canvas.path = []
            logging.warning("[WorkflowCheckpointer] All components already completed")

        return canvas