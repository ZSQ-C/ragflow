# 01 — async_chat()：RAG 对话流程总调度

> **文件位置**：`api/db/services/dialog_service.py` L455-L781
> **核心定位**：RAGFlow 中 RAG 对话的**总调度异步生成器**，负责从用户提问→检索→LLM 生成的完整链路编排
> **调用链**：HTTP POST /v1/conversation/completion → `conversation_app.completion()` → `async_chat(dialog, messages, stream)`

---

## 一、核心总览（带逻辑关系）

### 1.1 核心定位

`async_chat()` 是 RAGFlow 整个 RAG（Retrieval-Augmented Generation）对话系统的**总入口函数**。它不是普通的同步函数，而是一个**异步生成器**（`async generator`），这意味着它可以逐步 `yield` 答案块给调用者，调用者拿到一块就立即通过 SSE 推送给前端，用户不需要等全部答案生成完毕就能看到第一个字——这就是"流式响应"的核心实现原理。

**适用场景**：
- Web UI 用户点击"发送"发起知识库问答
- 通过 REST API `/v1/conversation/completion` 发起的编程调用
- Agent 组件内部调用对话模型时（Agent 的 `_invoke_async` 内部也经过此函数）

**解决的业务问题**：
- 将 LLM 的知识来源限制在指定知识库内，解决大模型**幻觉**问题
- 提供**引用溯源**能力，每条回答可追溯到具体文档和页码
- 支持**多轮对话**上下文理解（如追问"它有什么优势"能正确理解为指代上文讨论的内容）
- 支持**多策略检索**（向量语义检索、SQL 精确检索、Tavily 网络搜索、知识图谱）

### 1.2 整体流程串讲

整个 `async_chat()` 的执行链路可分为三个宏观阶段。**筹备阶段**（L455-L511）负责路由判断和模型加载：系统先检查用户是否指定了知识库或网络搜索 API，都没有则走纯 LLM 对话模式直接 `return`；有的话就从数据库加载对话配置对应的 chat/embed/rerank/tts 四种模型实例，同时提取最近 3 条用户消息作为待检索的问题集合。

**检索阶段**（L514-L636）是 RAG 的核心。如果知识库是结构化数据（有字段映射），优先尝试 `use_sql()`——让 LLM 把自然语言问题转为 SQL 语句去向量数据库精确查询；SQL 无结果或失败，则平滑降级到调用 `settings.retriever.retrieval()` 执行混合检索。在调用检索之前，问题会经过四步精炼：`full_question()` 将多轮上下文压缩为独立问题、`cross_languages()` 做跨语言翻译、`apply_meta_data_filter()` 提取过滤条件、`keyword_extraction()` 追加关键词增强检索权重。检索完成后，还可选叠加 TOC 目录增强、子块展开、Tavily 网络搜索、知识图谱检索四种增强策略。

**生成阶段**（L638-L781）将检索结果通过 `kb_prompt()` 格式化为 LLM 可理解的知识块，用 `message_fit_in()` 把对话历史裁剪到 Token 限制（95% max_tokens）内，拼装系统 Prompt（含引用提示词），最后调用 `chat_mdl.async_chat_streamly_delta()` 流式生成。响应中的 `<think>` 思考标签会被 `_stream_with_think_delta()` 解析分离，同时可选做 TTS 语音合成。

**各模块调用关系**：`async_chat()` 作为总调度器，自身不执行检索或生成，而是通过"获取实例 → 调用方法"的模式串联各子系统。`TenantLLMService` 负责读数据库获取模型配置，`LLMBundle` 封装统一的 LLM 调用接口，`settings.retriever`（全局 Dealer 实例）负责与 Elasticsearch/Infinity 交互执行实际检索，`rag.prompts.generator` 中的工具函数负责 Prompt 构建。

---

## 二、模块拆分（固定顺序 + 关系说明）

### 模块1：初始化与前置校验（L455-L461）

**作用**：流程的"入口门卫"。校验请求合法性（断言最后一条消息来自用户），根据是否有知识库做路由判断——没有知识库且没有网络搜索 API 则直接走纯 LLM 对话模式，跳过所有检索步骤。

**与其他模块的配合关系**：输出决定后续是否执行。走了 `async_chat_solo` 分支则整个函数在此 `return`，模块2-8全部跳过。

### 模块2：模型配置获取（L463-L494）

**作用**："基础设施搭建"。获取本次对话所需的全部模型实例（chat/embed/rerank/tts），设置 token 上限，初始化 Langfuse 追踪器，处理 Agent 场景下的工具绑定。产出 `chat_mdl`、`embd_mdl`、`rerank_mdl` 供后续模块使用。

**与其他模块的配合关系**：`embd_mdl` 传给模块6（检索），`rerank_mdl` 传给模块6（重排序），`chat_mdl` 传给模块6（深度研考）和模块8（LLM 生成）。

### 模块3：问题提取与附件处理（L497-L511）

**作用**：从完整消息列表中提取最近 3 条用户消息，同时解析文件附件和文档 ID。为检索提供"搜什么"的输入。

### 模块4：SQL 检索尝试（L514-L525）

**作用**：结构化数据的"优先通道"。当知识库配置了字段映射时，用 LLM 生成 SQL 精确查询；成功则直接返回答案，失败则平滑降级到模块6的向量检索。

### 模块5：问题精炼优化（L527-L559）

**作用**：检索前的"输入增强"。依次执行参数校验、多轮合并、跨语言翻译、元数据过滤、关键词提取五个子步骤，每个步骤独立可控（通过 `prompt_config` 配置开关）。

### 模块6：核心检索执行（L561-L636）

**作用**：整个 RAG 流程的"核心引擎"。调用 `settings.retriever.retrieval()` 执行混合检索（全文+向量），得到 top-N 相关文档块。可选叠加 TOC 增强、子块展开、Tavily 搜索、KG 检索四种增强。

### 模块7：Prompt 构建与 Token 管理（L638-L667）

**作用**：将检索结果和对话历史"格式化"为 LLM 能理解的输入。`kb_prompt()` 格式化知识块，`message_fit_in()` 裁剪历史到 token 限制内，`citation_prompt()` 生成引用要求。

### 模块8：LLM 流式调用与结果返回（L655-L781）

**作用**：调用 LLM 模型进行流式生成，解析 `<think>` 标签，TTS 语音合成，yield 答案块。

---

## 三、方法详细解析（强制 5 要素 + 文字流程串讲）

### 3.1 方法文字流程串讲：初始化与前置校验（L455-L461）

函数首先打出 `"Begin async_chat"` 调试日志，然后执行 `assert messages[-1]["role"] == "user"`——这是一个硬校验，确保消息列表最后一条必须是用户发来的新问题，而不是系统消息或历史回答。如果校验失败直接抛出断言异常。

接着是关键的**路由判断**：`if not dialog.kb_ids and not dialog.prompt_config.get("tavily_api_key")`——如果对话配置中没有指定任何知识库（`kb_ids` 为空）并且也没有配置 Tavily 网络搜索的 API Key（`tavily_api_key` 为空），说明本次对话不需要做任何检索。这种情况下调用 `async_chat_solo(dialog, messages, stream)` 进入纯 LLM 对话模式——直接把历史消息发给 LLM 生成回答，不做文档检索。`async_chat_solo` 也是一个异步生成器，外层通过 `async for ans in async_chat_solo(...)` 逐块转发其输出，然后 `return` 提前结束整个 `async_chat()` 函数。

**分支判断解读**：如果用户配置了知识库或网络搜索→继续执行后续的检索流程；如果都没有→走纯 LLM 对话，函数提前返回。这个设计避免了"空白搜索"的无效开销。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `dialog`（Dialog 对象，含 kb_ids、prompt_config、llm_id 等）、`messages`（list[dict]，对话历史）、`stream`（bool，默认 True） |
| **核心逻辑** | 校验消息合法性 → 判断是否需要检索 → 无检索则走 `async_chat_solo` 纯对话 |
| **输出形式** | 无检索时：yield 纯 LLM 生成的答案块；无 yield 时：继续执行后续模块 |
| **底层关键依赖** | `async_chat_solo()`（纯 LLM 对话函数） |
| **关键代码片段** | 见上方流程讲解 |

#### 特殊处理标注
- **断言校验**：`assert messages[-1]["role"] == "user"` 防止非用户消息触发检索
- **提前返回**：无知识库 + 无网络搜索 → 直接 `return`，不走后续任何检索逻辑

### 3.2 方法文字流程串讲：模型配置获取（L463-L494）

函数用 `timer()` 记录开始时间戳后，调用 `TenantLLMService.llm_id2llm_type(dialog.llm_id)` 查询数据库判断 LLM 类型——返回 "chat" 表示普通对话模型（如 qwen-turbo-latest）、"image2text" 表示视觉模型。根据类型不同，用不同的 `LLMType` 枚举值调用 `TenantLLMService.get_model_config()` 从 `tenant_llm` 表中读取完整的模型配置字典（含 llm_factory、api_key、max_tokens 等字段）。

接着初始化 Langfuse 可观测性追踪：先从 `TenantLangfuseService` 查询当前租户是否配置了 Langfuse，配置了则尝试连接并创建 trace_id。这里用 `try-except` 包裹，连接失败不阻断主流程。然后调用 **`get_models(dialog)`**——这是最关键的一步。`get_models()` 内部根据 `dialog.kb_ids` 查询所有关联知识库，获取它们的 `embd_id`（嵌入模型 ID），确保所有知识库使用同一个嵌入模型，然后通过 `LLMBundle` 类分别创建四种模型实例：`embd_mdl`（用于向量化用户问题）、`rerank_mdl`（可选，用于精排检索结果）、`chat_mdl`（用于生成最终回答）、`tts_mdl`（可选，用于语音合成）。

如果当前调用来自 Agent 组件（`toolcall_session` 和 `tools` 不为空），则调用 `chat_mdl.bind_tools(toolcall_session, tools)` 将 OpenAI Function Calling 格式的工具定义绑定到对话模型上，使其在推理时能调用外部工具。

**分支判断**：`llm_type == "image2text"` → 用 `LLMType.IMAGE2TEXT` 查配置；否则 → 用 `LLMType.CHAT` 查配置。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `dialog`（含 tenant_id、llm_id）、`kwargs`（可选 toolcall_session、tools） |
| **核心逻辑** | 判断 LLM 类型 → 从数据库获取模型配置 → `get_models()` 创建四种模型实例 → 可选绑定 Agent 工具 |
| **输出形式** | 赋值给局部变量：`embd_mdl`、`rerank_mdl`、`chat_mdl`、`tts_mdl`、`retriever` |
| **底层关键依赖** | `TenantLLMService`（数据库查询）、`LLMBundle`（模型实例封装）、`Langfuse`（可观测性） |
| **关键代码片段** | `kbs, embd_mdl, rerank_mdl, chat_mdl, tts_mdl = get_models(dialog)` |

#### 特殊处理标注
- **Langfuse 连接失败兜底**：`try-except pass` 不阻断主流程
- **工具绑定**：Agent 场景下 `chat_mdl.bind_tools(toolcall_session, tools)` 开启 Function Calling

### 3.3 方法文字流程串讲：问题提取（L497-L511）

从消息列表中提取 `role == "user"` 的消息内容，取最近 3 条作为 `questions` 列表。取 3 条的原因是保留多轮对话上下文——比如用户先问"什么是 RAG"，系统回答了，用户接着问"它有什么优势"，这第二条消息本身指代不明确，但配合前一条消息就能完整理解。

接着处理附件：`doc_ids` 可能从外部 kwargs 传入，也可能嵌入在最后一条消息的 `doc_ids` 字段中。如果有 `files` 附件，调用 `split_file_attachments()` 按文件类型分离——chat 模型下分出 `text_attachments`（文本文件内容）和 `image_attachments`（图片，用于多模态输入）；image2text 模型下用 `raw=True` 参数直接读原始文件字节。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `messages`（list[dict]，含 role/content/doc_ids/files）、`llm_type`、`kwargs` |
| **核心逻辑** | 提取最近3条用户消息 → 解析 doc_ids 和 files 附件 → 分离文本/图片附件 |
| **输出形式** | `questions`（list[str]）、`attachments`（list[str]）、`attachments_`（str）、`image_attachments`（list） |
| **底层关键依赖** | `split_file_attachments()` |
| **关键代码片段** | `questions = [m["content"] for m in messages if m["role"] == "user"][-3:]` |

### 3.4 方法文字流程串讲：SQL 检索尝试（L514-L525）

获取 `prompt_config` 和 `field_map` 后，检查是否有字段映射——如果有，说明当前知识库是结构化数据（如 Excel 表格），可以尝试用 SQL 精确查询。调用 `use_sql(questions[-1], field_map, tenant_id, chat_mdl, quote, kb_ids)`，内部流程是：把用户问题发送给 LLM → LLM 生成 SQL 语句 → 在向量数据库（Infinity/ES/OB）中执行 SQL → 返回结构化结果。

如果 SQL 查询成功（返回了 chunks 或 answer），直接 `yield ans` 给调用者然后 `return` 结束函数。如果 SQL 无结果或失败，不抛异常，只打一条 debug 日志 `"SQL failed or returned no results, falling back to vector search"`，然后继续执行模块6的向量检索。

**分支判断解读**：`field_map` 为空 → 跳过 SQL 检索；SQL 有结果 → 直接返回；SQL 无结果 → 透明降级到向量检索。这是一个"乐观尝试"的设计模式。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `questions`、`field_map`、`tenant_id`、`chat_mdl`、`kb_ids` |
| **核心逻辑** | 有字段映射→LLM生成SQL→执行→有结果直接返回→无结果降级向量检索 |
| **输出形式** | 成功时 yield 答案并 return；失败时不阻断，继续执行 |
| **底层关键依赖** | `use_sql()`（Text2SQL + SQL 执行引擎） |
| **关键代码片段** | `if field_map: ans = await use_sql(...); if ans: yield ans; return` |

#### 特殊处理标注
- **无结果不报错**：SQL 失败时只打 debug 日志，平滑降级到向量检索
- **聚合查询兼容**：`ans.get("reference", {}).get("chunks") or ans.get("answer")`，COUNT/SUM 等聚合查询可能没有 chunks 但有 answer

### 3.5 方法文字流程串讲：问题精炼优化（L527-L559）

首先 `param_keys = [p["key"] for p in prompt_config.get("parameters", [])]` 提取 Prompt 模板中声明的所有参数名。遍历这些参数，跳过 `"knowledge"`（它会在模块7自动填充），对于其他参数——如果既没有在 `kwargs` 中传入也不是可选参数（`optional=False`），直接抛 `KeyError` 异常；如果在 `kwargs` 中未传入但是可选，则用空格替换模板中的占位符 `{参数名}`。

接下来进入四个精炼子步骤：

**步骤1-多轮合并**（L538-L541）：当 `questions` 有多条（说明是多轮对话）且配置了 `refine_multiturn`，调用 `full_question(tenant_id, llm_id, messages)` 将完整对话历史发给 LLM，让 LLM 理解上下文后重构为一个独立完整的问题。如果没用多轮或没配置优化，直接用最后一条问题。

**步骤2-跨语言翻译**（L543-L544）：配置了 `cross_languages` 时，调用 `cross_languages(tenant_id, llm_id, question, target_language)` 将问题翻译为目标语言（如中文知识库用英文问题→翻译成中文）。

**步骤3-元数据过滤**（L546-L554）：配置了 `meta_data_filter` 时，获取知识库所有文档的元数据，调用 `apply_meta_data_filter()` 用 LLM 从问题中提取结构化过滤条件（如"最近一个月的销售数据"→过滤日期字段）。

**步骤4-关键词增强**（L556-L557）：配置了 `keyword` 时，调用 `keyword_extraction(chat_mdl, question)` 用 LLM 提取 3-5 个核心关键词，追加到问题末尾 `questions[-1] += keywords`。这些关键词在后续检索中会获得更高权重。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `prompt_config`、`questions`、`messages`、`dialog`、`chat_mdl`、`kwargs` |
| **核心逻辑** | 参数校验 → 多轮合并 → 跨语言翻译 → 元数据过滤 → 关键词增强 |
| **输出形式** | 更新后的 `questions`（list[str]，最终只保留1条）、更新后的 `attachments` |
| **底层关键依赖** | `full_question()`、`cross_languages()`、`apply_meta_data_filter()`、`keyword_extraction()` |
| **关键代码片段** | 分别见上方四个步骤 |

#### 特殊处理标注
- **参数校验**：required 参数缺失抛 `KeyError`，optional 参数缺省用空格替换
- **多轮合并条件**：仅 `len(questions) > 1` 时触发

### 3.6 方法文字流程串讲：核心检索执行（L561-L636）

初始化 `kbinfos = {"total": 0, "chunks": [], "doc_aggs": []}` 和空 `knowledges` 列表。如果 Prompt 模板中声明了 `"knowledge"` 参数，进入核心检索段。

先收集所有知识库的租户 ID 列表：`tenant_ids = list(set([kb.tenant_id for kb in kbs]))`。然后判断检索模式——

**分支A-深度研考模式**（`reasoning=True`）：创建 `DeepResearcher` 实例，用 `asyncio.Queue` 实现异步通信，`asyncio.create_task` 启动研考任务，主循环从队列取消息并 yield 给调用者。`<START_DEEP_RESEARCH>` 标记开始思考、`<END_DEEP_RESEARCH>` 标记结束。这是类似 OpenAI Deep Research 的功能。

**分支B-普通向量检索**：如果 `embd_mdl` 存在，调用 `retriever.retrieval()` 执行混合检索，参数包括查询文本、嵌入模型、租户ID、知识库ID、页码、top_n、相似度阈值、向量权重、文档过滤等。检索结果存入 `kbinfos`。然后依次检查可选增强：`toc_enhance` 走 TOC 目录增强检索（用 LLM 从检索结果目录树中精选相关 chunks，替换原结果）；`retrieval_by_children` 走子块展开（检索到父块后获取子块，原地替换）。

如果配置了 `tavily_api_key`，调用 Tavily API 进行网络搜索，结果追加到 `kbinfos["chunks"]`。如果配置了 `use_kg`，获取默认聊天模型，调用 `settings.kg_retriever.retrieval()` 进行知识图谱检索，结果插入到 chunks 最前面。

**分支判断解读**：reasoning 模式和普通模式是互斥的（通过 `if-else`）；TOC 增强、子块展开、Tavily、KG 都是可选追加，不是互斥关系。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `questions`、`embd_mdl`、`rerank_mdl`、`chat_mdl`、`dialog`（含 kb_ids/top_n/top_k/similarity_threshold/vector_similarity_weight）、`prompt_config`、`attachments`、`kbs` |
| **核心逻辑** | reasoning模式→DeepResearcher；普通模式→retriever.retrieval() + 可选增强（TOC/子块/Tavily/KG） |
| **输出形式** | `kbinfos = {"total": N, "chunks": [...], "doc_aggs": {...}}` 或 reasoning 模式下 yield 思考过程 |
| **底层关键依赖** | `settings.retriever`（全局 Dealer 实例）、`Tavily`（网络搜索）、`settings.kg_retriever`（KG 检索） |
| **关键代码片段** | `kbinfos = await retriever.retrieval(" ".join(questions), embd_mdl, tenant_ids, dialog.kb_ids, 1, dialog.top_n, ...)` |

#### 特殊处理标注
- **并行追加**：向量检索、Tavily、KG 的结果是追加关系（`extend` / `insert`），不是覆盖
- **TOC 替换**：TOC 增强是替换（`kbinfos["chunks"] = cks`），因为 LLM 精选的结果更精准

### 3.7 方法文字流程串讲：Prompt 构建（L638-L667）

调用 `kb_prompt(kbinfos, max_tokens)` 将检索结果格式化为 LLM 可理解的知识块字符串列表。每个知识块包含文档名、URL、发布时间等元数据，按 token 限制截断。

如果没有结果且配置了 `empty_response`，直接返回预设的空回答，不调用 LLM。有结果时，将知识块用分隔符 `"\n------\n"` 拼接起来赋值给 `kwargs["knowledge"]`，再通过 `prompt_config["system"].format(**kwargs)` 填充到系统 Prompt 模板中，构建系统消息 `msg = [{"role": "system", "content": ...}]`。

如果有知识块且配置了引用要求，调用 `citation_prompt()` 生成引用提示词，要求 LLM 在回答中标注 `[ID:n]` 格式的引用。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `kbinfos`、`max_tokens`、`prompt_config`、`kwargs` |
| **核心逻辑** | kb_prompt() 格式化知识 → 填充Prompt模板 → citation_prompt() 生成引用要求 |
| **输出形式** | `msg`（list[dict]，首条为 system 消息）、`prompt4citation`（str，引用提示词） |
| **底层关键依赖** | `kb_prompt()`、`citation_prompt()`（位于 `rag/prompts/generator.py`） |
| **关键代码片段** | `msg = [{"role": "system", "content": prompt_config["system"].format(**kwargs)}]` |

### 3.8 方法文字流程串讲：LLM 流式调用与结果返回（L655-L781）

将对话历史中的非 system 消息追加到 `msg` 列表：`msg.extend([{"role": m["role"], "content": ...} for m in messages if m["role"] != "system"])`。调用 `message_fit_in(msg, int(max_tokens * 0.95))` 裁剪消息——从最早的消息开始删除直到总 token 数 < 95% 上限。如果是 chat 模型且有图片附件，调用 `convert_last_user_msg_to_multimodal()` 把最后一条用户消息转为多模态格式。最后 `assert len(msg) >= 2` 确保至少有一条 system + 一条 user 消息。

**流式模式**（`stream=True`）：调用 `chat_mdl.async_chat_streamly_delta(prompt + prompt4citation, msg[1:], gen_conf)` 获取流式迭代器，然后 `async for kind, value, state in _stream_with_think_delta(stream_iter)` 逐块处理。`_stream_with_think_delta()` 内部检测 `<think>` 和 `</think>` 标签——遇到 `<think>` 进入思考模式、遇到 `</think>` 退出思考模式，思考内容单独存储到消息的 `think` 字段。每块数据通过 `yield {"answer": value, "reference": {}, ...}` 返回。

**非流式模式**（`stream=False`）：调用 `chat_mdl.async_chat(prompt + prompt4citation, msg[1:], gen_conf)` 一次性获取完整答案，然后 `yield` 最终结果。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `msg`、`max_tokens`、`stream`、`chat_mdl`、`gen_conf`、`prompt`、`prompt4citation`、`tts_mdl` |
| **核心逻辑** | 裁剪消息→追加引用提示词→流式/非流式调用LLM→解析<think>标签→yield答案块 |
| **输出形式** | yield dict：`{"answer": str, "reference": {}, "audio_binary": bytes|None, "final": bool}` |
| **底层关键依赖** | `chat_mdl.async_chat_streamly_delta()`、`_stream_with_think_delta()`、`message_fit_in()` |
| **关键代码片段** | `async for kind, value, state in _stream_with_think_delta(stream_iter): yield {...}` |

#### 特殊处理标注
- **Token 裁剪**：`int(max_tokens * 0.95)` 留 5% 余量给生成的输出
- **多模态转换**：只有 chat 模型才做 `convert_last_user_msg_to_multimodal()`
- **思考标签解析**：`_stream_with_think_delta()` 独立处理 DeepSeek-R1 等模型的 reasoning

---

## 四、同类逻辑对比表

| 功能名称 | 核心流程 | 入参 | 底层依赖 API | 输出格式 | 差异场景 |
|----------|----------|------|-------------|---------|---------|
| `async_chat_solo()` | 纯 LLM 对话，不检索 | dialog, messages, stream | `chat_mdl.chat()` | 流式/非流式答案 | 无知识库时使用 |
| `use_sql()` | LLM→SQL→执行→结果 | question, field_map, tenant_id, chat_mdl | Infinity/ES SQL 引擎 | 结构化答案 + chunks | 结构化知识库时优先 |
| `retriever.retrieval()` | 混合检索（全文+向量）→重排 | question, embd_mdl, kb_ids, ... | ES/Infinity + Embedding | `{"chunks":[], "doc_aggs":{}}` | 非结构化知识库，默认路径 |
| `retriever.retrieval_by_toc()` | 目录增强检索 | question, chunks, chat_mdl | LLM + ES | 精选后的 chunks | TOC 增强模式 |
| `Tavily.retrieve_chunks()` | 网络搜索 | question | Tavily API | chunks + doc_aggs | 需要实时网络信息 |
| `settings.kg_retriever.retrieval()` | 知识图谱检索 | question, tenant_ids, kb_ids, embd_mdl | KG 检索引擎 + LLM | 单个 KG chunk | 有知识图谱配置时 |

---

## 五、疑惑解答

**Q1：为什么 `questions` 取最近 3 条而不是全部？**

用户可能进行了多轮对话（例如先问概念再追问细节），取最近 3 条可以捕捉足够的上下文进行指代消解（如"它有什么优势"→需要知道"它"指什么）。取全部会导致检索内容过多、噪音大；取 1 条则丢失关键上下文。3 条是工程经验值。

**Q2：`message_fit_in(msg, int(max_tokens * 0.95))` 为什么是 95% 而不是 100%？**

留 5% 余量给 LLM 输出。如果输入占用 100%，LLM 就没有空间生成回答了。95% 是一个安全的经验值。

**Q3：为什么 SQL 检索失败不抛异常而是静默降级？**

因为在 RAGFlow 的设计中，SQL 检索是"乐观尝试"——只在结构化知识库时有望成功。非结构化文档即使配置了字段映射，SQL 也可能无法覆盖用户意图。静默降级保证了用户体验的一致性：用户不需要关心背后用的是 SQL 还是向量检索，只要有答案就行。

---

## 六、规范修正

- "嵌入模型"统一使用"Embedding 模型"
- "检索增强生成"正确缩写为"RAG"
- "重排序"统一指 "rerank"
- "TOC" 指 "Table of Contents"（文档目录）
- "KG" 指 "Knowledge Graph"（知识图谱）

---

## 七、可复现实操步骤

| 步骤 | 操作内容 | 依赖 API / 模块 | 最简代码 | 注意事项 |
|------|----------|----------------|---------|---------|
| 1 | 准备 Dialog 和 messages | DB 查询 | `e, dia = DialogService.get_by_id(dialog_id)` | Dialog 需含 kb_ids 和 llm_id |
| 2 | 获取模型 | `TenantLLMService` | `kbs, embd_mdl, rerank_mdl, chat_mdl, tts_mdl = get_models(dia)` | 确保 Tenant 已配置 LLM |
| 3 | 提取问题 | 列表推导 | `qs = [m["content"] for m in msgs if m["role"]=="user"][-3:]` | 最后一条必须是 user |
| 4 | 检索 | `settings.retriever` | `kbinfos = await retriever.retrieval(q, embd_mdl, tids, kb_ids, ...)` | 需要 ES/Infinity 运行中 |
| 5 | 构建 Prompt | `kb_prompt()` | `knowledges = kb_prompt(kbinfos, max_tokens)` | 确保模板中有 {knowledge} |
| 6 | 调用 LLM | `chat_mdl` | `async for ans in async_chat(dia, msgs, True): yield ans` | 完整的 `async_chat()` 一次调用即可 |

---

## 八、关键模块总览

| 模块名称 | 负责功能 | 在流程中的核心作用 |
|----------|----------|-------------------|
| `TenantLLMService` | 读取租户配置的 LLM 模型 | 提供 chat/embed/rerank/tts 四种模型配置 |
| `LLMBundle` | 统一封装 LLM 调用接口 | 屏蔽不同厂商 API 的差异，提供统一的 chat/encode 方法 |
| `settings.retriever` | 全局检索器（Dealer 类） | 执行混合检索（全文+向量）、重排序、TOC 增强 |
| `rag.prompts.generator` | Prompt 构建工具集 | kb_prompt()/citation_prompt()/message_fit_in()/full_question() 等 |
| `Dealer`（`rag/nlp/search.py`） | 检索核心控制器 | search() 混合检索 + retrieval() 精排全链路 + insert_citations() 引用插入 |
| `FulltextQueryer`（`rag/nlp/query.py`） | 全文查询构造 | question() 将自然语言转为 ES/Infinity 查询表达式 |
| `Dealer`（`rag/nlp/term_weight.py`） | 词权重计算 | weights() 计算 IDF×NER×POS 动态词权重 |
| `Dealer`（`rag/nlp/synonym.py`） | 同义词查找 | lookup() 三级查找（自定义词典→WordNet→Redis） |
| `Elasticsearch/Infinity/OceanBase` | 向量数据库后端 | 存储文档向量、执行向量+全文混合检索 |
| `_stream_with_think_delta()` | 流式响应解析 | 检测 `<think>`/`</think>` 标签，分离思考与回答 |
