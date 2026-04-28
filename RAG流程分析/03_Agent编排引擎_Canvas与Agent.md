# 03 — Agent 编排引擎：Canvas.run() 与 Agent._invoke_async()

> **文件位置**：`agent/canvas.py` L283-L667、`agent/component/agent_with_tools.py` L73-L378
> **核心定位**：RAGFlow Agent 系统的执行引擎，负责 DSL 解析→批量执行→流式事件推送的完整工作流编排
> **调用链**：前端发送消息 → `Canvas(dsl).run(query=...)` → `Agent._invoke_async()` → Tool Call → LLM 生成

---

## 一、核心总览（带逻辑关系）

### 1.1 核心定位

RAGFlow 的 Agent 系统采用 **JSON DSL + Canvas 执行引擎** 的架构。用户在前端拖拽组件搭建工作流后，前端将其序列化为 JSON DSL，后端通过 `Canvas` 类将其反序列化并执行。`Canvas` 继承自 `Graph`（负责 DSL 解析），新增了全局变量管理、对话历史、引用追踪和执行编排能力。其核心方法是 `run()`——一个**异步生成器**，通过 `yield` 事件字典（7 种类型：workflow_started、node_started、node_finished、message、message_end、user_inputs、workflow_finished）驱动前端实时更新状态。

`Agent` 组件（位于 `agent_with_tools.py`）是工作流中最关键的组件类型。它继承自 `LLM` 和 `ToolBase`，实现了完整的 OpenAI Function Calling 工具调用链路。

**适用场景**：
- 可视化编排的智能客服工作流（检索→Agent→回答）
- 多步骤复杂任务（检索+代码执行+网络搜索）
- 设计为子 Agent 被父 Agent 调用的层级 Agent 系统

### 1.2 整体流程串讲

**DSL 加载阶段（Canvas.load()）**：JSON DSL 中的 `components` 字典被反序列化。遍历每个组件，通过工厂函数 `component_class(name + "Param")` 创建参数对象，用 DSL 中 `params` 字段更新参数，然后调用 `param.check()` 校验参数合法性，最后通过 `component_class(name)(self, id, param)` 创建组件实例。同时加载全局变量（`globals`）、对话历史（`history`）、引用（`retrieval`）和变量（`variables`）。

**执行阶段（Canvas.run()）**：这是一个四阶段流程。**阶段1-初始化**：更新系统时间、生成 message_id、添加用户输入、重置所有组件、处理文件上传、递增会话轮次、yield `workflow_started` 事件。**阶段2-批量并行执行**：内部函数 `_run_batch()` 使用 `asyncio.Semaphore(5)` 控制并发数，对每个待执行的组件做依赖检查（变量的上游组件是否已完成），然后通过 `asyncio.gather` 并行执行。同步组件通过 `loop.run_in_executor` 放到线程池。**阶段3-后处理**：Message 组件做流式输出+ `<think>` 解析+TTS；错误组件根据 `exception_handler` 配置跳转或使用默认值；根据组件类型（Switch/Categorize/Loop/Iteration）决定路径推进方向。**阶段4-完成**：yield `workflow_finished` 事件，记录历史。

**Agent 内部（Agent._invoke_async()）**：如果无工具→直接调用 LLM 对话。有工具→检查下游是否有 Message 组件，有则用 `partial()` 延迟绑定流式函数 `stream_output_with_tools_async()`，等 Canvas 后处理阶段才真正执行（实现流式输出）。如果没有 Message 下游→非流式调用 `_generate_async()`。流式模式中，LLM 推理返回 tool_choice → `LLMToolPluginCallSession.tool_call_async()` 执行工具 → 结果注入 LLM 上下文 → 下一轮推理 → 最多 `max_rounds` 轮 → 最终流式输出答案。

---

## 二、模块拆分

### 模块1：Canvas 初始化与加载（L285-L322）

**作用**：设置全局变量（sys.query/sys.user_id/sys.conversation_turns等），加载 DSL 中的历史、变量、引用。是整个 Agent 系统的"启动模块"。

### 模块2：Canvas.run() 主循环（L375-L667）

**作用**：Agent 执行的核心编排器，异步生成器 yield 事件。包含_init_（初始化）→_run_batch（并行执行）→_后处理（流式输出+路径推进）→_完成 四个子阶段。

### 模块3：Agent.__init__() 工具绑定（L76-L147）

**作用**：构建 Agent 的工具库。依次加载配置的工具实例（通过工厂模式）、MCP 工具、创建 LLMBundle 并绑定 OpenAI Function Calling 格式的 tool_meta。

### 模块4：Agent._invoke_async() 主执行（L188-L259）

**作用**：Agent 的核心逻辑。无工具走纯对话、有工具+Message下游走流式、有工具无Message下游走非流式。

### 模块5：stream_output_with_tools_async() 流式（L261-L319）

**作用**：流式 LLM 生成 + 引用插入后处理 + 附件收集。包含多轮优化、Token 裁剪、引用提示词注入。

---

## 三、方法详细解析

### 3.1 Canvas.run()—— Agent 执行编排引擎（L375-L667）

#### 文字流程串讲

**阶段1-初始化（L376-L433）**：更新 `sys.date` 为当前时间，用 `time.perf_counter()` 记录启动时间（高精度计时器），生成 `message_id = get_uuid()`。调用 `add_user_input(query)` 将用户消息追加到 history。遍历所有组件调用 `reset(True)` 清空 outputs。处理文件上传：`await self.get_files_async()` 异步加载文件内容。递增 `sys.conversation_turns`。关键判断：`if not self.path or self.path[-1].lower().find("userfillup") < 0`——如果 path 为空或尾部不是 userfillup（说明是新对话），追加 "begin" 到 path，并创建新的引用槽位。最后 yield `workflow_started` 事件。

**阶段2-_run_batch()（L435-L482）**：内部异步函数，实现 5 路并行执行。`asyncio.Semaphore(5)` 限制最大并发。对 path[f:t] 范围内的每个组件，先做**依赖检查**：遍历 `get_input_elements()` 中的每个输入变量，如果变量引用了上游组件（`_cpn_id` 字段）且该组件不在 `self.path[:i]`（未完成执行），则从 path 中移除该组件。依赖满足的组件：Begin/UserFillUp 传入 `inputs` 参数；其他组件通过 `get_input()` 自动解析变量引用。检测组件的 `_invoke_async` 或 `_invoke` 是否是协程函数，决定走 await 还是线程池执行。所有任务通过 `asyncio.gather` 并行等待。

**阶段3-后处理（L500-L648）**：遍历刚执行完的组件。**Message 组件特殊处理**：如果 `output("content")` 是 `partial` 类型（Agent 延迟绑定的流式函数），调用 `() `执行获取流式生成器，遍历流式输出、每16字符做 TTS、解析 `<think>/</think>` 标签。**错误处理**：`exception_handler()` 返回 `{"goto": [...], "default_value": "..."}`——有 goto 则 path 扩展到错误处理组件；有 default_value 则 yield 默认消息。**路径推进**（核心流转逻辑）：Switch/Categorize→`_extend_path(output("_next"))`；Loop/Iteration→`_append_path(get_start())`（进入子组件）；LoopItem/IterationItem完成→`_extend_path(parent.downstream)`（回退）；ExitLoop→`_extend_path(parent.downstream)`；无downstream有父组件→回父组件继续；否则→`_extend_path(downstream)`。

**阶段4-完成（L650-L667）**：记录历史和全局变量，yield `workflow_finished` 事件。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `**kwargs`：query/user_id/files/inputs/webhook_payload等 |
| **核心逻辑** | 初始化全局变量→批量并行执行→流式后处理→路径状态机推进→事件推送 |
| **输出形式** | `async generator yield dict`：{event, message_id, task_id, data} |
| **底层关键依赖** | `asyncio.gather`（并行）、`asyncio.Semaphore`（并发控制）、`loop.run_in_executor`（线程池） |
| **关键代码片段** | `async with sem: await cpn_obj.invoke_async(**call_kwargs)` |

#### 特殊处理标注
- **partial 延迟绑定**：Agent 设置 `set_output("content", partial(stream_fn, ...))`，Canvas 在 Message 处理后 `output("content")()` 才执行——这是流式输出的核心机制
- **路径推进状态机**：7 种组件类型有各自的路径推进逻辑

### 3.2 Agent.__init__()—— 工具加载与绑定（L76-L147）

#### 文字流程串讲

构造函数先调用父类 `LLM.__init__()`，然后遍历 `self._param.tools` 配置列表。对每个工具，调用 `_load_tool_obj(cpn)` 通过工厂模式创建工具实例（如 Retrieval 组件），生成索引名 `{original_name}_{idx}` 防止重名冲突。

接着创建 `LLMBundle` 实例：`LLMBundle(tenant_id, chat_model_config, max_retries=..., max_rounds=max_rounds, verbose_tool_use=False)`。`max_rounds` 限制工具调用的最大轮次（默认5轮），防止无限循环。

然后构建 `self.tool_meta`——对每个工具调用 `get_meta()` 获取 OpenAI Function Calling 格式的元数据（含 name/description/parameters），深拷贝后更新 `function.name` 为索引名。如果有 MCP 工具，调用 `MCPServerService.get_by_id()` 获取 MCP 服务端配置，创建 `MCPToolCallSession` 实例，用 `mcp_tool_metadata_to_openai_tool()` 转换元数据格式。

创建回调函数 `self.callback = partial(self._canvas.tool_use_callback, id)`——每次工具调用后将结果记录到 Redis。创建 `LLMToolPluginCallSession(self.tools, self.callback)` 工具调用会话。如果有任何工具，调用 `self.chat_mdl.bind_tools(self.toolcall_session, self.tool_meta)` 绑定。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `canvas`（Canvas实例）、`id`（组件ID）、`param`（AgentParam，含tools/mcp/max_rounds/llm_id） |
| **核心逻辑** | 加载工具实例→创建LLMBundle→构建tool_meta→绑定到chat_mdl |
| **输出形式** | 完成实例初始化，self.tools/self.chat_mdl/self.toolcall_session 可用 |
| **底层关键依赖** | `LLMBundle`、`LLMToolPluginCallSession`、`MCPToolCallSession` |
| **关键代码片段** | `self.chat_mdl.bind_tools(self.toolcall_session, self.tool_meta)` |

#### 特殊处理标注
- **工具去重**：`indexed_name = f"{original_name}_{idx}"` 解决多工具同名冲突
- **MCP 协议**：让 Agent 可以调用任意第三方 MCP 服务的工具

### 3.3 Agent._invoke_async()—— 主执行逻辑（L188-L259）

#### 文字流程串讲

方法被 `@timeout(20*60)` 装饰器（默认 20 分钟超时）包裹。先 `check_if_canceled()` 检测任务是否被取消。

处理父 Agent 调用参数：如果有 `kwargs["user_prompt"]`（说明被父 Agent 作为工具调用），拼接 REASONING+CONTEXT+QUERY 三部分到 `self._param.prompts`。

**三分支路由**：
- **无工具**：直接 `return await LLM._invoke_async(self, **kwargs)` 走纯 LLM 对话
- **有 Message 下游 + 无异常跳转 + 无 structured output**：`self.set_output("content", partial(self.stream_output_with_tools_async, prompt, msg, ...))`——用 `partial` 延迟绑定流式函数，返回
- **无 Message 下游或有 structured output**：非流式调用 `_generate_async(msg)`

**Structured Output 处理**：如果有 output_schema（JSON Schema），用 `json_repair.loads()` 解析 LLM 生成的 JSON 字符串，失败则用 `max_retries` 次重试 `_force_format_to_schema_async()`。

**工具附件收集**：`_collect_tool_attachment_content()` 和 `_collect_tool_artifact_markdown()` 从工具输出中提取附件内容和 artifact 链接，追加到答案末尾。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `**kwargs`：user_prompt/reasoning/context（父Agent调用时） |
| **核心逻辑** | 取消检测→父Agent参数处理→三分支路由→structured output→附件收集 |
| **输出形式** | 流式模式：set_output("content", partial(...))；非流式：set_output("content", ans) |
| **底层关键依赖** | `_generate_async()`、`stream_output_with_tools_async()`、`json_repair.loads()` |
| **关键代码片段** | `self.set_output("content", partial(self.stream_output_with_tools_async, prompt, msg, ...))` |

#### 特殊处理标注
- **partial 延迟绑定**：`partial()` 将流式函数和参数打包但不执行，Canvas 在 Message 组件处理时才 `content()` 触发
- **json_repair**：对 LLM 生成的有瑕疵 JSON 做自动修复

### 3.4 LLMToolPluginCallSession.tool_call_async()—— 工具调用会话

#### 文字流程串讲

这是工具调用的实际执行者（`agent/tools/base.py L50-L77`）。接收工具名和参数，从 `self.tools_map` 取工具对象。

**三种执行模式**：
- **MCP 工具**：`await thread_pool_exec(tool_obj.tool_call, name, arguments, 60)`——同步方法放线程池，60 秒超时
- **异步工具**（有 `invoke_async` 协程）：`await tool_obj.invoke_async(**arguments)`——直接 await
- **同步工具**：`await thread_pool_exec(tool_obj.invoke, **arguments)`——放线程池避免阻塞事件循环

每次调用后通过 `self.callback(name, arguments, resp, elapsed_time)` 记录到 Redis 日志。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `name`（str，工具名）、`arguments`（dict，工具参数） |
| **核心逻辑** | 判断工具类型→选择执行方式→回调记录日志 |
| **输出形式** | `resp`（工具返回值，可以是 str/dict/bytes 等各种类型） |
| **底层关键依赖** | `thread_pool_exec()`、MCPToolCallSession |
| **关键代码片段** | `resp = await thread_pool_exec(tool_obj.invoke, **arguments)` |

#### 特殊处理标注
- **同步工具异步化**：所有同步调用通过 `thread_pool_exec` 在线程池中执行，避免阻塞 asyncio 事件循环

---

## 四、同类逻辑对比表

| 功能 | 核心流程 | 触发条件 | 输出 |
|------|----------|----------|------|
| **纯 LLM 对话** | `LLM._invoke_async()` | `not self.tools` | 直接返回文本答案 |
| **流式 Agent** | `partial(stream_output_with_tools_async)` | 有工具+Message下游 | async generator yield 文本块 |
| **非流式 Agent** | `_generate_async(msg)` | 有工具+无Message下游 | 完整答案字符串 |
| **Structured Output** | `json_repair.loads()` + 重试 | 有 output_schema | JSON dict |
| **MCP 工具** | `thread_pool_exec(mcp.tool_call)` | 工具是 MCPToolCallSession | 工具返回值 |
| **异步工具** | `await tool.invoke_async()` | 有 invoke_async 协程 | 工具返回值 |
| **同步工具** | `thread_pool_exec(tool.invoke)` | 仅有 invoke 方法 | 工具返回值 |

---

## 五、疑惑解答

**Q1：为什么 Agent 用 `partial()` 延迟执行而不是直接调用？**

因为流式输出需要 Canvas 的 Message 组件来逐块推送。如果 Agent 直接执行完再设 output，答案就是完整的一块了，前端无法实现打字机效果。通过 `partial()` 把生成器函数和参数打包，Canvas 在适当时候调用 `content()` 即可逐块处理。

**Q2：为什么同步工具要放线程池而不是直接调用？**

Direct sync call in a coroutine blocks the entire event loop. `thread_pool_exec` 把同步调用移到单独线程，asyncio 事件循环可以继续处理其他任务。否则一个慢速工具就会阻塞所有其他并发任务。

**Q3：`_run_batch()` 中的依赖检查为什么重要？**

工作流中组件 A 的输出是组件 B 的输入（通过 `{A@variable}` 引用）。如果 B 在 A 完成前执行，它拿不到 A 的输出值。依赖检查确保 B 只在实际就绪时才执行。

---

## 六、规范修正

- "工作流"和"Agent 画布"指同一概念
- "组件"和"节点"指同一概念（DSL 中的 component）
- "工具调用"和"Tool Call"、"Function Calling"指同一机制

---

## 七、可复现实操步骤

| 步骤 | 操作内容 | 最简代码 | 注意事项 |
|------|----------|---------|---------|
| 1 | 构建 DSL | 见上方 DSL JSON 示例 | components 须含 begin 和 at least one downstream |
| 2 | 创建 Canvas | `canvas = Canvas(json.dumps(dsl), tenant_id)` | dsl 需序列化为 JSON 字符串 |
| 3 | 执行工作流 | `async for ev in canvas.run(query="你好"): process(ev)` | 必须用 async for 消费事件 |
| 4 | 解析事件 | `if ev["event"]=="message": print(ev["data"]["content"])` | 7 种事件类型 |
| 5 | Agent 绑定工具 | `Agent(canvas, id, AgentParam(tools=[...], llm_id="qwen"))` | tools 是组件配置列表 |

---

## 八、关键模块总览

| 模块名称 | 负责功能 | 在流程中的核心作用 |
|----------|----------|-------------------|
| `Graph` | DSL 反序列化 | JSON→组件实例树 |
| `Canvas` | 执行编排 | 批量并行执行+路径状态机+事件推送 |
| `Agent` | 智能体 | Tool Call 路由+LLM 推理+流式生成 |
| `LLMToolPluginCallSession` | 工具调用 | 同步/异步/MCP 三种工具的统一调用入口 |
| `ComponentBase` | 组件基类 | invoke/invoke_async/变量解析/异常处理 |
| `Message` | 输出组件 | 流式输出+TTS+格式转换 |
| `Retrieval` | 检索工具 | 知识库检索+记忆检索 |
| `CodeExec` | 代码沙箱 | Python/NodeJS 安全执行 |
| `Categorize` | 意图路由 | LLM 分类→统计→跳转 |
| `Switch` | 条件分支 | 8 种运算符的条件判断跳转 |
