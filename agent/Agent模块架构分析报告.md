# Agent 模块架构分析报告

> 分析日期：2026-06-24

---

## 一、总览：分层架构

```
┌──────────────────────────────────────────────────────────────┐
│                     API Layer (Flask)                        │
│            api/apps/canvas_app.py  → 接收请求、返回 SSE      │
└──────────────────────────┬───────────────────────────────────┘
                           │ DSL JSON
┌──────────────────────────▼───────────────────────────────────┐
│                   Canvas 编排引擎                            │
│  canvas.py: Canvas(Graph) → 解析 DSL、调度执行、流式输出     │
│  extensions/ → 中间件 / DSL 校验 / 工具链 / 断点恢复        │
└──────────────────────────┬───────────────────────────────────┘
                           │ 组件调度
┌──────────────────────────▼───────────────────────────────────┐
│                   Component System (22 个组件)               │
│  base.py → ComponentBase / ComponentParamBase               │
│  ├── 控制流: Begin, Switch, Categorize, Loop, Iteration     │
│  ├── AI 核心: LLM, Agent(带工具)                             │
│  ├── 数据处理: Message, Fillup, DocsGenerator, Excel        │
│  └── 工具类: VariableAssigner, StringTransform, ...         │
└──────────────────────────┬───────────────────────────────────┘
                           │ 被 Agent 调用
┌──────────────────────────▼───────────────────────────────────┐
│                   Tools System (22 个工具)                   │
│  base.py → ToolBase / ToolParamBase                         │
│  ├── 搜索引擎: Tavily, Google, DuckDuckGo, SearXNG, ...    │
│  ├── 学术检索: ArXiv, PubMed, GoogleScholar                 │
│  ├── 数据源: AKShare, Tushare, YahooFinance, Jin10          │
│  ├── 代码执行: CodeExec (沙箱)                              │
│  ├── 知识库: Retrieval (RAGFlow 自身 RAG)                   │
│  └── 其他: Email, GitHub, DeepL, Wikipedia, ...             │
└──────────────────────────┬───────────────────────────────────┘
                           │ 沙箱隔离
┌──────────────────────────▼───────────────────────────────────┐
│                   Sandbox (代码执行安全层)                   │
│  providers/ → Aliyun / E2B / SelfManaged                   │
│  executor_manager/ → Docker 容器管理、限流、安全审计        │
└──────────────────────────────────────────────────────────────┘
```

---

## 二、目录结构

```
agent/
├── __init__.py                  # 空文件（版权声明）
├── canvas.py                    # 核心编排引擎 (Graph → Canvas)
├── settings.py                  # 全局常量 FLOAT_ZERO, PARAM_MAXDEPTH
│
├── component/                   # 组件系统 (22 个组件)
│   ├── __init__.py              # 组件动态加载器 component_class()
│   ├── base.py                  # 基类: ComponentParamBase / ComponentBase
│   ├── begin.py                 # 入口组件: 用户输入、文件解析、Webhook
│   ├── llm.py                   # LLM 推理组件
│   ├── agent_with_tools.py      # Agent 组件 (LLM + Tool Use / ReAct)
│   ├── message.py               # 消息输出 (流式、格式转换、记忆存储)
│   ├── switch.py                # 条件分支 (10 种操作符)
│   ├── categorize.py            # LLM 驱动的分类路由
│   ├── loop.py / loopitem.py    # 循环迭代组件
│   ├── iteration.py / iterationitem.py  # 迭代组件 (含终止条件)
│   ├── exit_loop.py             # 提前退出循环
│   ├── fillup.py                # 用户中途输入表单
│   ├── invoke.py                # 子工作流调用
│   ├── docs_generator.py        # 文档生成 (68KB，最复杂组件)
│   ├── excel_processor.py       # Excel 读写处理
│   ├── variable_assigner.py     # 变量赋值
│   ├── varaiable_aggregator.py  # 变量聚合
│   ├── string_transform.py      # 字符串转换
│   ├── list_operations.py       # 列表操作
│   └── data_operations.py       # 数据操作
│
├── tools/                       # 工具系统 (22 个工具)
│   ├── __init__.py              # 工具加载入口
│   ├── base.py                  # ToolBase / ToolParamBase / LLMToolPluginCallSession
│   ├── tavily.py                # Tavily 搜索
│   ├── google.py                # Google 搜索
│   ├── duckduckgo.py            # DuckDuckGo 搜索
│   ├── searxng.py               # SearXNG 元搜索
│   ├── wikipedia.py             # Wikipedia 搜索
│   ├── arxiv.py                 # ArXiv 学术检索
│   ├── pubmed.py                # PubMed 学术检索
│   ├── googlescholar.py         # Google Scholar 学术检索
│   ├── retrieval.py             # RAGFlow 知识库检索
│   ├── code_exec.py             # 代码执行 (沙箱)
│   ├── akshare.py               # A股/金融数据
│   ├── tushare.py               # 金融数据
│   ├── yahoofinance.py          # 雅虎财经
│   ├── jin10.py                 # 金十数据
│   ├── wencai.py                # 问财数据
│   ├── qweather.py              # 天气数据
│   ├── exesql.py                # SQL 执行
│   ├── crawler.py               # 网页爬虫
│   ├── deepl.py                 # DeepL 翻译
│   ├── github.py                # GitHub API
│   └── email.py                 # 邮件发送
│
├── extensions/                  # 扩展系统（企业级增强）
│   ├── __init__.py              # 统一导出
│   ├── canvas_middleware.py     # 中间件插件架构 (5 个钩子)
│   ├── dsl_schema.py            # DSL 校验 / 版本迁移 / 模板管理
│   ├── tool_chain.py            # 工具链编排 (DAG + 3 种模式)
│   └── workflow_checkpoint.py   # 工作流断点保存/恢复
│
├── templates/                   # 预置工作流模板 (24 个 JSON)
│   ├── deep_research.json       # 深度研究
│   ├── customer_service.json    # 客服系统
│   ├── sql_assistant.json       # SQL 助手
│   ├── stock_research_report.json  # 股票研报
│   ├── generate_SEO_blog.json   # SEO 博客生成
│   ├── cv_analysis_and_candidate_evaluation.json  # 简历分析
│   ├── web_search_assistant.json  # 网络搜索助手
│   └── ... (共 24 个模板)
│
├── plugin/                      # 插件系统
│   ├── __init__.py
│   ├── common.py               # 插件类型常量
│   ├── plugin_manager.py       # PluginManager (加载/查询插件)
│   ├── llm_tool_plugin.py      # LLMToolPlugin 定义
│   └── embedded_plugins/       # 内置插件
│       └── llm_tools/
│           └── bad_calculator.py
│
├── sandbox/                     # 代码执行沙箱
│   ├── client.py               # 沙箱客户端 SDK
│   ├── providers/              # 沙箱后端
│   │   ├── base.py             # 抽象接口
│   │   ├── aliyun_codeinterpreter.py
│   │   ├── e2b.py              # E2B 云沙箱
│   │   ├── self_managed.py     # 自建 Docker 沙箱
│   │   └── manager.py          # Provider 管理器
│   └── executor_manager/       # 自建沙箱服务端
│       ├── main.py
│       ├── core/               # 容器管理、配置
│       ├── services/           # 代码执行、安全、限流
│       └── api/                # Flask API 路由
│
└── test/
    └── client.py
```

---

## 三、核心入口：DSL 到执行的完整链路

整个系统的入口是一段 **JSON DSL**（类似工作流描述语言），描述了一个有向图：

```json
{
  "components": {
    "begin": {
      "obj": {"component_name": "Begin", "params": {}},
      "downstream": ["llm_0"],
      "upstream": []
    },
    "llm_0": {
      "obj": {"component_name": "LLM", "params": {}},
      "downstream": ["message_0"],
      "upstream": ["begin"]
    },
    "message_0": {
      "obj": {"component_name": "Message", "params": {}},
      "downstream": [],
      "upstream": ["llm_0"]
    }
  },
  "path": ["begin"],
  "globals": {
    "sys.query": "",
    "sys.user_id": "",
    "sys.conversation_turns": 0,
    "sys.files": [],
    "sys.history": [],
    "sys.date": ""
  },
  "history": [],
  "retrieval": [],
  "memory": []
}
```

### 执行流程

参考文件：[canvas.py](canvas.py)

```
1. Graph.__init__(dsl) → Graph.load()
   └── components 字典中每个节点:
       ├── 通过 component_class() 动态加载类 (component/__init__.py:51-58)
       ├── 实例化 Param 对象并调用 param.check() 校验参数
       └── 实例化 Component 对象 (canvas, component_id, param)

2. Canvas.run(**kwargs) → async generator (SSE)
   └── while idx < len(self.path):           ← 逐批推进 path
       ├── yield "node_started"               ← SSE 事件
       ├── await _run_batch(idx, to)          ← 并发执行当前批次
       │   └── ThreadPoolExecutor (max_workers=5) + asyncio.Semaphore
       │       对每个组件: cpn.invoke() 或 cpn.invoke_async()
       ├── 后处理:
       │   ├── Message 组件 → 流式输出 content (partial/stream)
       │   ├── Switch/Categorize → 分支路由 (设置 _next)
       │   ├── Loop/Iteration → 循环展开 (扩展 path)
       │   └── 异常处理 → exception_handler (goto/default_value)
       └── yield "node_finished" / "workflow_finished"
```

### 关键设计

- `path` 是动态增长的工作路径列表，控制流组件（Switch、Loop）在运行时向 path 追加新节点
- 同一批次内的组件**并发执行**，批次间**串行推进**
- 通过异步生成器（`async yield`）实现 SSE 流式输出

---

## 四、组件系统（Component System）

### 4.1 双层基类设计

```
ComponentParamBase          ComponentBase (ABC)
  ├── param 校验/更新           ├── invoke() / invoke_async()
  ├── 输入/输出管理              ├── output() / set_output()
  └── 异常策略配置              ├── get_input() / get_input_elements()
                               ├── error() / debug()
                               └── thoughts()
```

- **Param 对象**：声明式配置，支持递归嵌套更新、参数校验、过时参数兼容（[base.py:40-363](component/base.py)）
- **Component 对象**：持有 `_canvas` 引用（双向绑定），通过 `variable_ref_patt` 正则解析 DSL 变量引用 `{component_id@variable_name}`

### 4.2 组件分类

| 类别 | 组件 | 职责 |
|------|------|------|
| **入口** | `Begin` | 接收用户输入、文件解析、Webhook |
| **AI 推理** | `LLM` | 纯 LLM 调用（prompt + 历史 + 引用） |
| **AI 推理** | `Agent` | LLM + Tool Use（多轮 ReAct 循环） |
| **输出** | `Message` | 流式/模板渲染、格式转换（PDF/XLSX/DOCX）、记忆存储 |
| **分支** | `Switch` | 多条件逻辑路由（and/or，10 种操作符） |
| **分类** | `Categorize` | LLM 驱动的分类路由 |
| **循环** | `Loop` / `LoopItem` | 基于列表的循环迭代 |
| **循环** | `Iteration` / `IterationItem` | 迭代循环（含终止条件、最大次数） |
| **退出** | `ExitLoop` | 提前退出循环 |
| **数据处理** | `DocsGenerator` | 文档生成（68KB，最复杂的组件） |
| **数据处理** | `ExcelProcessor` | Excel 读写处理 |
| **数据操作** | `VariableAssigner` / `VariableAggregator` | 变量赋值/聚合 |
| **数据操作** | `StringTransform` / `ListOperations` / `DataOperations` | 字符串/列表/数据转换 |
| **交互** | `Fillup` / `UserFillUp` | 用户中途输入表单 |
| **调用** | `Invoke` | 子工作流调用 |

---

## 五、LLM 与 Agent 的执行机制

### 5.1 LLM 组件

参考文件：[component/llm.py](component/llm.py)

```
_invoke_async():
  1. _prepare_prompt_variables()
     ├── 提取输入中的 data:image 变量 → 分离图片
     ├── 替换 prompt 模板变量
     └── 如果下游连接 Message → 设置 output("content") = partial(stream_fn)
                                   实现延迟流式输出

  2. 如果有 output_structure (JSON Schema):
     └── 调用 structured_output_prompt → 要求 LLM 返回严格 JSON
        失败重试 max_retries 次

  3. 如果下游是 Message:
     └── 直接返回 partial(_stream_output_async)  ← 延迟执行
        真正的流式生成在 Canvas.run() 的 Message 后处理中触发

  4. 否则:
     └── 直接 await _generate_async() → set_output("content", ans)
```

**核心优化**：
- `<think>` / `</think>` 标签解析 → 思维链可视化
- 图片自动检测 → 切换到 `IMAGE2TEXT` 模型
- 引用自动拼接（`citation_prompt`）

### 5.2 Agent 组件（ReAct 模式）

参考文件：[component/agent_with_tools.py](component/agent_with_tools.py)

Agent 继承自 `LLM` + `ToolBase`，实现 **ReAct 模式**的工具调用：

```
Agent.__init__():
  1. 加载 tools 列表 → 每个 tool 实例化并注册到 self.tools
  2. 加载 MCP 工具 → MCPToolCallSession 封装
  3. 构建 tool_meta (OpenAI function calling 格式)
  4. chat_mdl.bind_tools(toolcall_session, tool_meta) → 绑定工具到 LLM

Agent._invoke_async():
  1. 如果有 external kwargs (user_prompt/reasoning/context)
     → 构建为 prompts（支持父 Agent 传递指令）

  2. 没有 tools → 回退到 LLM._invoke_async() (纯 LLM 模式)

  3. 有 tools + 下游是 Message:
     → partial(stream_output_with_tools_async) → 流式工具调用模式
       每轮 tool call 结果自动注入消息，LLM 继续推理

  4. 有 tools + 无 Message 下游:
     → 直接 await _generate_async()（单次调用）
     → 有 output_structure → JSON 解析 + 重试
     → 收集 tool 产出的附件和 artifact
```

---

## 六、Tool System 与调用链

### 6.1 工具注册机制

参考文件：[tools/base.py](tools/base.py)

每个工具是一个 **Component**（继承 `ToolBase`），定义 `meta` 元数据：

```python
class ToolMeta(TypedDict):
    name: str
    description: str
    parameters: dict[str, ToolParameter]  # JSON Schema 格式
```

工具的 `get_meta()` 返回 OpenAI Function Calling 兼容的格式，LLM 据此决定调用哪个工具。

### 6.2 工具调用会话（LLMToolPluginCallSession）

```
LLMToolPluginCallSession.tool_call_async(name, arguments):
  1. 查找 tool_map[name] → ToolBase 实例 或 MCPToolCallSession
  2. 调用 tool.invoke_async(**arguments) 或 tool.invoke(**arguments)
  3. 执行 callback(name, arguments, result, elapsed) → 记录到 Redis
  4. 返回结果给 LLM
```

### 6.3 ToolChain 工具链编排

参考文件：[extensions/tool_chain.py](extensions/tool_chain.py)

支持三种执行模式：

| 模式 | 行为 | 使用场景 |
|------|------|----------|
| `SEQUENTIAL` | A→B→C 串行，前输出 = 后输入 | 搜索→提取→分析 |
| `PARALLEL` | DAG 依赖解析，无依赖者并发 | 多源搜索合并 |
| `FALLBACK` | 主工具失败自动降级 | 主 API 不可用切换备用 |

---

## 七、扩展系统（Extensions）

位于 [extensions/](extensions/)，是最近新增的企业级增强层：

| 模块 | 职责 | 核心价值 |
|------|------|----------|
| **canvas_middleware.py** | 5 个钩子的中间件链 | 审计、监控、限流、日志增强——无需修改 canvas.py |
| **dsl_schema.py** | DSL 结构校验 + 版本迁移 + 模板管理 | 提前拦截错误、自动版本升级、参数化模板实例化 |
| **tool_chain.py** | 工具链编排（DAG + 3 种执行模式） | 减少 LLM 推理次数、并发执行无依赖工具 |
| **workflow_checkpoint.py** | 工作流断点保存/恢复 | 长时间运行容错、支持故障恢复 |

### 7.1 中间件钩子（MiddlewareHook）

参考文件：[extensions/canvas_middleware.py](extensions/canvas_middleware.py)

```
BEFORE_WORKFLOW → AFTER_NODE → BEFORE_TOOL → AFTER_TOOL → AFTER_WORKFLOW
```

借鉴 Web 框架（Flask/Werkzeug）的中间件链模式，将 Canvas 执行生命周期划分为 5 个钩子点。

### 7.2 DSL 校验与版本迁移

参考文件：[extensions/dsl_schema.py](extensions/dsl_schema.py)

- **DSLSchemaValidator**：参照 JSON Schema 标准，在 `Graph.load()` 之前执行校验，提前拦截错误
- **DSLVersionMigrator**：支持 DSL 跨版本迁移（如 v0.0.1 → v1.0.0），自动检测版本差异并执行迁移脚本
- **DSLTemplateManager**：预置工作流模板，支持 `${variable}` 参数化实例化

---

## 八、变量系统与 DSL 引用

整个画布通过**变量引用**实现组件间数据传递（[canvas.py:166-269](canvas.py)）：

```
引用语法: {component_id@variable_name.field.subfield}
系统变量: {sys.query} {sys.user_id} {sys.files} ...
环境变量: {env.api_key}

解析流程:
  get_variable_value(exp):
    1. 无 @ → 从 globals 字典取
    2. 有 @ → 从对应组件的 outputs 取，支持点号路径 (a.b.0.c)
```

**变量类型支持**：

| 类型 | 说明 |
|------|------|
| `string` | 字符串 |
| `number` | 数字 |
| `boolean` | 布尔值 |
| `object` | JSON 对象 |
| `array[string]` | 字符串数组 |
| `array[number]` | 数字数组 |
| `array[object]` | 对象数组 |

---

## 九、沙箱系统（Sandbox）

位于 [sandbox/](sandbox/)，用于安全执行代码（Python/SQL）：

```
sandbox/
├── client.py          → 沙箱客户端 SDK
├── providers/         → 后端实现
│   ├── base.py        → 抽象接口
│   ├── aliyun_codeinterpreter.py
│   ├── e2b.py         → E2B 云沙箱
│   └── self_managed.py → 自建 Docker 沙箱
└── executor_manager/  → 自建沙箱的服务端
    ├── core/          → 容器管理、配置
    ├── services/      → 代码执行、安全审计、限流
    └── api/           → Flask API 路由
```

---

## 十、整体执行流程图

```
用户请求 (HTTP)
    │
    ▼
canvas_app.py 构造 Canvas(dsl_json, tenant_id, task_id)
    │
    ▼
Canvas.__init__ → Graph.load()
    ├── DSLSchemaValidator.validate()      ← 可选：提前校验
    ├── DSLVersionMigrator.migrate()       ← 可选：版本升级
    ├── component_class() 动态加载每个组件
    └── Param.update() + Param.check()
    │
    ▼
canvas.run(query="用户问题", files=[...])
    │
    ▼ (async generator → SSE)
LOOP: while idx < len(path):
    │
    ├── yield "node_started"
    │
    ├── _run_batch(idx, to)               ← 并发执行当前批次
    │   ├── Begin.invoke()     → 解析输入
    │   ├── Retrieval.invoke() → 检索知识库
    │   ├── Agent.invoke()     → LLM + Tool Use (ReAct)
    │   │   └── LLMToolPluginCallSession
    │   │       ├── tool.invoke_async()
    │   │       └── callback → Redis 记录
    │   └── LLM.invoke()       → LLM 推理
    │
    ├── 后处理:
    │   ├── Message → _stream() → yield "message" (逐 token)
    │   ├── Switch  → 条件判断 → 扩展 path
    │   ├── Loop    → 循环展开 → 扩展 path
    │   └── 异常    → exception_handler (goto/默认值)
    │
    ├── yield "node_finished"
    └── 扩展 path (通过 downstream / _next)
    │
    ▼
yield "workflow_finished"
    ├── 保存 history
    └── 更新 globals
```

---

## 十一、关键设计特点总结

| 特点 | 说明 |
|------|------|
| **DSL 驱动** | 整个工作流是纯 JSON，可存储、可版本化、可模板化 |
| **组件动态加载** | `component_class()` 从 `agent.component` / `agent.tools` / `rag.flow` 三个命名空间动态查找类 |
| **流式优先** | 通过 `functools.partial` 实现延迟执行，Message 组件在 Canvas 层面逐 token 输出 SSE |
| **并发执行** | `ThreadPoolExecutor(max_workers=5)` + `asyncio.Semaphore` 控制同批次组件的并发 |
| **变量引用** | `{cpn_id@key.subkey}` 语法实现组件间数据流，支持点号深层路径 |
| **四层扩展** | 中间件钩子 + 插件系统 + 工具链编排 + 版本迁移 |
| **Tool as Component** | 工具和组件是同级概念（都继承 ComponentBase），Agent 通过 function calling 协议调用 |
| **沙箱隔离** | 代码执行通过 Docker 容器隔离，支持阿里云/E2B/自建三种后端 |
| **多轮工具调用** | Agent 支持 ReAct 模式的迭代推理，LLM 自主决定调用哪些工具、何时结束 |
| **容错设计** | 组件级异常处理（goto/默认值）、工作流级断点恢复、中间件级错误隔离 |
