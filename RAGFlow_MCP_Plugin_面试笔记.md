# RAGFlow MCP 与 Plugin 架构面试笔记

---

## 一、MCP（Model Context Protocol）架构

### 1.1 整体架构图

```
┌──────────────────────────────────────────────────────────────┐
│                    RAGFlow MCP 架构                           │
│                                                              │
│  RAGFlow 作为 MCP Server (对外暴露能力)                        │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  mcp/server/server.py                                    │ │
│  │  ├── SSE 传输 (http://host:9382/sse)                     │ │
│  │  └── Streamable HTTP 传输 (http://host:9382/mcp)         │ │
│  │  暴露工具: ragflow_retrieval                             │ │
│  │  输入: dataset_ids + question → 输出: 相关 Chunk          │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  RAGFlow 作为 MCP Client (消费外部能力)                       │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  common/mcp_tool_call_conn.py :: MCPToolCallSession      │ │
│  │  ├── 连接外部 MCP Server (SSE/Streamable HTTP)           │ │
│  │  ├── 统一封装为 OpenAI Function Calling 格式              │ │
│  │  └── 集成到 Agent 工具调用体系                             │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  管理 API (后端 + 前端)                                       │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  api/apps/mcp_server_app.py (REST API)                   │ │
│  │  api/db/services/mcp_server_service.py (数据库服务层)     │ │
│  │  web/src/pages/user-setting/mcp/ (前端管理页面)            │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 RAGFlow 作为 MCP Client（重点）

**核心类：MCPToolCallSession**

| 项目 | 内容 |
|------|------|
| 文件 | [common/mcp_tool_call_conn.py](file:///e:/AI/GitHub/RagFlow/common/mcp_tool_call_conn.py) |
| 行数 | 332 行 |
| 传输协议 | SSE（Server-Sent Events）+ Streamable HTTP |
| 生命周期 | 每个 MCP Server 连接一个独立事件循环 + 线程池 |

**代码核心逻辑：**

```python
# common/mcp_tool_call_conn.py - MCPToolCallSession 类

class MCPToolCallSession(ToolCallSession):
    def __init__(self, mcp_server, server_variables, custom_header):
        # 1. 创建独立事件循环（每个MCP一个线程）
        self._event_loop = asyncio.new_event_loop()
        self._thread_pool.submit(self._event_loop.run_forever)

        # 2. 启动 MCP Server 连接循环
        asyncio.run_coroutine_threadsafe(
            self._mcp_server_loop(), self._event_loop
        )

    async def _mcp_server_loop(self):
        # SSE 传输
        if self._mcp_server.server_type == MCPServerType.SSE:
            async with sse_client(url, headers) as stream:
                async with ClientSession(*stream) as client_session:
                    await asyncio.wait_for(
                        client_session.initialize(), timeout=5
                    )
                    await self._process_mcp_tasks(client_session)

        # Streamable HTTP 传输
        elif self._mcp_server.server_type == MCPServerType.STREAMABLE_HTTP:
            async with streamablehttp_client(url, headers) as (read, write):
                async with ClientSession(read, write) as client_session:
                    await self._process_mcp_tasks(client_session)
```

**在 Agent 组件中集成：**

```python
# agent/component/agent_with_tools.py#L100-L106
for mcp in self._param.mcp:
    _, mcp_server = MCPServerService.get_by_id(mcp["mcp_id"])
    tool_call_session = MCPToolCallSession(
        mcp_server, mcp_server.variables, custom_header
    )
    for tnm, meta in mcp["tools"].items():
        self.tool_meta.append(mcp_tool_metadata_to_openai_tool(meta))
        self.tools[tnm] = tool_call_session
```

### 1.3 RAGFlow 作为 MCP Server

**启动方式：**
```bash
# self-host 模式（默认）
uv run mcp/server/server.py --host=127.0.0.1 --port=9382 \
    --base-url=http://127.0.0.1:9380 --api-key=ragflow-xxxxx

# host 模式（多租户）
uv run mcp/server/server.py --host=127.0.0.1 --port=9382 \
    --base-url=http://127.0.0.1:9380 --mode=host
```

**暴露的 MCP 工具：**
```python
ragflow_retrieval:
  输入: {
    "dataset_ids": ["list of dataset IDs"],
    "document_ids": ["optional document IDs"],
    "question": "用户问题"
  }
  输出: 检索到的相关 Chunk 列表
```

**典型应用场景：**
- Claude Desktop 直接调用 RAGFlow 知识库
- Cursor IDE 集成企业内部文档检索
- 任意 MCP 客户端接入 RAGFlow 检索能力

### 1.4 MCP 数据库模型

```python
# api/db/db_models.py#L1086-L1097
class MCPServer(DataBaseModel):
    id          = CharField(primary_key=True)
    name        = CharField(max_length=255)       # MCP 名称
    tenant_id   = CharField(index=True)            # 租户 ID
    url         = CharField(max_length=2048)       # MCP Server URL
    server_type = CharField(max_length=32)         # SSE / STREAMABLE_HTTP
    description = TextField()                      # 描述
    variables   = JSONField(default=dict)          # 环境变量
    headers     = JSONField(default=dict)          # 请求头
```

### 1.5 MCP 完整代码清单

| 文件路径 | 功能 | 行数 |
|---------|------|------|
| [common/mcp_tool_call_conn.py](file:///e:/AI/GitHub/RagFlow/common/mcp_tool_call_conn.py) | MCP Client 核心：连接管理、工具调用 | 332 行 |
| [agent/component/agent_with_tools.py](file:///e:/AI/GitHub/RagFlow/agent/component/agent_with_tools.py) | Agent 组件集成 MCP 工具 | L100-L106 |
| [api/apps/mcp_server_app.py](file:///e:/AI/GitHub/RagFlow/api/apps/mcp_server_app.py) | MCP Server 管理 API（增删改查） | 300+ 行 |
| [api/db/services/mcp_server_service.py](file:///e:/AI/GitHub/RagFlow/api/db/services/mcp_server_service.py) | MCP Server 数据库服务层 | 100+ 行 |
| [api/db/db_models.py](file:///e:/AI/GitHub/RagFlow/api/db/db_models.py) | MCPServer 数据库模型 | L1086-L1097 |
| [web/src/pages/user-setting/mcp/index.tsx](file:///e:/AI/GitHub/RagFlow/web/src/pages/user-setting/mcp/index.tsx) | MCP 管理前端页面 | 300+ 行 |
| [mcp/server/server.py](file:///e:/AI/GitHub/RagFlow/mcp/server/server.py) | MCP 服务端实现 | 完整实现 |
| [test/testcases/test_web_api/test_mcp_server_app/test_mcp_server_app_unit.py](file:///e:/AI/GitHub/RagFlow/test/testcases/test_web_api/test_mcp_server_app/test_mcp_server_app_unit.py) | MCP 单元测试 | 完整测试 |
| [docs/develop/mcp/launch_mcp_server.md](file:///e:/AI/GitHub/RagFlow/docs/develop/mcp/launch_mcp_server.md) | 启动文档 | - |
| [docs/develop/mcp/mcp_client_example.md](file:///e:/AI/GitHub/RagFlow/docs/develop/mcp/mcp_client_example.md) | 客户端示例 | - |

### 1.6 面试高频问题与回答

**Q1：MCP 在 RAGFlow 中是怎么实现的？**

> RAGFlow 同时实现了 MCP Server 和 MCP Client 两端。
>
> **作为 Server**，外部工具（如 Claude Desktop、Cursor）可以通过 MCP 标准协议调用 RAGFlow 的 `ragflow_retrieval` 工具来检索知识库，支持 SSE 和 Streamable HTTP 两种传输模式。
>
> **作为 Client**，RAGFlow 的 Agent 组件可以通过 `MCPToolCallSession` 连接任意第三方 MCP Server，把外部能力作为 Agent 的工具来使用。每个 MCP 连接拥有独立的异步事件循环和线程池，互不干扰。
>
> 核心实现在 [common/mcp_tool_call_conn.py](file:///e:/AI/GitHub/RagFlow/common/mcp_tool_call_conn.py)，完整的 REST API 和前端管理页面也都实现了，用户可以在界面上增删改查 MCP Server 配置。

**Q2：MCP 协议有什么优势？**

> MCP 是 Anthropic 推出的 AI 工具标准化协议。相比传统的 REST API 集成，MCP 的标准化接口让工具接入成本大幅降低。RAGFlow 接入 MCP 后做到了"一次实现，所有 MCP 客户端都能用"——这也是 MCP 的核心价值。

---

## 二、Plugin（插件/Skill）架构

### 2.1 整体架构

```
agent/plugin/
├── __init__.py                   # 插件加载器入口
├── common.py                     # 通用工具函数
├── llm_tool_plugin.py            # LLMToolPlugin 基类定义
├── plugin_manager.py             # 插件管理器
└── embedded_plugins/
    └── llm_tools/
        ├── bad_calculator.py     # 示例插件（故意算错的计算器）
        └── [用户自定义插件]       # 用户可扩展
```

### 2.2 Skill 概念

在 [agent_context_engine.md](file:///e:/AI/GitHub/RagFlow/docs/basics/agent_context_engine.md#L34) 中定义：

> "Skills = best practices on when and how to use tools"
> Skills = 在什么场景下如何使用工具的最佳实践

**Context Engine 的三大组件：**
1. **Knowledge Index** - 知识库索引
2. **Memory Index** - 对话记忆索引
3. **Tool Retrieval** - 工具检索（Skills 的核心作用）

### 2.3 插件开发示例

```python
# agent/plugin/embedded_plugins/llm_tools/bad_calculator.py
class BadCalculatorPlugin(LLMToolPlugin):
    _version_ = "1.0.0"

    # LLM 调用的实际逻辑
    def invoke(self, a: int, b: int) -> str:
        return str(a + b + 100)  # 故意算错

    # 工具元数据（给 LLM 看的描述）
    @classmethod
    def get_metadata(cls) -> LLMToolMetadata:
        return {
            "name": "bad_calculator",
            "displayName": "$t:bad_calculator.name",
            "description": "A tool to calculate the sum of two numbers",
            "parameters": {
                "a": {"type": "number", "description": "第一个数", "required": True},
                "b": {"type": "number", "description": "第二个数", "required": True}
            }
        }
```

### 2.4 内置工具列表

| 工具文件 | 功能 | 说明 |
|---------|------|------|
| [agent/tools/retrieval.py](file:///e:/AI/GitHub/RagFlow/agent/tools/retrieval.py) | 知识库检索 | 最核心工具 |
| [agent/tools/google.py](file:///e:/AI/GitHub/RagFlow/agent/tools/google.py) | Google 搜索 | 网络搜索 |
| [agent/tools/tavily.py](file:///e:/AI/GitHub/RagFlow/agent/tools/tavily.py) | Tavily 搜索 | AI 搜索 |
| [agent/tools/duckduckgo.py](file:///e:/AI/GitHub/RagFlow/agent/tools/duckduckgo.py) | DuckDuckGo 搜索 | 免费搜索 |
| [agent/tools/pubmed.py](file:///e:/AI/GitHub/RagFlow/agent/tools/pubmed.py) | PubMed 检索 | 学术文献 |
| [agent/tools/arxiv.py](file:///e:/AI/GitHub/RagFlow/agent/tools/arxiv.py) | Arxiv 检索 | 学术论文 |
| [agent/tools/github.py](file:///e:/AI/GitHub/RagFlow/agent/tools/github.py) | GitHub 搜索 | 代码搜索 |
| [agent/tools/code_exec.py](file:///e:/AI/GitHub/RagFlow/agent/tools/code_exec.py) | 代码执行 | 沙箱执行 |
| [agent/tools/email.py](file:///e:/AI/GitHub/RagFlow/agent/tools/email.py) | 邮件发送 | 通知工具 |
| [agent/tools/wikipedia.py](file:///e:/AI/GitHub/RagFlow/agent/tools/wikipedia.py) | Wikipedia 查询 | 知识查询 |

### 2.5 面试高频问题与回答

**Q1：RAGFlow 的插件体系怎么设计的？**

> RAGFlow 的插件体系基于 `LLMToolPlugin` 基类，开发一个插件只需要两步：实现 `get_metadata()` 描述工具的用途和参数，实现 `invoke()` 写工具的执行逻辑。系统启动时通过 `plugin_manager.py` 自动扫描 `embedded_plugins` 目录并加载所有插件。这种设计让小团队也能快速扩展 Agent 的工具生态。

---

## 三、MCP vs Plugin 对比

| 维度 | MCP | Plugin/Skill |
|------|-----|-------------|
| **标准** | 标准化协议（MCP 1.0） | 自研框架 |
| **接入方式** | 运行时动态连接（SSE/HTTP） | 编译时静态加载 |
| **工具来源** | 任意第三方 MCP Server | 内置 + 用户手动安装 |
| **适用场景** | 动态接入外部能力（天气、数据库、文件） | 固定的内部能力（检索、搜索） |
| **协议格式** | JSON-RPC 2.0 + OpenAI Function Calling 格式 | 自定义 Python 类接口 |
| **核心优势** | 生态互通、热插拔 | 开发简单、无网络依赖 |

---

## 四、工具调用完整链路

```
用户输入
    │
    ▼
Canvas.run()                            # 启动工作流
    │
    ▼
Agent._invoke_async()                  # [agent_with_tools.py]
    │ 无工具 → 直接 LLM 对话
    │ 有工具 → 继续
    │
    ▼
chat_mdl (LLM 推理 + tool_choice)       # LLM 决定调用哪个工具
    │ 返回 tool_calls
    ▼
LLMToolPluginCallSession.tool_call_async()   # [agent/tools/base.py]
    │
    ├── MCP 工具 → MCPToolCallSession.tool_call()     # 走网络
    │              (thread_pool_exec 包装)
    ├── 异步工具 → await invoke_async()               # 走协程
    └── 同步工具 → thread_pool_exec(invoke)           # 走线程池
    │
    ▼
canvas.tool_use_callback()              # 记录工具调用日志 (Redis)
    │
    ▼
工具结果注入回 LLM 上下文                # 继续下一轮推理
    │
    ▼
... (最多 max_rounds 轮，默认 5)
    │
    ▼
set_output("content", final_answer)     # 输出最终结果
```

---

## 五、简历写法模板

### MCP 相关

> **MCP 协议集成（主导开发）**
> - **作为 MCP Server**：实现 MCP 标准协议（SSE + Streamable HTTP 双传输），对外暴露 `ragflow_retrieval` 检索工具，支持 Claude Desktop / Cursor 等 MCP 客户端直接调用
> - **作为 MCP Client**：实现 `MCPToolCallSession` 连接管理器，支持动态接入任意第三方 MCP Server，集成到 Agent 工具调用体系统一管理
> - **完整管理链路**：后端 REST API + 前端用户界面 + 数据库持久化，支持 MCP Server 的增删改查和变量配置

### Plugin 相关

> **Agent 工具插件体系（主导设计）**
> - 设计 LLM Tool Plugin 插件框架，支持热加载第三方工具
> - 实现 Tool Retrieval（工具检索），避免将所有工具描述塞入 Prompt
> - 内置 18+ 种工具（知识库检索、Bing 搜索、Tavily、PubMed、Arxiv、代码执行等）
> - 对外暴露 MCP 接口，让 LLM 工具生态可无限扩展

---

## 六、面试话术（完整版）

> **问：RAGFlow 的工具调用和 MCP 是怎么做的？**
>
> **答：** RAGFlow 支持两种工具扩展方式。
>
> 第一种是**内置 Plugin 插件体系**。在 `agent/plugin/` 目录下有一个插件加载器，启动时会递归扫描 `embedded_plugins` 文件夹，自动加载所有继承自 `LLMToolPlugin` 的插件类。像知识库检索、Google 搜索、Tavily 这些工具都是通过这种方式注册的。开发一个新插件只需要实现 `get_metadata()` 和 `invoke()` 两个方法，非常轻量。
>
> 第二种是 **MCP 协议接入**，这个技术含量更高。RAGFlow 同时实现了 MCP Server 和 MCP Client：
>
> 作为 Server，外部工具如 Claude Desktop、Cursor 可以通过 MCP 标准协议调用 RAGFlow 的知识库检索能力。启动方式很简单，跑一条命令就行，支持 SSE 和 Streamable HTTP 两种传输模式。
>
> 作为 Client，RAGFlow 的 Agent 可以连接任意第三方 MCP Server。核心实现在 [common/mcp_tool_call_conn.py](file:///e:/AI/GitHub/RagFlow/common/mcp_tool_call_conn.py)，`MCPToolCallSession` 类管理 MCP 连接的生命周期——每个连接有独立的异步事件循环和线程池。在 Agent 组件里，MCP 工具和内置工具通过统一的 `toolcall_session` 调度，LLM 通过 function calling 自动选择调用哪个工具，对 LLM 来说完全透明。
>
> 另外前端的 MCP 管理页面也实现了，用户可以在界面上添加、删除、配置 MCP Server 的 URL 和环境变量，不需要改代码。
>
> **总结：** Plugin 解决了"内置工具怎么开发"的问题，MCP 解决了"外部工具怎么接入"的问题，两者互补，共同构成了 RAGFlow 的 Agent 工具生态。

---

## 七、技术要点速查

| 关键数据 | 值 |
|---------|-----|
| MCP 客户端代码行数 | 332 行 |
| 支持传输协议 | SSE + Streamable HTTP |
| Agent 最大工具调用轮数 | 5 轮（可配置） |
| 内置工具数量 | 18+ |
| MCP Server 启动端口 | 9382 |
| 最小版本要求 | v0.18.0+ |

---

## 八、MCP 代码深度解析（按总结提示词模板）

### 8.1 核心总览（带逻辑关系）

**核心定位：**

MCP（Model Context Protocol）在 RAGFlow 中的实现分为三大模块：底层协议连接层（`common/mcp_tool_call_conn.py`）、Agent 工具集成层（`agent/component/agent_with_tools.py`）、以及管理 API 层（`api/apps/mcp_server_app.py` + `api/db/services/mcp_server_service.py`）。这三个模块自底向上构成了一个完整的 MCP 工具调用链路——底层负责与任意 MCP Server 建立 SSE 或 Streamable HTTP 连接并收发 JSON-RPC 2.0 消息；中间层将底层连接封装为与 RAGFlow 内置工具统一的 OpenAI Function Calling 格式，集成到 Agent 的 tool_choice 机制中；顶层通过 Quart REST API 对外暴露 MCP Server 的增删改查、工具列表获取、工具测试等管理能力，并配有前端页面供用户操作。

**解决的业务问题：** 传统 RAG 系统只能调用有限的内置工具（知识库检索、搜索引擎等），引入 MCP 协议后可以动态接入任意实现了 MCP 标准的第三方服务（如天气查询、数据库操作、文件系统等），工具生态从封闭走向开放。

**整体流程串讲：**

整体执行链路从用户在前端 MCP 管理页面创建一个 MCP Server 开始。用户在界面上填写 MCP Server 的 URL、传输类型（SSE 或 Streamable HTTP）、请求头和环境变量，提交后由 `api/apps/mcp_server_app.py` 中的 `/create` 路由接收请求，首先调用 `MCPServerService` 查询数据库校验是否有重名，通过后调用 `get_mcp_tools()` 工具函数连接该 MCP Server 执行 `list_tools` 操作获取工具列表，然后将工具列表持久化到 `variables["tools"]` 字段中存入 `mcp_server` 表。当用户在 Agent 工作流中配置该 MCP 工具时，`Agent.__init__()` 方法通过 `MCPServerService.get_by_id()` 从数据库加载 MCP Server 配置，创建 `MCPToolCallSession` 实例建立长期连接，然后调用 `mcp_tool_metadata_to_openai_tool()` 将 MCP 的工具元数据转换为 OpenAI Function Calling 格式，注册到 `chat_mdl.bind_tools()` 中。当 LLM 决定调用该工具时，`LLMToolPluginCallSession.tool_call_async()` 会检测到工具类型为 `MCPToolCallSession`，于是调用 `tool_obj.tool_call()`，该方法通过 `asyncio.run_coroutine_threadsafe()` 将调用请求投递到 MCP 连接专属的事件循环中，由 `_process_mcp_tasks()` 消费队列并调用 MCP Client 的 `call_tool()` 方法，结果沿原路返回到 LLM 上下文供下一轮推理使用。

### 8.2 模块拆分（固定顺序 + 关系说明）

#### 模块一：底层协议连接模块（`common/mcp_tool_call_conn.py`）

**作用与位置：** 这是整个 MCP 体系的基础层，负责与外部 MCP Server 建立连接、维护会话、收发消息。它在整体流程中处于最底层，其他所有模块都依赖它来与 MCP Server 交互。它定义了 `ToolCallSession` 协议接口和 `MCPToolCallSession` 实现类，并提供了全局的 session 管理和清理功能。

**与其他模块的关系：**
- 被 `agent/component/agent_with_tools.py` 中的 Agent 组件依赖，Agent 创建 `MCPToolCallSession` 实例来连接 MCP Server
- 被 `api/apps/mcp_server_app.py` 中的管理 API 依赖，用于创建 MCP 连接测试工具可用性
- 与 `agent/tools/base.py` 中的 `LLMToolPluginCallSession` 配合，后者作为统一调度器判断工具类型后调用 `MCPToolCallSession`

#### 模块二：Agent 工具集成模块（`agent/component/agent_with_tools.py`）

**作用与位置：** 这是 MCP 工具在 RAGFlow Agent 体系中的集成点。它在整体流程中处于中间层，负责将底层的 MCP 连接封装为 Agent 可调用的工具格式。Agent 初始化时加载 MCP 工具、`_invoke_async` 执行时触发 LLM 调用、`stream_output_with_tools_async` 流式输出结果。

**与其他模块的关系：**
- 依赖 `common/mcp_tool_call_conn.py` 创建 `MCPToolCallSession` 连接
- 依赖 `api/db/services/mcp_server_service.py` 查询 MCP Server 配置
- 依赖 `agent/tools/base.py` 中的 `LLMToolPluginCallSession` 做统一工具调度
- 被 Agent 工作流上游的 `Canvas` 模块调用

#### 模块三：工具调度模块（`agent/tools/base.py`）

**作用与位置：** 这是 MCP 工具与内置工具的统一切入点。`LLMToolPluginCallSession` 作为一个调度器，根据工具对象类型决定调用方式——MCP 工具走网络、异步工具走协程、同步工具走线程池。在整体流程中处于 Agent 之下的中间调度层。

#### 模块四：管理 API 模块（`api/apps/mcp_server_app.py`）

**作用与位置：** 这是 MCP Server 的管理接口层，对外提供 9 个 REST API 端点。在整体流程中处于最上层，是用户操作 MCP Server 的入口。

**与其他模块的关系：**
- 依赖 `api/db/services/mcp_server_service.py` 进行数据库操作
- 依赖 `common/mcp_tool_call_conn.py` 创建临时连接测试工具可用性
- 与前端页面 `web/src/pages/user-setting/mcp/` 配合构成完整管理链路

#### 模块五：数据库服务模块（`api/db/services/mcp_server_service.py`）

**作用与位置：** 这是 MCP Server 的数据库访问层，封装了对 `MCPServer` 模型的 CRUD 操作。在整体流程中属于数据持久化层，管理 API 模块依赖它来完成数据操作。

### 8.3 方法详细解析（强制 5 要素 + 文字流程串讲）

---

#### 8.3.1 `MCPToolCallSession.__init__()` — 初始化 MCP 客户端连接

**方法文字流程串讲：**

该方法在每次创建 MCP 客户端连接时被调用，传入 MCP Server 的数据库对象、环境变量和自定义请求头。它首先将自身注册到全局的 `_ALL_INSTANCES` 弱引用集合中，这样做是为了后续可以通过 `shutdown_all_mcp_sessions()` 优雅关闭所有活动连接。然后它创建一个全新的异步事件循环和一个最大工作线程数为 1 的线程池，将事件循环的 `run_forever` 提交到线程池中运行——这意味着每个 MCP 连接都拥有一个独立的线程和独立的事件循环，互不阻塞。最后通过 `asyncio.run_coroutine_threadsafe()` 将 `_mcp_server_loop()` 协程调度到该事件循环中启动。这样设计的原因在于 MCP 连接需要长期维持与外部 Server 的会话（SSE 长连接），不能占用主事件循环的线程，独立线程可以确保即使某个 MCP Server 不可用也不会阻塞主流程。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | `mcp_server: Any`（必填，MCP Server 数据库对象）、`server_variables: dict[str, Any] \| None`（选填，环境变量，默认 None）、`custom_header`（选填，自定义请求头，默认 None） |
| **核心逻辑** | 创建独立线程和事件循环，启动 MCP 连接循环 |
| **输出形式** | 无返回值；初始化成功后 `_mcp_server_loop` 在后台运行 |
| **底层关键依赖** | `asyncio.new_event_loop()`、`ThreadPoolExecutor`、`weakref.WeakSet`、`asyncio.run_coroutine_threadsafe()` |
| **关键代码片段** | `self._event_loop = asyncio.new_event_loop(); self._thread_pool.submit(self._event_loop.run_forever); asyncio.run_coroutine_threadsafe(self._mcp_server_loop(), self._event_loop)` |

**特殊处理标注：**
- 使用 `weakref.WeakSet` 注册实例，避免阻止垃圾回收
- 每个 MCP 连接独立线程隔离故障域

---

#### 8.3.2 `MCPToolCallSession._mcp_server_loop()` — MCP 连接循环

**方法文字流程串讲：**

这是 MCP 客户端的核心连接方法，在独立线程的事件循环中运行。方法开始时从 `mcp_server` 对象中取出 URL 和 headers，然后对 headers 中的占位符进行模板替换——这在配置文件或环境变量中使用了 `${VAR}` 格式的变量名时起作用。替换逻辑是：遍历 raw_headers 中的每个键值对，用 `Template.safe_substitute()` 将字符串中的占位符替换为 `server_variables` 中对应的值。custom_header 的变量替换则用自身作为替换源。替换完成后，根据 `server_type` 字段进入不同的连接分支：如果是 `SSE` 类型，通过 `sse_client(url, headers)` 建立 SSE 流连接，在 `async with` 块中创建 `ClientSession`，等待 5 秒的超时初始化；如果是 `STREAMABLE_HTTP` 类型，通过 `streamablehttp_client(url, headers)` 建立 HTTP 流连接，同样创建 `ClientSession` 并初始化。初始化成功后调用 `_process_mcp_tasks()` 进入任务处理循环。如果初始化超时、被取消或连接异常，会分别记录错误日志并通过 `_process_mcp_tasks(None, error_message)` 让所有等待中的任务返回错误。

该方法存在三种异常情况的分支判断：第一种是初始化超时（`asyncio.TimeoutError`），此时连接建立失败但 session 对象存在，所有任务会收到超时错误消息；第二种是任务被取消（`asyncio.CancelledError`），直接 return 结束循环；第三种是连接异常（`Exception`），比如 URL 不可达或认证失败，任务会收到连接失败的提示。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | 无显式参数；隐式依赖 `self._mcp_server`、`self._server_variables`、`self._custom_header` |
| **核心逻辑** | 连接 MCP Server 初始化会话，进入任务处理循环 |
| **输出形式** | 无返回值；成功后开始循环消费 `self._queue` 中的任务 |
| **底层关键依赖** | `sse_client()`（来自 `mcp.client.sse`）、`streamablehttp_client()`（来自 `mcp.client.streamable_http`）、`ClientSession`（来自 `mcp.client.session`）、`Template.safe_substitute()`（来自 `string`） |
| **关键代码片段** | `async with sse_client(url, headers) as stream: async with ClientSession(*stream) as client_session: await asyncio.wait_for(client_session.initialize(), timeout=5)` |

**特殊处理标注：**
- headers 支持 `${VARIABLE}` 模板替换，通过 `Template.safe_substitute()` 实现
- `Bearer` Token 会被自动过滤（`nv.strip().strip("Bearer")`）

---

#### 8.3.3 `MCPToolCallSession._process_mcp_tasks()` — 任务处理循环

**方法文字流程串讲：**

这是一个在 `_mcp_server_loop` 成功后进入的无限循环，负责消费任务队列 `self._queue` 中的 MCP 请求。循环以 1 秒超时的 `asyncio.wait_for(self._queue.get(), timeout=1)` 阻塞等待——这意味着如果没有任务，循环每秒空转一次，但可以在 `_close` 标志为 True 时快速退出。当获取到任务后，从队列元组中解包出 `mcp_task`（任务类型）、`arguments`（参数字典）和 `result_queue`（结果队列）。

方法首先检查 `client_session` 是否为 None 或 `error_message` 是否非空——这对应着初始化失败或连接异常的场景。如果任一条件成立，将 `ValueError(error_message)` 放入结果队列，跳过执行。

接下来根据 `mcp_task` 的类型分发：如果是 `"list_tools"`，调用 `client_session.list_tools()` 获取该 MCP Server 支持的工具列表；如果是 `"tool_call"`，调用 `client_session.call_tool(**arguments)` 执行工具调用；如果是未知类型，返回 `ValueError`。无论执行成功还是抛出异常，结果都通过 `await result_queue.put(r)` 放回结果队列，等待调用方获取。这种基于 asyncio.Queue 的生产者-消费者模式实现了跨线程的异步通信：调用方（在 Agent 主线程）通过 `_call_mcp_server()` 向队列放任务，连接线程消费任务并返回结果。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | `client_session: ClientSession \| None`、`error_message: str \| None` |
| **核心逻辑** | 循环消费任务队列，分发执行 list_tools 或 tool_call |
| **输出形式** | 不直接返回；结果写入 `result_queue` |
| **底层关键依赖** | `asyncio.Queue`、`ClientSession.list_tools()`、`ClientSession.call_tool()` |
| **关键代码片段** | `if mcp_task == "list_tools": r = await client_session.list_tools() elif mcp_task == "tool_call": r = await client_session.call_tool(**arguments)` |

**特殊处理标注：**
- `except asyncio.TimeoutError: continue` — 空等待时继续循环
- `_close` 标志控制循环退出，配合 `close()` 方法优雅关闭

---

#### 8.3.4 `MCPToolCallSession._call_mcp_server()` — 发送任务到 MCP 连接

**方法文字流程串讲：**

这是调用方（Agent 主线程）向 MCP 连接线程发送请求的入口方法。它首先检查 `_close` 标志，如果连接已关闭则直接抛出 `ValueError("Session is closed")`。然后创建一个新的 `asyncio.Queue` 作为结果队列，将任务类型、参数和结果队列打包为元组通过 `await self._queue.put()` 放入 MCP 连接的主任务队列。接着调用 `asyncio.wait_for(results.get(), timeout=request_timeout)` 等待结果——这意味着调用方最多等待 `request_timeout` 秒。如果超时，抛出 `asyncio.TimeoutError` 并携带提示信息；如果结果是一个 `Exception` 实例（即 MCP Server 返回了错误），直接 raise 该异常；如果成功，返回 `CallToolResult` 对象。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | `task_type: MCPTaskType`（必填，`"list_tools"` 或 `"tool_call"`）、`request_timeout: float \| int`（选填，默认 8 秒）、`**kwargs`（选填，tool_call 时的 name 和 arguments） |
| **核心逻辑** | 向任务队列放入请求，等待结果队列返回 |
| **输出形式** | 成功返回 `CallToolResult \| ListToolsResult`，失败抛出异常 |
| **底层关键依赖** | `asyncio.Queue.put()`、`asyncio.Queue.get()`、`asyncio.wait_for()` |
| **关键代码片段** | `await self._queue.put((task_type, kwargs, results)); result = await asyncio.wait_for(results.get(), timeout=request_timeout); if isinstance(result, Exception): raise result` |

---

#### 8.3.5 `MCPToolCallSession._call_mcp_tool()` — 执行 MCP 工具调用

**方法文字流程串讲：**

这是对 `_call_mcp_server()` 的专门封装，专门用于 `"tool_call"` 类型的任务。它调用 `_call_mcp_server("tool_call", name=name, arguments=arguments, request_timeout=request_timeout)`，得到 `CallToolResult` 对象后检查 `result.isError` 标志。如果 MCP Server 返回了错误（`isError=True`），返回错误信息字符串；如果返回的是文本内容（`TextContent`），取出 `content[0].text` 返回；如果内容类型不支持，返回类型提示。需要注意这里只处理 `TextContent` 类型，图片等二进制内容尚未支持。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | `name: str`（必填，工具名称）、`arguments: dict[str, Any]`（必填，工具参数）、`request_timeout: float \| int`（选填，默认 10 秒） |
| **核心逻辑** | 调用 MCP 工具，解析返回结果 |
| **输出形式** | `str` 类型的结果文本 |
| **底层关键依赖** | `CallToolResult`、`TextContent`（来自 `mcp.types`） |
| **关键代码片段** | `if result.isError: return f"MCP server error: {result.content}"; if isinstance(result.content[0], TextContent): return result.content[0].text` |

**特殊处理标注：**
- `request_timeout` 默认值 10 秒，比 `_call_mcp_server` 的默认 8 秒更长，给工具执行留出余量
- 仅支持文本类型结果，二进制内容未实现

---

#### 8.3.6 `MCPToolCallSession.tool_call()` — 同步工具调用入口

**方法文字流程串讲：**

这是对外暴露的同步接口，被 RAGFlow 的统一工具调度器 `LLMToolPluginCallSession.tool_call_async()` 通过线程池调用。由于 `_call_mcp_tool()` 是异步方法，而 MCP 连接运行在另一个线程的独立事件循环中，这里不能直接 `await`。解决方案是使用 `asyncio.run_coroutine_threadsafe()` 将 `_call_mcp_tool()` 协程调度到 MCP 连接的事件循环中去执行，然后通过 `future.result(timeout=timeout)` 阻塞等待结果。如果超时返回错误提示，如果异常返回异常信息。这种设计确保了无论调用方在哪个线程，都能安全地与 MCP 连接互通。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | `name: str`（必填）、`arguments: dict[str, Any]`（必填）、`timeout: float \| int`（选填，默认 10 秒） |
| **核心逻辑** | 将异步工具调用调度到 MCP 事件循环，阻塞等待结果 |
| **输出形式** | 成功返回 `str`，失败返回错误字符串（不抛异常） |
| **底层关键依赖** | `asyncio.run_coroutine_threadsafe()`、`Future.result()` |
| **关键代码片段** | `future = asyncio.run_coroutine_threadsafe(self._call_mcp_tool(name, arguments), self._event_loop); return future.result(timeout=timeout)` |

**特殊处理标注：**
- 失败时不抛异常，返回错误字符串，避免 Agent 流程崩溃
- `if self._close: return "Error: Session is closed"` 快速失败

---

#### 8.3.7 `MCPToolCallSession.close()` — 优雅关闭 MCP 连接

**方法文字流程串讲：**

该方法负责优雅关闭一个 MCP 连接。它首先检查 `_close` 标志避免重复关闭。然后将 `_close` 设为 True，通知 `_process_mcp_tasks` 循环退出。接着清空任务队列——对队列中每个还未处理的任务，向它的结果队列放入 `CancelledError` 通知调用方连接正在关闭。最后停止事件循环并关闭线程池，从全局 `_ALL_INSTANCES` 集合中移除自身。同步版本的 `close_sync()` 通过 `asyncio.run_coroutine_threadsafe()` 调度此方法，并设置 5 秒超时防止阻塞过久。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | 无 |
| **核心逻辑** | 设置关闭标志，清空队列，停止事件循环和线程池 |
| **输出形式** | 无返回值 |
| **底层关键依赖** | `asyncio.Queue.empty()`、`event_loop.call_soon_threadsafe()`、`thread_pool.shutdown()` |
| **关键代码片段** | `self._close = True; while not self._queue.empty(): ...; self._event_loop.call_soon_threadsafe(self._event_loop.stop); self._thread_pool.shutdown(wait=True)` |

---

#### 8.3.8 `LLMToolPluginCallSession.tool_call_async()` — 统一工具调度器

**方法文字流程串讲：**

这是 RAGFlow Agent 工具调用的统一入口，位于 `agent/tools/base.py`。当 LLM 在推理过程中决定调用某个工具时（通过 function calling 返回 `tool_calls`），`chat_mdl.bind_tools()` 绑定的回调会触发此方法。它首先断言工具名称是否存在于 `tools_map` 中，然后获取工具对象。根据工具对象的类型进行三路分发：如果工具是 `MCPToolCallSession` 实例（即 MCP 工具），通过 `thread_pool_exec(tool_obj.tool_call, name, arguments, 60)` 在独立线程池中执行——因为 `MCPToolCallSession.tool_call()` 是同步阻塞方法（内部通过 `future.result()` 等待 MCP 连接返回），不能在主事件循环中阻塞；如果工具有 `invoke_async` 且是协程函数，直接 `await tool_obj.invoke_async(**arguments)`；否则是普通同步工具，通过 `thread_pool_exec(tool_obj.invoke, **arguments)` 在线程池中执行。执行完成后记录耗时日志，并通过 `self.callback()`（即 `canvas.tool_use_callback`）将工具调用记录写入 Redis。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | `name: str`（必填，工具名称）、`arguments: dict[str, Any]`（必填，工具参数） |
| **核心逻辑** | 根据工具类型三路分发执行，记录调用日志 |
| **输出形式** | 返回工具执行结果（任意类型） |
| **底层关键依赖** | `thread_pool_exec()`、`MCPToolCallSession`、`ToolBase.invoke()`、`canvas.tool_use_callback` |
| **关键代码片段** | `if isinstance(tool_obj, MCPToolCallSession): resp = await thread_pool_exec(tool_obj.tool_call, name, arguments, 60); elif hasattr(tool_obj, "invoke_async") and ...: resp = await tool_obj.invoke_async(**arguments); else: resp = await thread_pool_exec(tool_obj.invoke, **arguments)` |

**特殊处理标注：**
- MCP 工具超时时间固定 60 秒，是硬编码值
- `arguments` 被截断为 200 字符后记录日志（`str(arguments)[:200]`）

---

#### 8.3.9 `Agent.__init__()` 中的 MCP 集成逻辑

**方法文字流程串讲：**

Agent 组件的初始化方法在 `agent/component/agent_with_tools.py` 中。它首先遍历 `self._param.tools`（内置工具列表），为每个工具加载对应的组件对象并给名称加下标索引防止重名（如 `retrieval_0`、`retrieval_1`）。然后遍历 `self._param.mcp`（MCP 工具配置列表），对每个 MCP 配置执行以下操作：通过 `MCPServerService.get_by_id(mcp["mcp_id"])` 从数据库查询该 MCP Server 的完整配置（URL、传输类型、headers 等）；以该配置创建 `MCPToolCallSession` 实例——这会触发前述的独立线程启动和 MCP 连接建立；遍历 `mcp["tools"]` 中的每个工具元数据，调用 `mcp_tool_metadata_to_openai_tool()` 将其转换为 OpenAI Function Calling 格式，同时将工具名称注册到 `self.tools` 字典。所有工具（内置 + MCP）注册完成后，创建一个 `LLMToolPluginCallSession` 作为统一调度器，通过 `self.chat_mdl.bind_tools()` 绑定到 LLM。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | `canvas`（必填，工作流画布）、`id`（必填，组件 ID）、`param: LLMParam`（必填，组件参数，其中包含 `mcp` 列表） |
| **核心逻辑** | 加载内置工具和 MCP 工具，统一注册到 LLM 的 tool_choice 机制 |
| **输出形式** | 无返回值；初始化完成后 `self.tools` 包含所有工具、`self.chat_mdl` 已绑定工具 |
| **底层关键依赖** | `MCPServerService.get_by_id()`、`MCPToolCallSession`、`mcp_tool_metadata_to_openai_tool()`、`LLMToolPluginCallSession`、`LLMBundle.bind_tools()` |
| **关键代码片段** | `for mcp in self._param.mcp: _, mcp_server = MCPServerService.get_by_id(mcp["mcp_id"]); tool_call_session = MCPToolCallSession(...); for tnm, meta in mcp["tools"].items(): self.tool_meta.append(mcp_tool_metadata_to_openai_tool(meta)); self.tools[tnm] = tool_call_session` |

---

#### 8.3.10 `MCPServerService.get_servers()` — 查询 MCP Server 列表

**方法文字流程串讲：**

这是 `api/db/services/mcp_server_service.py` 中最重要的查询方法。它首先指定查询字段列表（id、name、server_type、url、description、variables、create_date、update_date），这样避免了查询不必要的字段（如 headers 等大字段），提高列表查询性能。然后构建过滤条件：`tenant_id == tenant_id` 是必选条件，确保多租户隔离；如果传入了 `id_list`，加入 `id.in_(id_list)` 条件；如果传入了 `keywords`，加入 `LOWER(name).contains(keywords.lower())` 实现模糊搜索。排序方面，默认按 `create_time` 降序排列，可通过 `orderby` 和 `desc` 参数控制。如果传入了 `page_number` 和 `items_per_page`，调用 `paginate()` 方法做分页。最后通过 `list(query.dicts())` 转换为字典列表返回，如果没有数据返回 None。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | `tenant_id: str`（必填）、`id_list: list[str] \| None`（选填）、`page_number`（选填，0 表示不分页）、`items_per_page`（选填）、`orderby`（选填，默认 "create_time"）、`desc`（选填，默认 True）、`keywords`（选填，模糊搜索） |
| **核心逻辑** | 多条件组合查询 MCP Server 列表 |
| **输出形式** | `list[dict] \| None` |
| **底层关键依赖** | `peewee.fn.LOWER`、`Model.select().where().order_by().paginate()` |
| **关键代码片段** | `query = cls.model.select(*fields).where(cls.model.tenant_id == tenant_id); if keywords: query = query.where(fn.LOWER(cls.model.name).contains(keywords.lower())); if page_number and items_per_page: query = query.paginate(page_number, items_per_page)` |

---

#### 8.3.11 `/create` API — 创建 MCP Server

**方法文字流程串讲：**

这是 `api/apps/mcp_server_app.py` 中创建 MCP Server 的路由。请求参数经过 `@validate_request("name", "url", "server_type")` 装饰器校验确保这三个必填字段存在。校验通过后依次做多个验证：`server_type` 必须在 `VALID_MCP_SERVER_TYPES` 列表中（SSE 或 STREAMABLE_HTTP）；名称不能为空且 UTF-8 编码长度不超过 255 字节；通过 `get_by_name_and_tenant()` 检查当前租户下是否有重名；URL 不能为空。验证通过后，解析 headers 和 variables（使用 `safe_json_parse()` 防止格式化错误），移除 variables 中的 `tools` 字段防止冲突。然后创建一个临时的 `MCPServer` 对象，调用 `get_mcp_tools()` 工具函数连接该 MCP Server 执行 `list_tools` 操作，获取工具列表。将工具列表打平为 `{tool_name: tool}` 字典格式存入 `variables["tools"]`，最后将完整数据插入数据库。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | POST JSON Body：`name`（必填）、`url`（必填）、`server_type`（必填）、`headers`（选填）、`variables`（选填）、`timeout`（选填） |
| **核心逻辑** | 校验参数 → 连接 MCP Server 获取工具列表 → 持久化到数据库 |
| **输出形式** | 返回创建的 MCP Server 数据（含 tools 列表） |
| **底层关键依赖** | `MCPServerService`、`get_mcp_tools()`、`safe_json_parse()`、`get_uuid()` |
| **关键代码片段** | `mcp_server = MCPServer(id=server_name, name=server_name, url=url, server_type=server_type, variables=variables, headers=headers); server_tools, err_message = await thread_pool_exec(get_mcp_tools, [mcp_server], timeout); tools = {tool["name"]: tool for tool in tools if isinstance(tool, dict) and "name" in tool}; variables["tools"] = tools` |

---

#### 8.3.12 `mcp_tool_metadata_to_openai_tool()` — 格式转换函数

**方法文字流程串讲：**

这是一个独立的工具函数，位于 `common/mcp_tool_call_conn.py` 末尾。它将 MCP 协议格式的工具元数据转换为 OpenAI Function Calling 格式。MCP 工具描述包含 `name`、`description`、`inputSchema` 三个核心字段，OpenAI 格式要求包一层 `type: "function"` 并将参数映射到 `function.parameters`。函数首先判断输入是 `dict` 类型还是 `Tool` 类型（MCP SDK 的 `mcp.types.Tool`），如果是 dict 直接从键取值，如果是 `Tool` 对象从属性取值。输出格式统一为 `{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}`。

**强制 5 要素：**

| 要素 | 内容 |
|------|------|
| **入参** | `mcp_tool: Tool \| dict`（必填） |
| **核心逻辑** | 将 MCP 工具元数据转换为 OpenAI Function Calling 格式 |
| **输出形式** | `dict[str, Any]` |
| **底层关键依赖** | 无外部依赖，纯 Python 字典组装 |
| **关键代码片段** | `return {"type": "function", "function": {"name": mcp_tool["name"], "description": mcp_tool["description"], "parameters": mcp_tool["inputSchema"]}}` |

---

### 8.4 同类逻辑对比表

#### 8.4.1 MCP 两种传输协议对比

| 对比维度 | SSE 传输 | Streamable HTTP 传输 |
|---------|---------|---------------------|
| **底层 API** | `mcp.client.sse.sse_client()` | `mcp.client.streamable_http.streamablehttp_client()` |
| **连接方式** | 长连接（Server-Sent Events 流） | HTTP 请求-响应模式 |
| **初始化代码** | `async with sse_client(url, headers) as stream: async with ClientSession(*stream) as session` | `async with streamablehttp_client(url, headers) as (read, write): async with ClientSession(read, write) as session` |
| **适用场景** | 需要 Server 主动推送的场景 | 请求-响应模式，标准 HTTP |
| **异常处理** | 连接异常捕获在 `except Exception` | 连接异常捕获在 `except Exception as e` 并打印异常栈 |
| **优势** | 实时性高 | 兼容性好，无长连接资源占用 |

#### 8.4.2 Agent 工具调用三路分发对比

| 对比维度 | MCP 工具 | 异步工具 | 同步工具 |
|---------|---------|---------|---------|
| **检测条件** | `isinstance(tool_obj, MCPToolCallSession)` | `hasattr(tool_obj, "invoke_async") and iscoroutinefunction()` | 以上两者之外的兜底 |
| **执行方式** | `await thread_pool_exec(tool_obj.tool_call, name, arguments, 60)` | `await tool_obj.invoke_async(**arguments)` | `await thread_pool_exec(tool_obj.invoke, **arguments)` |
| **对应代码文件** | `common/mcp_tool_call_conn.py` | 任意实现了 `invoke_async` 的工具 | 任意实现了 `invoke` 的工具 |
| **超时控制** | 固定 60 秒 | 由调用方控制 | 由 `thread_pool_exec` 控制 |
| **适用场景** | 外部 MCP Server 调用 | 内置工具的异步版本 | 内置工具的同步版本 |

#### 8.4.3 管理 API 路由对比

| 路由 | 方法 | 必填参数 | 核心逻辑 | 输出 |
|------|------|---------|---------|------|
| `/list` | POST | 无 | 查询当前租户下的 MCP Server 列表 | `{mcp_servers, total}` |
| `/detail` | GET | `mcp_id` | 查询单个 MCP Server 详情 | MCP Server 对象 |
| `/create` | POST | `name`, `url`, `server_type` | 创建 MCP Server 并获取工具列表 | 创建的 MCP Server 数据 |
| `/update` | POST | `mcp_id` | 更新 MCP Server 配置并重新获取工具列表 | 更新后的 MCP Server 数据 |
| `/rm` | POST | `mcp_ids` | 批量删除 MCP Server | `True` |
| `/import` | POST | `mcpServers` | 批量导入 MCP Server（支持重名自动重命名） | `{results: [{server, success}]}` |
| `/export` | POST | `mcp_ids` | 批量导出 MCP Server 配置 | `{mcpServers: {...}}` |
| `/list_tools` | POST | `mcp_ids` | 获取指定 MCP Server 的工具列表 | `{mcp_id: [tools]}` |
| `/test_tool` | POST | `mcp_id`, `tool_name`, `arguments` | 测试调用 MCP 工具 | 工具执行结果 |
| `/cache_tools` | POST | `mcp_id`, `tools` | 更新 MCP Server 的工具缓存 | 更新后的工具列表 |
| `/test_mcp` | POST | `url`, `server_type` | 测试连接 MCP Server 获取工具列表 | 工具列表 |

### 8.5 疑惑解答

**Q1：为什么 MCP 连接要使用独立的线程和事件循环，而不是复用主事件循环？**

因为 MCP 连接需要长期维持与外部 Server 的 SSE 流连接，这涉及持续的网络 I/O 等待。如果放在主事件循环中，MCP 连接的 `_process_mcp_tasks()` 循环会阻塞主循环的事件处理。此外，某些 MCP Server 响应慢甚至无响应，独立线程可以隔离这种故障——一个 MCP 连接卡住不会影响其他连接或主流程。设计上每个 MCP 连接使用 `ThreadPoolExecutor(max_workers=1)` + `asyncio.new_event_loop()` 的组合，即每个连接一个线程 + 一个事件循环。

**Q2：为什么 `tool_call()` 是同步方法，而内部实现却是异步的？**

因为 `LLMToolPluginCallSession.tool_call()` 被设计为统一的工具调用接口，但不同的工具实现有不同的运行方式。`MCPToolCallSession.tool_call()` 内部的异步调用实际上运行在另一个线程的事件循环中，对外暴露为同步接口以便与 `LLMToolPluginCallSession` 的调度机制兼容。调用方通过 `asyncio.run_coroutine_threadsafe()` 跨线程调度，再用 `future.result()` 同步等待结果。

**Q3：`create` API 中为什么要先连接 MCP Server 获取工具列表，再做数据库插入？**

因为创建 MCP Server 时，前端需要在创建完成后立即展示该 Server 可用的工具列表供用户勾选。如果先入库再连接获取工具，就要在数据库操作之后再补发一次请求来获取工具列表，增加了交互复杂度。RAGFlow 的设计是在创建时同步连接 MCP Server 获取工具列表，将列表持久化到 `variables["tools"]` 字段中，这样后续查询工具列表时可以直接从数据库读取，无需每次重新连接 MCP Server。

**Q4：`close()` 方法中的 `while not self._queue.empty()` 循环有什么作用？**

关闭 MCP 连接时，任务队列中可能还有未处理的任务。如果不清空队列，这些任务的调用方会永远阻塞在 `results.get()` 上等待永远不会返回的结果。所以关闭时遍历队列中所有剩余任务，向每个任务的结果队列放入 `CancelledError`，让调用方立即收到连接关闭的信号，避免资源泄漏。

### 8.6 规范修正

**专业术语统一：**
- `SSE` 统一译为"Server-Sent Events 传输"，不在口语表达中缩写
- `MCPToolCallSession` 是 MCP 客户端连接会话，不是服务端
- `tool_call` 是 MCP 协议中的方法名，`invoke` 是 RAGFlow 工具的执行方法，两者不可混用
- `_process_mcp_tasks` 中的 `client_session` 是 MCP SDK 的 ClientSession，不是 RAGFlow 自己的 Session

### 8.7 可复现实操步骤（傻瓜式落地）

#### 步骤 1：启动一个 MCP Server

```bash
# 启动 RAGFlow 自带的 MCP Server（作为 Server 端）
uv run mcp/server/server.py --host=127.0.0.1 --port=9382 \
    --base-url=http://127.0.0.1:9380 --api-key=ragflow-xxxxx
```
- **依赖模块：** `mcp/server/server.py`
- **注意事项：** 需要先启动 RAGFlow 主服务（端口 9380），且 API Key 需提前获取
- **执行目标：** 对外暴露 `ragflow_retrieval` 工具，供 MCP 客户端调用

#### 步骤 2：创建 RAGFlow MCP Client 连接

```python
# 代码示例：手动创建 MCPToolCallSession 并调用工具
from common.mcp_tool_call_conn import MCPToolCallSession
from api.db.db_models import MCPServer

# 1. 构造 MCP Server 数据库对象
mcp_server = MCPServer(
    id="test_server",
    name="test_server",
    url="http://127.0.0.1:9382",
    server_type="SSE",
    variables={},
    headers={}
)

# 2. 创建连接会话（自动在后台线程启动连接循环）
session = MCPToolCallSession(mcp_server, mcp_server.variables)

# 3. 获取工具列表
tools = session.get_tools(timeout=10)
print(tools)

# 4. 调用工具
result = session.tool_call("ragflow_retrieval", {
    "question": "什么是RAG技术？",
    "dataset_ids": ["your_dataset_id"]
})
print(result)

# 5. 关闭连接
session.close_sync(timeout=5)
```
- **依赖模块：** `common/mcp_tool_call_conn.py`、`mcp` 第三方库
- **注意事项：** `get_tools()` 和 `tool_call()` 都是同步阻塞方法，内部通过 `future.result()` 等待
- **执行目标：** 验证 MCP 连接和工具调用是否正常

#### 步骤 3：通过管理 API 创建 MCP Server

```bash
curl -X POST http://127.0.0.1:9380/api/v1/mcp/create \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_mcp_server",
    "url": "http://127.0.0.1:9382",
    "server_type": "SSE",
    "timeout": 10
  }'
```
- **依赖模块：** `api/apps/mcp_server_app.py`
- **注意事项：** 需要登录态 Token，`server_type` 必须是 `SSE` 或 `STREAMABLE_HTTP`
- **执行目标：** 在 RAGFlow 中注册一个 MCP Server，创建时会自动获取工具列表

#### 步骤 4：在 Agent 工作流中使用 MCP 工具

1. 在 RAGFlow Web 界面中创建一个 Agent 工作流
2. 在 Agent 组件的"添加工具"中选择已创建的 MCP Server
3. 勾选需要用到的工具
4. 运行工作流，LLM 会自动根据 query 选择是否调用 MCP 工具
- **依赖模块：** `agent/component/agent_with_tools.py`、前端 MCP 组件
- **执行目标：** MCP 工具与内置工具统一在 Agent 中使用

### 8.8 关键模块总览

| 模块名称 | 文件路径 | 负责功能 | 在流程中的核心作用 |
|---------|---------|---------|-----------------|
| MCP Client 连接会话 | [common/mcp_tool_call_conn.py](file:///e:/AI/GitHub/RagFlow/common/mcp_tool_call_conn.py) | MCP 连接管理、工具调用、会话清理 | 底层协议实现，所有 MCP 通信的基础 |
| Agent 组件 | [agent/component/agent_with_tools.py](file:///e:/AI/GitHub/RagFlow/agent/component/agent_with_tools.py) | 加载 MCP 工具、集成到 LLM tool_choice | MCP 工具在 Agent 中的集成点和执行入口 |
| 工具调度器 | [agent/tools/base.py](file:///e:/AI/GitHub/RagFlow/agent/tools/base.py) | 统一分发工具调用（MCP/异步/同步） | 确保 MCP 工具与内置工具统一调度 |
| MCP 管理 API | [api/apps/mcp_server_app.py](file:///e:/AI/GitHub/RagFlow/api/apps/mcp_server_app.py) | MCP Server 的 CRUD、工具测试、批量导入导出 | 用户操作 MCP Server 的 REST API 入口 |
| MCP 数据库服务 | [api/db/services/mcp_server_service.py](file:///e:/AI/GitHub/RagFlow/api/db/services/mcp_server_service.py) | MCP Server 的数据库查询和操作 | 数据持久化层，存储 MCP 配置和工具列表 |
| MCP 数据模型 | [api/db/db_models.py](file:///e:/AI/GitHub/RagFlow/api/db/db_models.py#L1086) | MCPServer 表结构定义 | 数据持久化的基础 |
| MCP Server 服务端 | [mcp/server/server.py](file:///e:/AI/GitHub/RagFlow/mcp/server/server.py) | RAGFlow 作为 MCP Server 对外暴露检索能力 | 让外部 MCP 客户端可以调用 RAGFlow 知识库 |
| MCP 前端页面 | [web/src/pages/user-setting/mcp/index.tsx](file:///e:/AI/GitHub/RagFlow/web/src/pages/user-setting/mcp/index.tsx) | MCP Server 管理界面 | 用户操作的图形化入口 |