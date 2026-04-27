# RAGFlow RAG & Agent 模块实现流程完全详解

> **文档目标**：通过逐行级别源码解读，完整掌握 RAGFlow 中 RAG 检索全链路和 Agent 编排引擎的具体实现方式
> **阅读建议**：建议配合 IDE 打开对应源文件，按文档标注的行号跳转阅读

---

## 目录

- [第一篇：RAG 检索全链路实现详解](#第一篇rag-检索全链路实现详解)
  - [第一章：对话入口 —— async_chat() 全流程](#第一章对话入口--async_chat-全流程)
  - [第二章：混合检索 —— Dealer.search()](#第二章混合检索--dealersearch)
  - [第三章：查询构造 —— FulltextQueryer.question()](#第三章查询构造--fulltextqueryerquestion)
  - [第四章：词权重计算 —— term_weight.weights()](#第四章词权重计算--term_weightweights)
  - [第五章：检索精排 —— Dealer.retrieval() 与 rerank()](#第五章检索精排--dealerretrieval-与-rerank)
  - [第六章：引用溯源 —— insert_citations()](#第六章引用溯源--insert_citations)
  - [第七章：Prompt 构建与 LLM 调用](#第七章prompt-构建与-llm-调用)

- [第二篇：Agent 编排引擎实现详解](#第二篇agent-编排引擎实现详解)
  - [第八章：DSL 定义与 Graph 加载](#第八章dsl-定义与-graph-加载)
  - [第九章：Canvas 执行引擎 —— run()](#第九章canvas-执行引擎--run)
  - [第十章：Agent 智能体 —— invoke_async()](#第十章agent-智能体--invoke_async)
  - [第十一章：Tool Call 全链路](#第十一章tool-call-全链路)
  - [第十二章：流式输出与事件系统](#第十二章流式输出与事件系统)

---

# 第一篇：RAG 检索全链路实现详解

---

## 第一章：对话入口 —— async_chat() 全流程

**文件位置**：`api/db/services/dialog_service.py` L455-L781

这是 RAGFlow 中 RAG 对话的**总调度函数**，一个异步生成器，逐步 yield 答案给调用者。

### 1.1 前置校验与路由 (L455-L461)

```python
async def async_chat(dialog, messages, stream=True, **kwargs):
    logging.debug("Begin async_chat")
    # 断言：消息列表的最后一条必须是用户消息
    assert messages[-1]["role"] == "user", "The last content ... is not from user."

    # 路由判断：无知识库且无 Tavily 搜索 API → 纯对话模式
    if not dialog.kb_ids and not dialog.prompt_config.get("tavily_api_key"):
        async for ans in async_chat_solo(dialog, messages, stream):
            yield ans
        return
```

**实现细节**：
- `async_chat_solo()` 是纯 LLM 对话模式，不做任何检索，直接将消息发送给 LLM 生成回答
- 这里的 `yield` 是异步生成器的语法，每 yield 一次就向调用者推送一块数据（用于 SSE 流式响应）

---

### 1.2 模型配置获取 (L463-L494)

```python
    chat_start_ts = timer()                      # 记录开始时间
    llm_type = TenantLLMService.llm_id2llm_type(dialog.llm_id)
    # 根据 LLM 类型（chat/image2text）获取模型配置
    if llm_type == "image2text":
        llm_model_config = TenantLLMService.get_model_config(
            dialog.tenant_id, LLMType.IMAGE2TEXT, dialog.llm_id)
    else:
        llm_model_config = TenantLLMService.get_model_config(
            dialog.tenant_id, LLMType.CHAT, dialog.llm_id)

    factory = llm_model_config.get("llm_factory", "")  # 如 "dashscope"
    max_tokens = llm_model_config.get("max_tokens", 8192)
```

**数据来源**：
- `dialog.llm_id` → 例如 `"qwen-turbo-latest"`
- `TenantLLMService.llm_id2llm_type()` → 查询 `tenant_llm` 表，返回 `"chat"` 或 `"image2text"`
- `TenantLLMService.get_model_config()` → 查询 `tenant_llm` 表，返回完整模型配置 dict

```python
    # Langfuse 追踪（可选，如果配置了）
    langfuse_tracer = None
    langfuse_keys = TenantLangfuseService.filter_by_tenant(tenant_id=dialog.tenant_id)
    if langfuse_keys:
        langfuse = Langfuse(public_key=..., secret_key=..., host=...)
        if langfuse.auth_check():
            langfuse_tracer = langfuse
            trace_context = {"trace_id": langfuse_tracer.create_trace_id()}

    # 获取所有模型实例
    kbs, embd_mdl, rerank_mdl, chat_mdl, tts_mdl = get_models(dialog)
    # 绑定工具（来自 Agent 调用）
    toolcall_session, tools = kwargs.get("toolcall_session"), kwargs.get("tools")
    if toolcall_session and tools:
        chat_mdl.bind_tools(toolcall_session, tools)

    retriever = settings.retriever  # 全局检索器实例（Dealer类）
```

**`get_models()` 函数逻辑**：
1. 通过 `dialog.kb_ids` 查询所有关联的知识库
2. 从每个知识库获取 `embd_id`（嵌入模型ID），确保所有 KB 使用相同的嵌入模型
3. 通过 `LLMBundle` 类创建嵌入模型、重排序模型、对话模型、TTS 模型的实例
4. 返回 `(kbs, embd_mdl, rerank_mdl, chat_mdl, tts_mdl)` 五元组

---

### 1.3 问题提取与附件处理 (L497-L511)

```python
    # 提取最近3条用户消息作为问题列表
    questions = [m["content"] for m in messages if m["role"] == "user"][-3:]

    attachments = None
    if "doc_ids" in kwargs:
        attachments = [doc_id for doc_id in kwargs["doc_ids"].split(",") if doc_id]
    if "doc_ids" in messages[-1]:
        attachments = [doc_id for doc_id in messages[-1]["doc_ids"] if doc_id]
    if "files" in messages[-1]:
        if llm_type == "chat":
            text_attachments, image_attachments = split_file_attachments(
                messages[-1]["files"])
        else:
            text_attachments, image_files = split_file_attachments(
                messages[-1]["files"], raw=True)
        attachments_ = "\n\n".join(text_attachments)
```

**设计要点**：提取最近3条消息的原因是**保留多轮对话上下文**——用户可能追问，前面会话的信息也需要参与检索。

---

### 1.4 SQL 检索尝试 (L514-L525)

```python
    prompt_config = dialog.prompt_config
    field_map = KnowledgebaseService.get_field_map(dialog.kb_ids)

    if field_map:
        # 如果知识库有字段映射（结构化数据），先尝试 SQL 检索
        ans = await use_sql(questions[-1], field_map, dialog.tenant_id,
                            chat_mdl, prompt_config.get("quote", True),
                            dialog.kb_ids)
        if ans and (ans.get("reference", {}).get("chunks") or ans.get("answer")):
            yield ans   # SQL 检索成功，直接返回
            return
        # SQL 失败 → 降级到向量检索
```

**设计亮点**：**Text2SQL → 向量检索的降级策略**。当知识库是结构化数据（如 Excel 表格），LLM 先生成 SQL 精确查询；如果 SQL 无结果或失败，自动降级到语义向量检索。

---

### 1.5 问题精炼优化 (L527-L559)

```python
    # 参数校验：检查 Prompt 模板中声明的参数是否都已传入
    param_keys = [p["key"] for p in prompt_config.get("parameters", [])]
    for p in prompt_config["parameters"]:
        if p["key"] == "knowledge": continue         # knowledge 会由系统自动填充
        if p["key"] not in kwargs and not p["optional"]:
            raise KeyError("Miss parameter: " + p["key"])
        if p["key"] not in kwargs:
            # 未传入的参数从模板中移除占位符
            prompt_config["system"] = prompt_config["system"].replace(
                "{%s}" % p["key"], " ")

    # 多轮对话优化：将多轮上下文整合为一个完整问题
    if len(questions) > 1 and prompt_config.get("refine_multiturn"):
        questions = [await full_question(dialog.tenant_id, dialog.llm_id, messages)]
    else:
        questions = questions[-1:]   # 只用最后一条问题

    # 跨语言翻译（可选）
    if prompt_config.get("cross_languages"):
        questions = [await cross_languages(dialog.tenant_id, dialog.llm_id,
                      questions[0], prompt_config["cross_languages"])]

    # 元数据过滤（可选）
    if dialog.meta_data_filter:
        metas = DocMetadataService.get_flatted_meta_by_kbs(dialog.kb_ids)
        attachments = await apply_meta_data_filter(
            dialog.meta_data_filter, metas, questions[-1], chat_mdl, attachments)

    # 关键词提取增强（可选）
    if prompt_config.get("keyword", False):
        questions[-1] += await keyword_extraction(chat_mdl, questions[-1])
```

**四个优化策略详解**：

1. **`full_question()`（多轮优化）**：将整个对话历史发送给 LLM，让 LLM 理解上下文后把追问重构为独立完整问题。
   - 输入：`[Q1, A1, Q2("它有什么优势？")]`
   - 输出：`"RAG（检索增强生成）技术相比纯LLM有哪些优势和特点？"`

2. **`cross_languages()`（跨语言）**：检测问题语言和知识库语言不匹配时，将问题翻译为知识库语言。例如中文问题 + 英文知识库 → 翻译成英文再检索。

3. **`apply_meta_data_filter()`（元数据过滤）**：对结构化知识库，用 LLM 从自然语言问题中提取筛选条件。例如"最近一个月的销售数据" → 自动过滤日期字段。

4. **`keyword_extraction()`（关键词增强）**：用 LLM 从问题中提取 3-5 个核心关键词，追加到问题末尾增强检索权重。

---

### 1.6 核心检索执行 (L561-L636)

```python
    if "knowledge" in param_keys:
        logging.debug("Proceeding with retrieval")
        tenant_ids = list(set([kb.tenant_id for kb in kbs]))

        # 分支1：深度研究模式
        if prompt_config.get("reasoning", False) or kwargs.get("reasoning"):
            reasoner = DeepResearcher(chat_mdl, prompt_config, ...)
            # 使用 asyncio.Queue 异步通信
            queue = asyncio.Queue()
            async def callback(msg: str):
                await queue.put(msg + "<br/>")

            await callback("<START_DEEP_RESEARCH>")
            task = asyncio.create_task(
                reasoner.research(kbinfos, questions[-1], questions[-1],
                                  callback=callback))
            while True:
                msg = await queue.get()
                if msg.find("<START_DEEP_RESEARCH>") == 0:
                    yield {"answer": "", ..., "start_to_think": True}
                elif msg.find("<END_DEEP_RESEARCH>") == 0:
                    yield {"answer": "", ..., "end_to_think": True}
                    break
                else:
                    yield {"answer": msg, ..., "final": False}
            await task

        # 分支2：普通向量检索模式
        else:
            if embd_mdl:
                # ============ 核心检索调用 ============
                kbinfos = await retriever.retrieval(
                    " ".join(questions),      # 查询文本（多条问题合并）
                    embd_mdl,                 # 嵌入模型
                    tenant_ids,               # 租户ID列表
                    dialog.kb_ids,            # 知识库ID列表
                    1,                        # 页码
                    dialog.top_n,             # 返回数量
                    dialog.similarity_threshold,     # 相似度阈值
                    dialog.vector_similarity_weight, # 向量权重
                    doc_ids=attachments,      # 指定文档ID过滤
                    top=dialog.top_k,         # 初始召回数
                    aggs=True,                # 按文档聚合
                    rerank_mdl=rerank_mdl,    # 重排序模型
                    rank_feature=label_question(" ".join(questions), kbs),
                )

                # TOC 目录增强检索（可选）
                if prompt_config.get("toc_enhance"):
                    cks = await retriever.retrieval_by_toc(
                        " ".join(questions), kbinfos["chunks"],
                        tenant_ids, chat_mdl, dialog.top_n)
                    if cks:
                        kbinfos["chunks"] = cks

                # 子块展开（可选）：检索到父块后，获取其子块
                kbinfos["chunks"] = retriever.retrieval_by_children(
                    kbinfos["chunks"], tenant_ids)

            # Tavily 网络搜索补充（可选）
            if prompt_config.get("tavily_api_key"):
                tav = Tavily(prompt_config["tavily_api_key"])
                tav_res = tav.retrieve_chunks(" ".join(questions))
                kbinfos["chunks"].extend(tav_res["chunks"])
                kbinfos["doc_aggs"].extend(tav_res["doc_aggs"])

            # 知识图谱 (KG) 检索补充（可选）
            if prompt_config.get("use_kg"):
                default_chat_model = get_tenant_default_model_by_type(
                    dialog.tenant_id, LLMType.CHAT)
                ck = await settings.kg_retriever.retrieval(
                    " ".join(questions), tenant_ids, dialog.kb_ids,
                    embd_mdl, LLMBundle(dialog.tenant_id, default_chat_model))
                if ck["content_with_weight"]:
                    kbinfos["chunks"].insert(0, ck)
```

**并行判断逻辑**：
- 向量检索和网络搜索、知识图谱检索**同层追加**：`chunks.extend()`，不是二选一
- TOC 增强**替换**原 chunks：`kbinfos["chunks"] = cks`
- 子块展开**原地替换**：`kbinfos["chunks"] = retriever.retrieval_by_children(...)`

---

### 1.7 Prompt 构建 (L638-L667)

```python
    # 将检索结果格式化为 LLM 可用的知识块
    knowledges = kb_prompt(kbinfos, max_tokens)

    # 无结果处理
    if not knowledges and prompt_config.get("empty_response"):
        empty_res = prompt_config["empty_response"]
        yield {"answer": empty_res, "reference": kbinfos, ...}
        return

    # 拼接知识到 kwargs，供 Prompt 模板使用
    kwargs["knowledge"] = "\n------\n" + "\n\n------\n\n".join(knowledges)
    gen_conf = dialog.llm_setting

    # 构建系统消息
    msg = [{"role": "system", "content": prompt_config["system"].format(**kwargs)}]

    # 引用提示词（双重保障之一）
    prompt4citation = ""
    if knowledges and prompt_config.get("quote", True):
        prompt4citation = citation_prompt()
```

**`kb_prompt()` 的格式化逻辑**：
1. 按 `doc_id` 将 chunks 分组
2. 每组生成文档头部（文档名、URL、发布时间）
3. 依次追加 chunk 内容，按 token 限制截断
4. 返回格式化后的知识块字符串列表

---

### 1.8 LLM 调用与引用后处理 (L655-L781)

```python
    # 扩展消息列表（保留用户和助手的历史消息）
    msg.extend([{"role": m["role"], "content": m["content"]}
                for m in messages if m["role"] != "system"])

    # Token 裁剪：确保不超过模型限制的 95%
    used_token_count, msg = message_fit_in(msg, int(max_tokens * 0.95))

    # 流式生成
    if stream:
        stream_iter = chat_mdl.async_chat_streamly_delta(
            prompt + prompt4citation, msg[1:], gen_conf)
        async for kind, value, state in _stream_with_think_delta(stream_iter):
            yield {"answer": value, "reference": {}, "audio_binary": tts(tts_mdl, value)}
```

**`_stream_with_think_delta()` 处理 DeepSeek-R1 等模型的 `<think>` 标签**：
- 遇到 `` → 流式输出思考内容（显示为折叠面板）
- 遇到 `` → 切换到正常输出模式

---

## 第二章：混合检索 —— Dealer.search()

**文件位置**：`rag/nlp/search.py` L74-L171

### 2.1 初始化与数据结构 (L36-L61)

```python
class Dealer:
    def __init__(self, dataStore: DocStoreConnection):
        self.qryr = query.FulltextQueryer()  # 全文查询构造器
        self.dataStore = dataStore           # 向量数据库连接（ES/Infinity/OB）

    @dataclass
    class SearchResult:
        total: int
        ids: list[str]
        query_vector: list[float] | None = None
        field: dict | None = None
        highlight: dict | None = None
        aggregation: list | dict | None = None
        keywords: list[str] | None = None
        group_docs: list[list] | None = None
```

### 2.2 向量查询构造 (L52-L60)

```python
    async def get_vector(self, txt, emb_mdl, topk=10, similarity=0.1):
        # 调用 Embedding 模型，在 thread_pool_exec 中执行（避免阻塞事件循环）
        qv, _ = await thread_pool_exec(emb_mdl.encode_queries, txt)
        shape = np.array(qv).shape
        if len(shape) > 1:
            raise Exception("shape mismatch.")

        embedding_data = [get_float(v) for v in qv]
        # 列名按维度动态生成：q_{dim}_vec，如 q_768_vec
        vector_column_name = f"q_{len(embedding_data)}_vec"
        return MatchDenseExpr(
            vector_column_name, embedding_data, 'float', 'cosine',
            topk, {"similarity": similarity})
```

**设计要点**：向量列名动态生成的原因
- 不同 Embedding 模型产出不同维度：BERT 768维、OpenAI 1536维、BGE 1024维
- 向量数据库中每列对应一个维度：`q_768_vec`、`q_1536_vec`
- 同一知识库中可能混存不同维度的向量

### 2.3 过滤器构造 (L62-L72)

```python
    def get_filters(self, req):
        condition = dict()
        # kb_ids → kb_id 过滤
        for key, field in {"kb_ids": "kb_id", "doc_ids": "doc_id"}.items():
            if key in req and req[key] is not None:
                condition[field] = req[key]
        # 知识图谱相关过滤
        for key in ["knowledge_graph_kwd", "available_int", "entity_kwd",
                     "from_entity_kwd", "to_entity_kwd", "removed_kwd"]:
            if key in req and req[key] is not None:
                condition[key] = req[key]
        return condition
```

### 2.4 search() 主方法 —— 三分支检索

```python
    async def search(self, req, idx_names, kb_ids, emb_mdl=None, ...):
        filters = self.get_filters(req)
        orderBy = OrderByExpr()

        # 分页参数
        pg = int(req.get("page", 1)) - 1
        topk = int(req.get("topk", 1024))
        ps = int(req.get("size", topk))
        offset, limit = pg * ps, ps

        # 默认返回字段
        src = req.get("fields", [
            "docnm_kwd", "content_ltks", "kb_id", "img_id",
            "title_tks", "important_kwd", "position_int",
            "doc_id", "page_num_int", "top_int", "create_timestamp_flt",
            "content_with_weight", "mom_id", PAGERANK_FLD, TAG_FLD
        ])
```

**三种检索分支**：

```python
        qst = req.get("question", "")

        # ====== 分支1：无问题 → 浏览检索 ======
        if not qst:
            # 按 doc_ids 获取全部 chunks，按页码+位置排序
            orderBy.asc("page_num_int").asc("top_int").desc("create_timestamp_flt")
            res = self.dataStore.search(src, [], filters, [],
                                        orderBy, offset, limit,
                                        idx_names, kb_ids)
            total = self.dataStore.get_total(res)

        else:
            matchText, keywords = self.qryr.question(qst, min_match=0.3)

            # ====== 分支2：纯全文检索（无 Embedding 模型） ======
            if emb_mdl is None:
                matchExprs = [matchText]
                res = await thread_pool_exec(
                    self.dataStore.search, src, highlightFields, filters,
                    matchExprs, orderBy, offset, limit,
                    idx_names, kb_ids, rank_feature=rank_feature)
                total = self.dataStore.get_total(res)

            # ====== 分支3：混合检索 ======
            else:
                # 构造向量查询
                matchDense = await self.get_vector(qst, emb_mdl, topk,
                                                    req.get("similarity", 0.1))
                q_vec = matchDense.embedding_data

                # 融合表达式：weighted_sum 加权求和
                fusionExpr = FusionExpr("weighted_sum", topk,
                                         {"weights": "0.05,0.95"})
                #          全文权重5%    向量权重95%

                matchExprs = [matchText, matchDense, fusionExpr]

                res = await thread_pool_exec(
                    self.dataStore.search, src, highlightFields, filters,
                    matchExprs, orderBy, offset, limit,
                    idx_names, kb_ids, rank_feature=rank_feature)
                total = self.dataStore.get_total(res)

                # ====== 降级检索：无结果时降低阈值 ======
                if total == 0:
                    if filters.get("doc_id"):
                        # 指定文档时去掉所有检索条件
                        res = await thread_pool_exec(
                            self.dataStore.search, src, [], filters, [],
                            orderBy, offset, limit, idx_names, kb_ids)
                        total = self.dataStore.get_total(res)
                    else:
                        # 降低阈值重试
                        matchText, _ = self.qryr.question(qst, min_match=0.1)
                        matchDense.extra_options["similarity"] = 0.17
                        res = await thread_pool_exec(
                            self.dataStore.search, src, highlightFields,
                            filters, [matchText, matchDense, fusionExpr],
                            orderBy, offset, limit,
                            idx_names, kb_ids, rank_feature=rank_feature)
                        total = self.dataStore.get_total(res)
```

**降级策略总结**：

| 层级 | min_match | similarity | 触发条件 |
|------|-----------|------------|----------|
| 第一轮 | 0.3 | 0.1 | 正常流程 |
| 降级轮 | 0.1 | 0.17 | 第一轮 total=0 |
| 兜底轮 | - | - | 指定 doc_ids，无过滤 |

**设计思想**：宁可多召回再精排，不可漏掉相关文档。

### 2.5 关键词收集 (L149-L156)

```python
            for k in keywords:
                kwds.add(k)
                for kk in rag_tokenizer.fine_grained_tokenize(k).split():
                    if len(kk) < 2: continue
                    if kk in kwds: continue
                    kwds.add(kk)
```

对每个关键词进行细粒度分词，将所有子词也加入关键词集合。这用于后续的 highlight 和引用匹配。

### 2.6 结果构建 (L158-L171)

```python
        ids = self.dataStore.get_doc_ids(res)
        keywords = list(kwds)
        highlight = self.dataStore.get_highlight(res, keywords,
                                                  "content_with_weight")
        aggs = self.dataStore.get_aggregation(res, "docnm_kwd")
        return self.SearchResult(
            total=total,
            ids=ids,
            query_vector=q_vec,
            aggregation=aggs,
            highlight=highlight,
            field=self.dataStore.get_fields(res, src + ["_score"]),
            keywords=keywords
        )
```

---

## 第三章：查询构造 —— FulltextQueryer.question()

**文件位置**：`rag/nlp/query.py` L27-L168

### 3.1 初始化与查询字段

```python
class FulltextQueryer(QueryBase):
    def __init__(self):
        self.tw = term_weight.Dealer()     # 词权重计算器
        self.syn = synonym.Dealer()        # 同义词查找器
        self.query_fields = [
            "title_tks^10",                # 标题 token，权重 10
            "title_sm_tks^5",              # 细粒度标题，权重 5
            "important_kwd^30",            # 重要关键词，权重 30
            "important_tks^20",            # 重要 token，权重 20
            "question_tks^20",             # 问题 token，权重 20
            "content_ltks^2",              # 内容粗粒度，权重 2
            "content_sm_ltks",             # 内容细粒度，权重 1
        ]
```

**字段权重设计**：重要关键词权重最高（30），内容权重最低（2），体现"标题匹配 > 正文匹配"的思想。

### 3.2 英文查询处理 (L58-L92)

```python
    def question(self, txt, min_match=0.6):
        if not self.is_chinese(txt):       # 英文路径
            tks = rag_tokenizer.tokenize(txt).split()
            keywords = [t for t in tks if t]

            # 计算每个词的权重
            tks_w = self.tw.weights(tks, preprocess=False)

            # 清理特殊字符
            tks_w = [(re.sub(r"[ \\\"'^]", "", tk), w) for tk, w in tks_w]
            tks_w = [(re.sub(r"^[\+-]", "", tk), w) for tk, w in tks_w if tk]

            # 同义词扩展
            syns = []
            for tk, w in tks_w[:256]:
                syn = [rag_tokenizer.tokenize(s) for s in self.syn.lookup(tk)]
                keywords.extend(syn)
                syn = ["\"{}\"^{:.4f}".format(s, w / 4.) for s in syn if s.strip()]
                syns.append(" ".join(syn))

            # 构建加权 OR 表达式
            q = ["({}^{:.4f} {})".format(tk, w, syn)
                 for (tk, w), syn in zip(tks_w, syns)
                 if tk and not re.match(r"[.^+\(\)-]", tk)]

            # Bigram 短语增强
            for i in range(1, len(tks_w)):
                left, right = tks_w[i-1][0].strip(), tks_w[i][0].strip()
                if not left or not right: continue
                q.append('"%s %s"^%.4f' % (
                    tks_w[i-1][0], tks_w[i][0],
                    max(tks_w[i-1][1], tks_w[i][1]) * 2))

            query = " ".join(q)
            return MatchTextExpr(
                self.query_fields, query, 100,
                {"original_query": original_query}), keywords
```

**生成的查询表达式示例**：
```
(title_tks^10:(RAG^0.85),   important_kwd^30:(检索增强生成^0.92),
 question_tks^20:(..."
```

### 3.3 中文查询处理 (L94-L168)

中文处理比英文更复杂，核心差异是使用了**细粒度分词**：

```python
        def need_fine_grained_tokenize(tk):
            # 长度<3 → 不切分（避免过度切分）
            if len(tk) < 3: return False
            # 纯数字/字母/符号 → 不切分
            if re.match(r"[0-9a-z\.\+#_\*-]+$", tk): return False
            return True

        for tt in self.tw.split(txt)[:256]:
            if not tt: continue
            keywords.append(tt)
            twts = self.tw.weights([tt])

            # 同义词扩展
            syns = self.syn.lookup(tt)
            if syns and len(keywords) < 32:
                keywords.extend(syns)

            tms = []
            for tk, w in sorted(twts, key=lambda x: x[1] * -1):
                # 细粒度分词：将"检索增强生成"切为 ["检索","增强","生成",
                #                              "检索增强","增强生成","检索增强生成"]
                sm = (rag_tokenizer.fine_grained_tokenize(tk).split()
                      if need_fine_grained_tokenize(tk) else [])

                # 清理特殊字符
                sm = [re.sub(r"[ ,\./;'\[\]\\`~!@#$%\^&\*\(\)=\+_<>\?:...]", "", m)
                      for m in sm]
                sm = [m for m in sm if len(m) > 1]

                tk_syns = self.syn.lookup(tk)

                # 构造查询：词本身 OR 同义词 OR 细粒度短语
                if sm:
                    tk = f'{tk} OR "%s" OR ("%s"~2)^0.5' % (
                        " ".join(sm), " ".join(sm))
```

**细粒度分词的价值**：
- 输入"检索增强生成" → 输出 `["检索", "增强", "生成", "检索增强", "增强生成", "检索增强生成"]`
- 好处：即使用户问的是"增强检索"，也能匹配到包含"检索增强"的文档

---

## 第四章：词权重计算 —— term_weight.weights()

**文件位置**：`rag/nlp/term_weight.py` L1-L247

### 4.1 资源加载

```python
class Dealer:
    def __init__(self):
        # 加载 NER 命名实体字典
        self.ner = json.load(open("rag/res/ner.json"))  # {词: (实体类型, 词频, 文档数)}
        # 加载词频统计字典
        self.dictionary = json.load(open("rag/res/term.freq"))
        self.total_freq = sum(v[0] for v in self.dictionary.values())
        self.total_docs = sum(v[1] for v in self.dictionary.values())
```

### 4.2 核心权重计算公式

```python
    def weights(self, tokens, prepro):
        weights = []
        queries = []

        for t in tokens:
            # 查找 NER 和词性信息
            ner_tag, freq, docs = self.ner.get(t, ("other", 0, 0))
            postag_tag = self.dictionary.get(t, ("default", 0, 0))[0]

            # ===== 步骤1：计算两个 IDF =====
            idf1 = math.log((docs * 10 + 1) / self.total_docs)
            #        文档频 IDF：包含该词的文档越多，IDF越低

            idf2 = math.log((freq * 10 + 1) / self.total_freq)
            #        词频 IDF：该词出现频率越高，IDF越低

            # ===== 步骤2：混合 IDF (30% 文档频 + 70% 词频) =====
            idf = 0.3 * idf1 + 0.7 * idf2

            # ===== 步骤3：NER 命名实体加权 =====
            ner_coeff = {
                "toxic": 2,   # 有害词（需要检测的内容）
                "func": 1,    # 功能词（普通处理）
                "corp": 3,    # 公司名（重要）
                "loca": 3,    # 地名（重要）
                "sch": 3,     # 学校名（重要）
                "stock": 3,   # 股票名（重要）
                "other": 1,   # 其他
            }.get(ner_tag, 1)

            # ===== 步骤4：词性标注加权 =====
            postag_coeff = {
                "r": 0.3,     # 副词 → 低权重
                "c": 0.3,     # 连词 → 低权重
                "d": 0.3,     # 数词 → 低权重
                "ns": 3,      # 地名 → 高权重
                "nt": 3,      # 机构 → 高权重
                "n": 2,       # 名词 → 较高权重
            }.get(postag_tag, 1)

            # ===== 步骤5：最终权重 =====
            weight = idf * ner_coeff * postag_coeff
            weights.append((t, weight))
            queries.append((t, weight))

        # ===== 步骤6：归一化 =====
        max_w = max([w for _, w in weights])
        weights = [(t, w/max_w) for t, w in weights]

        return weights
```

**设计思想总结**：

| 因子 | 高权重 | 低权重 | 原因 |
|------|--------|--------|------|
| **IDF** | 稀有词 | 常见词 | 常见词区分度低 |
| **NER** | 公司/地名/学校(×3) | 普通词(×1) | 实体词是查询锚点 |
| **POS** | 名词/机构(×2-3) | 副词/连词(×0.3) | 虚词对检索无帮助 |

---

## 第五章：检索精排 —— Dealer.retrieval() 与 rerank()

**文件位置**：`rag/nlp/search.py` L364-L521

### 5.1 retrieval() 主流程 (L364-L521)

```python
    async def retrieval(self, question, embd_mdl, tenant_ids, kb_ids,
                        page, page_size, similarity_threshold=0.2,
                        vector_similarity_weight=0.3, top=1024,
                        doc_ids=None, aggs=True, rerank_mdl=None, ...):
        ranks = {"total": 0, "chunks": [], "doc_aggs": {}}
        if not question:
            return ranks

        # ===== 步骤1：计算重排序窗口 =====
        RERANK_LIMIT = math.ceil(64 / page_size) * page_size \
                       if page_size > 1 else 1
        RERANK_LIMIT = max(30, RERANK_LIMIT)
        # 精排取前64个，粗排取 topK（默认1024）

        # ===== 步骤2：构造检索请求 =====
        req = {
            "kb_ids": kb_ids,
            "doc_ids": doc_ids,
            "page": math.ceil(page_size * page / RERANK_LIMIT),
            "size": RERANK_LIMIT,       # 精排窗口大小
            "question": question,
            "vector": True,
            "topk": top,                # 粗排召回数
            "similarity": similarity_threshold,
            "available_int": 1,         # 仅检索有效chunk
        }

        # ===== 步骤3：执行混合检索 =====
        sres = await self.search(req,
            [index_name(tid) for tid in tenant_ids],
            kb_ids, embd_mdl, highlight,
            rank_feature=rank_feature)
```

**精排分支选择**：

```python
        # ===== 步骤4：选择重排序策略 =====
        if rerank_mdl and sres.total > 0:
            # 策略A：外部重排序模型（Jina/CoHere/通义...）
            sim, tsim, vsim = self.rerank_by_model(
                rerank_mdl, sres, question,
                1 - vector_similarity_weight,     # 词权重系数
                vector_similarity_weight,          # 向量权重系数
                rank_feature=rank_feature)
        else:
            if settings.DOC_ENGINE_INFINITY:
                # 策略B：Infinity 引擎已做归一化，直接用 _score
                sim = [sres.field[id].get("_score", 0.0) for id in sres.ids]
                sim = [s if s is not None else 0.0 for s in sim]
            else:
                # 策略C：ES 引擎需要本地重排序
                sim, tsim, vsim = self.rerank(
                    sres, question,
                    1 - vector_similarity_weight,
                    vector_similarity_weight,
                    rank_feature=rank_feature)

        # ===== 步骤5：阈值过滤 =====
        sim_np = np.array(sim, dtype=np.float64)
        sorted_idx = np.argsort(sim_np * -1)   # 降序

        # 动态阈值：vector_similarity_weight <= 0 时不设阈值
        post_threshold = 0.0 if vector_similarity_weight <= 0 \
                         else similarity_threshold

        # 指定 doc_ids 时跳过阈值
        if doc_ids:
            post_threshold = 0.0

        valid_idx = [int(i) for i in sorted_idx if sim_np[i] >= post_threshold]

        # ===== 步骤6：分页截取 =====
        max_pages = max(RERANK_LIMIT // max(page_size, 1), 1)
        page_index = (page - 1) % max_pages
        begin = page_index * page_size
        end = begin + page_size
        page_idx = valid_idx[begin:end]

        # ===== 步骤7：构建返回结果 =====
        for i in page_idx:
            id = sres.ids[i]
            chunk = sres.field[id]
            d = {
                "chunk_id": id,
                "content_ltks": chunk["content_ltks"],
                "content_with_weight": chunk["content_with_weight"],
                "doc_id": chunk.get("doc_id", ""),
                "docnm_kwd": chunk.get("docnm_kwd", ""),
                "kb_id": chunk["kb_id"],
                "important_kwd": chunk.get("important_kwd", []),
                "similarity": float(sim_np[i]),
                "vector_similarity": float(vsim[i]),
                "term_similarity": float(tsim[i]),
                "vector": chunk.get(vector_column, zero_vector),
                "positions": chunk.get("position_int", []),
            }
            ranks["chunks"].append(d)

        # ===== 步骤8：按文档聚合 =====
        if aggs:
            for i in valid_idx:
                chunk = sres.field[i]
                dnm = chunk.get("docnm_kwd", "")
                did = chunk.get("doc_id", "")
                if dnm not in ranks["doc_aggs"]:
                    ranks["doc_aggs"][dnm] = {"doc_id": did, "count": 0}
                ranks["doc_aggs"][dnm]["count"] += 1
            ranks["doc_aggs"] = [{"doc_name": k, "doc_id": v["doc_id"],
                                   "count": v["count"]}
                                  for k, v in sorted(
                                      ranks["doc_aggs"].items(),
                                      key=lambda x: x[1]["count"] * -1)]
```

### 5.2 rerank_by_model() —— 外部重排序

**文件位置**：`rag/nlp/search.py` L335-L356

```python
    def rerank_by_model(self, rerank_mdl, sres, query,
                        tkweight=0.3, vtweight=0.7, ...):
        _, keywords = self.qryr.question(query)

        # 构建词权重矩阵
        for i in sres.ids:
            if isinstance(sres.field[i].get("important_kwd", []), str):
                sres.field[i]["important_kwd"] = [sres.field[i]["important_kwd"]]
        ins_tw = []
        for i in sres.ids:
            content_ltks = list(OrderedDict.fromkeys(
                sres.field[i][cfield].split()))
            title_tks = [t for t in sres.field[i].get("title_tks", "").split()
                         if t]
            question_tks = [t for t in sres.field[i].get("question_tks", "")
                            .split() if t]
            important_kwd = sres.field[i].get("important_kwd", [])
            tks = content_ltks + title_tks * 2 + important_kwd * 5 \
                  + question_tks * 6
            ins_tw.append(tks)

        # 计算 token 相似度（基于词权重）
        tksim = self.qryr.token_similarity(keywords, ins_tw)

        # 调用外部重排序模型计算语义相似度
        vtsim, _ = rerank_mdl.similarity(query,
            [remove_redundant_spaces(" ".join(tks)) for tks in ins_tw])

        # 计算标签特征分数
        rank_fea = self._rank_feature_scores(rank_feature, sres)

        # 融合：词权重 × token相似度 + 向量权重 × 语义相似度 + 标签加分
        return tkweight * np.array(tksim) + vtweight * vtsim + rank_fea, \
               tksim, vtsim
```

**加权系数说明**：

| 字段 | 加权倍数 | 意义 |
|------|----------|------|
| `content_ltks` | ×1 | 正文内容，基准 |
| `title_tks` | ×2 | 标题匹配更相关 |
| `important_kwd` | ×5 | 关键词高度相关 |
| `question_tks` | ×6 | 问题文本直接匹配 |

---

## 第六章：引用溯源 —— insert_citations()

**文件位置**：`rag/nlp/search.py` L177-L267

### 6.1 函数签名与前置处理

```python
    def insert_citations(self, answer, chunks, chunk_v,
                         embd_mdl, tkweight=0.1, vtweight=0.9):
        assert len(chunks) == len(chunk_v)
        if not chunks:
            return answer, set([])
```

参数说明：
- `answer`：LLM 生成的完整回答文本
- `chunks`：检索到的 chunk 内容列表
- `chunk_v`：chunks 对应的向量列表
- `embd_mdl`：嵌入模型
- `tkweight=0.1, vtweight=0.9`：词权重10%、向量权重90%（引用匹配更依赖语义）

### 6.2 答案句子切分（保护代码块）

```python
        # 先按代码块分隔符 ``` 切分，保护代码块不被切碎
        pieces = re.split(r"(```)", answer)
        # 过滤过短的句子（<5字符）
        for i, t in enumerate(pieces):
            if len(t) < 5: continue
            idx.append(i)
            pieces_.append(t)
        if not pieces_:
            return answer, set([])
```

### 6.3 核心匹配逻辑：迭代阈值降级

```python
        # 对答案句子和 chunks 分别做 Embedding
        ans_v, _ = embd_mdl.encode(pieces_)

        # 对每个 chunk 分词
        chunks_tks = [rag_tokenizer.tokenize(ck).split() for ck in chunks]

        cites = {}
        thr = 0.63                              # 初始相似度阈值

        # 迭代阈值降级：0.63 → 0.50 → 0.40 → 0.32 → ...
        while thr > 0.3 and len(cites.keys()) == 0 \
              and pieces_ and chunks_tks:
            for i, a in enumerate(pieces_):
                # 计算当前句子与所有 chunks 的混合相似度
                sim, tksim, vtsim = self.qryr.hybrid_similarity(
                    ans_v[i], chunk_v,
                    rag_tokenizer.tokenize(pieces_[i]).split(),
                    chunks_tks,
                    tkweight, vtweight)

                mx = np.max(sim) * 0.99         # 取最大相似度的 99%

                if mx < thr:                     # 低于阈值：跳过
                    continue

                # 高于阈值：记录所有相似度 > mx 的 chunk
                cites[idx[i]] = list(
                    set([str(ii) for ii in range(len(chunk_v))
                         if sim[ii] > mx]))[:4]   # 最多4个引用
            thr *= 0.8                           # 阈值降低 20%

        # 插入引用标记 [ID:n]
        res = ""
        seted = set([])
        for i, p in enumerate(pieces):
            res += p
            if i not in cites: continue
            for c in cites[i]:
                if c in seted: continue          # 同一 chunk 只引用一次
                res += f" [ID:{c}]"
                seted.add(c)

        return res, seted
```

**迭代阈值降级的设计思想**：
- 目的：在精确匹配和覆盖之间取得平衡
- 第一轮（thr=0.63）：只引用高度相关的 chunk
- 最后一轮（thr≈0.3）：放宽标准，确保至少有一个引用
- 每轮阈值 × 0.8，逐步放宽

---

## 第七章：Prompt 构建与 LLM 调用

### 7.1 Prompt 模板系统

RAGFlow 使用 **Python 字符串格式化**（不是 Jinja2）作为 Prompt 模板引擎。

```python
# Prompt 模板示例
SYSTEM_PROMPT = """
You are an intelligent assistant.
**Essential Rules:**
- When information is available: Summarize the content.
- When information is unavailable: You MUST respond
  "The answer you are looking for is not found in the knowledge base!"

## Knowledge Base:
{knowledge}

## Current Date:
{sys.date}
"""

# 模板填充
kwargs["knowledge"] = "\n------\n" + "\n\n------\n\n".join(knowledges)
msg[0]["content"] = prompt_config["system"].format(**kwargs)
```

### 7.2 Token 限制管理

```python
# message_fit_in(): 从最早的消息开始删除，直到总 token 数 < 95% max_tokens
used_token_count, msg = message_fit_in(msg, int(max_tokens * 0.95))

# kb_prompt(): 知识块内容按 token 限制截断
knowledges = kb_prompt(kbinfos, max_tokens)
```

### 7.3 流式输出（SSE）

```python
# 流式生成
stream_iter = chat_mdl.async_chat_streamly_delta(
    prompt + prompt4citation, msg[1:], gen_conf)

async for kind, value, state in _stream_with_think_delta(stream_iter):
    yield {
        "answer": value,              # 答案文本块
        "reference": {},              # 引用信息
        "audio_binary": tts(tts_mdl, value),  # TTS 音频
        "final": False                # 是否最后一块
    }
```

---

# 第二篇：Agent 编排引擎实现详解

---

## 第八章：DSL 定义与 Graph 加载

**文件位置**：`agent/canvas.py` L42-L281

### 8.1 DSL 数据结构

Agent 工作流完全由 JSON DSL 描述，核心结构：

```json
{
  "components": {
    "begin": {
      "obj": {"component_name": "Begin", "params": {}},
      "downstream": ["retrieval_0"],
      "upstream": []
    },
    "agent_0": {
      "obj": {
        "component_name": "Agent",
        "params": {
          "llm_id": "qwen-turbo-latest",
          "tools": [{"component_name": "Retrieval", "params": {...}}],
          "max_rounds": 5
        }
      },
      "downstream": ["message_0"],
      "upstream": ["retrieval_0"]
    }
  },
  "path": ["begin"],
  "history": [],
  "retrieval": {"chunks": [], "doc_aggs": []},
  "globals": {
    "sys.query": "",
    "sys.user_id": "tenant_uuid",
    "sys.conversation_turns": 0,
    "sys.files": [],
    "sys.history": []
  }
}
```

**关键字段解析**：

| 字段 | 类型 | 含义 |
|------|------|------|
| `components` | dict | 组件字典，key=组件唯一ID |
| `components[].obj` | dict | 序列化后的组件实例 |
| `components[].downstream` | list[str] | 下游组件ID列表 |
| `components[].upstream` | list[str] | 上游组件ID列表 |
| `path` | list[str] | 当前执行路径（表示已完成和待执行的组件队列） |
| `history` | list[(role, content)] | 对话历史 |
| `globals` | dict | 全局变量（以 `sys.` 前缀） |

### 8.2 Graph.__init__() —— 初始化

```python
class Graph:
    def __init__(self, dsl: str, tenant_id=None, task_id=None,
                 custom_header=None):
        self.path = []                       # 执行路径
        self.components = {}                 # 组件字典
        self.dsl = json.loads(dsl)           # 解析 JSON
        self._tenant_id = tenant_id
        self.task_id = task_id or get_uuid()
        self.custom_header = custom_header
        self._thread_pool = ThreadPoolExecutor(max_workers=5)
        self.load()                          # 立即加载
```

### 8.3 Graph.load() —— DSL 反序列化核心

```python
    def load(self):
        self.components = self.dsl["components"]
        cpn_nms = set()

        for k, cpn in self.components.items():
            cpn_nms.add(cpn["obj"]["component_name"])

            # === 步骤1：工厂模式创建参数对象 ===
            # component_class("Agent" + "Param") → AgentParam 类
            param = component_class(
                cpn["obj"]["component_name"] + "Param")()

            # === 步骤2：注入自定义 Header（用于 API 认证） ===
            cpn["obj"]["params"]["custom_header"] = self.custom_header

            # === 步骤3：用 DSL 中的参数更新 Param 对象 ===
            param.update(cpn["obj"]["params"])

            # === 步骤4：参数校验 ===
            param.check()

            # === 步骤5：工厂模式创建组件实例 ===
            # component_class("Agent")(self, k, param) → Agent 实例
            cpn["obj"] = component_class(
                cpn["obj"]["component_name"])(self, k, param)

        self.path = self.dsl["path"]
```

**工厂模式 `component_class()`**：

```python
# agent/component/__init__.py
def component_class(class_name):
    for module_name in ["agent.component", "agent.tools", "rag.flow"]:
        try:
            return getattr(importlib.import_module(module_name),
                          class_name)
        except:
            pass
```

自动扫描三个模块下的所有 `.py` 文件，通过 `inspect.getmembers` 注册到全局命名空间。

### 8.4 Canvas.load() —— 扩展加载

```python
class Canvas(Graph):
    def load(self):
        super().load()
        self.history = self.dsl["history"]
        if "globals" in self.dsl:
            self.globals = self.dsl["globals"]
        if "variables" in self.dsl:
            self.variables = self.dsl["variables"]
        self.retrieval = self.dsl["retrieval"]    # 检索引用列表
        self.memory = self.dsl.get("memory", [])   # Agent 记忆
```

---

## 第九章：Canvas 执行引擎 —— run()

**文件位置**：`agent/canvas.py` L375-L667

这是 Agent 执行的核心，一个**异步生成器**，yield 事件字典驱动前端实时更新。

### 9.1 阶段1：初始化 (L376-L433)

```python
    async def run(self, **kwargs):
        # === 更新全局变量 ===
        self.globals["sys.date"] = datetime.datetime.now(...).isoformat()
        st = time.perf_counter()                    # 记录开始时间
        self._loop = asyncio.get_running_loop()
        self.message_id = get_uuid()               # 生成消息ID
        created_at = int(time.time())

        # === 添加用户输入到历史 ===
        self.add_user_input(kwargs.get("query"))

        # === 重置所有组件 outputs ===
        for k, cpn in self.components.items():
            self.components[k]["obj"].reset(True)

        # === 处理文件上传 ===
        if kwargs.get("files"):
            self.globals["sys.files"] = await self.get_files_async(
                kwargs["files"], layout_recognize)

        # === 递增会话轮次 ===
        self.globals["sys.conversation_turns"] += 1

        # === 路径初始化：如果 path 尾部不是 userfillup，追加 begin ===
        if not self.path or self.path[-1].lower().find("userfillup") < 0:
            self.path.append("begin")
            self.retrieval.append({"chunks": [], "doc_aggs": []})

        # === 取消检测 ===
        if self.is_canceled():
            raise TaskCanceledException(f"Task {self.task_id} canceled")

        # === yield 工作流开始事件 ===
        yield decorate("workflow_started", {"inputs": kwargs.get("inputs")})
```

### 9.2 阶段2：批量并行执行 _run_batch() (L435-L482)

```python
        async def _run_batch(f, t):
            if self.is_canceled():
                raise TaskCanceledException(...)

            loop = asyncio.get_running_loop()
            tasks = []
            max_concurrency = 5    # 最大并发数
            sem = asyncio.Semaphore(max_concurrency)

            async def _invoke_one(cpn_obj, sync_fn, call_kwargs, use_async):
                async with sem:    # 信号量控制并发
                    if use_async:
                        await cpn_obj.invoke_async(**(call_kwargs or {}))
                    else:
                        # 同步方法放到线程池执行
                        await loop.run_in_executor(
                            self._thread_pool,
                            partial(sync_fn, **(call_kwargs or {})))

            i = f
            while i < t:
                cpn = self.get_component_obj(self.path[i])

                # === Begin/UserFillUp 特殊处理 ===
                if cpn.component_name.lower() in ["begin", "userfillup"]:
                    call_kwargs = {"inputs": kwargs.get("inputs", {})}
                    task_fn = cpn.invoke
                    i += 1
                else:
                    # === 依赖检查：变量的上游组件是否已完成？ ===
                    for _, ele in cpn.get_input_elements().items():
                        if (isinstance(ele, dict)
                            and ele.get("_cpn_id")
                            and ele.get("_cpn_id") not in self.path[:i]):
                            # 依赖未满足，从 path 中移除
                            self.path.pop(i)
                            t -= 1
                            break
                    else:
                        call_kwargs = cpn.get_input()
                        task_fn = cpn.invoke
                        i += 1

                if task_fn is None:
                    continue

                # 检测是否有 _invoke_async 方法（异步组件）
                fn_invoke_async = getattr(cpn, "_invoke_async", None)
                use_async = (fn_invoke_async and
                             asyncio.iscoroutinefunction(fn_invoke_async))

                tasks.append(asyncio.create_task(
                    _invoke_one(cpn, task_fn, call_kwargs, use_async)))

            if tasks:
                await asyncio.gather(*tasks)   # 并行等待所有任务完成
```

**依赖解析机制详解**：
1. `get_input_elements()` 返回组件的输入变量映射
2. 每个变量引用格式 `{begin@sys.query}` → 解析后得到 `_cpn_id = "begin"`
3. 检查 `_cpn_id` 是否已经在 `self.path[:i]` 中（已完成执行）
4. 不在 → 依赖未满足，从执行队列移除
5. 在 → 依赖已满足，可以执行

### 9.3 阶段3：主循环 —— 执行与后处理 (L500-L648)

```python
        idx = len(self.path) - 1
        while idx < len(self.path):
            to = len(self.path)

            # === 发送所有即将执行组件的 node_started 事件 ===
            for i in range(idx, to):
                yield decorate("node_started", {
                    "component_id": self.path[i],
                    "component_name": self.get_component_name(self.path[i]),
                    ...
                })

            # === 批量执行 ===
            await _run_batch(idx, to)

            # === 逐个后处理 ===
            for i in range(idx, to):
                cpn = self.get_component(self.path[i])
                cpn_obj = self.get_component_obj(self.path[i])

                # ---- Message 组件特殊处理 ----
                if cpn_obj.component_name.lower() == "message":
                    # 初始化 TTS
                    if cpn_obj.get_param("auto_play"):
                        tts_mdl = LLMBundle(tenant_id, tts_config)

                    # 流式内容处理
                    if isinstance(cpn_obj.output("content"), partial):
                        # partial(stream_output_with_tools_async, ...)
                        stream = cpn_obj.output("content")()
                        async for m in stream:
                            # 处理 <think>/</think> 标签
                            if m == "<think>":
                                yield decorate("message",
                                    {"content": "", "start_to_think": True})
                            elif m == "</think>":
                                yield decorate("message",
                                    {"content": "", "end_to_think": True})
                            else:
                                # 每16字符做一次 TTS
                                buff_m += m
                                if len(buff_m) > 16:
                                    yield decorate("message", {
                                        "content": m,
                                        "audio_binary": self.tts(tts_mdl, buff_m)
                                    })
                                    buff_m = ""
                                else:
                                    yield decorate("message", {"content": m})

                    message_end = self._build_message_end(cpn_obj)
                    yield decorate("message_end", message_end)

                # ---- 错误处理 ----
                if cpn_obj.error():
                    ex = cpn_obj.exception_handler()
                    if ex and ex["goto"]:
                        self.path.extend(ex["goto"])    # 跳转到错误处理组件
                    elif ex and ex["default_value"]:
                        yield decorate("message",
                            {"content": ex["default_value"]})
                    else:
                        self.error = cpn_obj.error()

                # ---- 发送 node_finished 事件 ----
                if cpn_obj.component_name.lower() not in ("iteration","loop"):
                    if isinstance(cpn_obj.output("content"), partial):
                        # 流式内容，延迟发送（等流结束）
                        partials.append(self.path[i])
                    else:
                        yield _node_finished(cpn_obj)

                # ---- 路径推进（核心流转逻辑）----
                if cpn_obj.component_name.lower() in ("iterationitem","loopitem") \
                   and cpn_obj.end():
                    # 迭代/循环项结束 → 回退到父组件的 downstream
                    iter = cpn_obj.get_parent()
                    yield _node_finished(iter)
                    _extend_path(
                        self.get_component(cpn["parent_id"])["downstream"])

                elif cpn_obj.component_name.lower() in ["categorize", "switch"]:
                    # 条件分支 → 按 _next 输出跳转
                    _extend_path(cpn_obj.output("_next"))

                elif cpn_obj.component_name.lower() in ("iteration", "loop"):
                    # 进入迭代/循环体的第一个子组件
                    _append_path(cpn_obj.get_start())

                elif cpn_obj.component_name.lower() == "exitloop":
                    # 退出循环
                    _extend_path(
                        self.get_component(cpn["parent_id"])["downstream"])

                elif not cpn["downstream"] and cpn_obj.get_parent():
                    # 无 downstream但有父组件（子组件执行完毕）
                    _append_path(cpn_obj.get_parent().get_start())

                else:
                    # 普通流转：推进到 downstream
                    _extend_path(cpn["downstream"])

            if self.error:
                break
            idx = to

            # === UserFillUp 处理：暂停等待用户输入 ===
            if any(self.get_component_obj(c).component_name.lower()
                   == "userfillup" for c in self.path[idx:]):
                # 收集需要用户输入的字段
                another_inputs = {}
                for c in path:
                    o = self.get_component_obj(c)
                    if o.component_name.lower() == "userfillup":
                        o.invoke()
                        another_inputs.update(o.get_input_elements())
                yield decorate("user_inputs",
                    {"inputs": another_inputs, "tips": tips})
                return                            # 暂停执行
```

**路径推进规则速查表**：

| 组件类型 | 推进逻辑 | 代码行 |
|----------|----------|--------|
| Begin / UserFillUp / 普通组件 | `_extend_path(downstream)` | L627 |
| Switch / Categorize | `_extend_path(output("_next"))` | L619 |
| Loop / Iteration | `_append_path(get_start())` | L621 |
| LoopItem / IterationItem (未完成) | `_append_path(get_start())` 再执行一次 | L625 |
| LoopItem / IterationItem (已完成) | `_extend_path(parent.downstream)` | L617 |
| ExitLoop | `_extend_path(parent.downstream)` | L623 |

### 9.4 阶段4：完成 (L650-L667)

```python
        self.path = self.path[:idx]
        if not self.error:
            yield decorate("workflow_finished", {
                "inputs": kwargs.get("inputs"),
                "outputs": self.get_component_obj(self.path[-1]).output(),
                "elapsed_time": time.perf_counter() - st,
            })
            # 记录历史和全局变量
            self.history.append(("assistant", output))
            self.globals["sys.history"].append(f"assistant: {output}")
```

---

## 第十章：Agent 智能体 —— invoke_async()

**文件位置**：`agent/component/agent_with_tools.py` L188-L259

### 10.1 Agent 初始化与工具绑定

```python
class Agent(LLM, ToolBase):
    component_name = "Agent"

    def __init__(self, canvas, id, param: LLMParam):
        LLM.__init__(self, canvas, id, param)
        self.tools = {}

        # === 步骤1：加载配置的工具 ===
        for idx, cpn in enumerate(self._param.tools):
            cpn = self._load_tool_obj(cpn)
            # 加索引防重名：retrieval → retrieval_0
            indexed_name = f"{original_name}_{idx}"
            self.tools[indexed_name] = cpn

        # === 步骤2：加载 MCP 工具 ===
        for mcp in self._param.mcp:
            _, mcp_server = MCPServerService.get_by_id(mcp["mcp_id"])
            tool_call_session = MCPToolCallSession(
                mcp_server, mcp_server.variables, custom_header)
            for tnm, meta in mcp["tools"].items():
                self.tool_meta.append(
                    mcp_tool_metadata_to_openai_tool(meta))
                self.tools[tnm] = tool_call_session

        # === 步骤3：创建 LLMBundle 并绑定工具 ===
        self.chat_mdl = LLMBundle(tenant_id, chat_model_config,
                                   max_retries=..., max_rounds=max_rounds)
        self.callback = partial(self._canvas.tool_use_callback, id)
        self.toolcall_session = LLMToolPluginCallSession(
            self.tools, self.callback)
        if self.tool_meta:
            self.chat_mdl.bind_tools(self.toolcall_session, self.tool_meta)
```

### 10.2 invoke_async() —— 主执行逻辑

```python
    async def _invoke_async(self, **kwargs):
        # === 分支1：无工具 → 纯对话模式 ===
        if not self.tools:
            return await LLM._invoke_async(self, **kwargs)

        prompt, msg, user_defined_prompt = self._prepare_prompt_variables()
        output_schema = self._get_output_schema()

        # 检查下游是否有 Message 组件
        component = self._canvas.get_component(self._id)
        downstreams = component["downstream"] if component else []
        ex = self.exception_handler()
        has_message_downstream = any(
            self._canvas.get_component_obj(cid).component_name.lower()
            == "message" for cid in downstreams)

        # === 分支2：有 Message 下游 + 无异常跳转 → 流式输出 ===
        if has_message_downstream and not (ex and ex["goto"]) \
           and not output_schema:
            # partial 延迟执行：等 Canvas 调用时才真正执行
            self.set_output("content", partial(
                self.stream_output_with_tools_async,
                prompt, deepcopy(msg), user_defined_prompt))
            return

        # === 分支3：无 Message 下游 → 非流式生成 ===
        msg = self._fit_messages(prompt, msg)
        ans = await self._generate_async(msg)     # 调用 LLM（含 tool call）

        # === 错误处理 ===
        if ans.find("**ERROR**") >= 0:
            if self.get_exception_default_value():
                self.set_output("content", self.get_exception_default_value())
            else:
                self.set_output("_ERROR", ans)
            return

        # === Structured Output 处理 ===
        if output_schema:
            for _ in range(self._param.max_retries + 1):
                try:
                    obj = json_repair.loads(self._clean_formatted_answer(ans))
                    self.set_output("structured", obj)
                    return obj
                except Exception:
                    ans = await self._force_format_to_schema_async(
                        ans, schema_prompt)
            self.set_output("_ERROR", "Cannot parse as JSON")
            return

        # 收集工具附件和artifact
        attachment_content = self._collect_tool_attachment_content(
            existing_text=ans)
        if attachment_content:
            ans += "\n\n" + attachment_content
        artifact_md = self._collect_tool_artifact_markdown(
            existing_text=ans)
        if artifact_md:
            ans += "\n\n" + artifact_md
        self.set_output("content", ans)
```

### 10.3 stream_output_with_tools_async() —— 流式输出

```python
    async def stream_output_with_tools_async(self, prompt, msg,
                                              user_defined_prompt={}):
        # === 步骤1：多轮对话优化 ===
        if len(msg) > 3:
            user_request = await full_question(messages=msg,
                                                chat_mdl=self.chat_mdl)
            self.callback("Multi-turn conversation optimization",
                          {}, user_request, elapsed_time=timer()-st)
            msg = [*msg[:-1], {"role": "user", "content": user_request}]

        # === 步骤2：Token 裁剪 ===
        msg = self._fit_messages(prompt, msg)

        # === 步骤3：引用提示词（如果需要引用） ===
        need2cite = (self._param.cite
                     and self._canvas.get_reference()["chunks"]
                     and self._id.find("-->") < 0)
        if need2cite and len(msg) < 7:
            self._append_system_prompt(msg, citation_prompt())

        # === 步骤4：流式生成 ===
        answer = ""
        async for delta in self._generate_streamly(msg):
            if self.check_if_canceled("Agent streaming"):
                return
            yield delta
            answer += delta

        # === 步骤5：引用插入后处理 ===
        if need2cite:
            cited_answer = ""
            async for delta in self._gen_citations_async(answer):
                yield delta
                cited_answer += delta
            self.set_output("content", cited_answer)

        # === 步骤6：收集工具附件 ===
        attachment_content = self._collect_tool_attachment_content(
            existing_text=cited_answer)
        if attachment_content:
            yield "\n\n" + attachment_content
```

---

## 第十一章：Tool Call 全链路

**文件位置**：`agent/tools/base.py` L50-L77, `agent/tools/retrieval.py` L88-L324

### 11.1 LLMToolPluginCallSession —— 工具调用会话

```python
class LLMToolPluginCallSession(ToolCallSession):
    async def tool_call_async(self, name, arguments):
        tool_obj = self.tools_map[name]

        # === 类型1：MCP 工具 → 线程池同步执行 ===
        if isinstance(tool_obj, MCPToolCallSession):
            resp = await thread_pool_exec(
                tool_obj.tool_call, name, arguments, 60)

        # === 类型2：异步工具 → await 执行 ===
        elif hasattr(tool_obj, "invoke_async") and \
             asyncio.iscoroutinefunction(tool_obj.invoke_async):
            resp = await tool_obj.invoke_async(**arguments)

        # === 类型3：同步工具 → 线程池执行 ===
        else:
            resp = await thread_pool_exec(
                tool_obj.invoke, **arguments)

        # === 回调记录日志到 Redis ===
        self.callback(name, arguments, resp,
                      elapsed_time=timer()-st)
        return resp
```

### 11.2 Retrieval 工具 —— 知识库检索

```python
class Retrieval(ToolBase):
    async def _invoke_async(self, **kwargs):
        if kwargs.get("query"):
            if retrieval_from == "dataset":
                return await self._retrieve_kb(kwargs["query"])
            elif retrieval_from == "memory":
                return await self._retrieve_memory(kwargs["query"])

    async def _retrieve_kb(self, query_text):
        # 步骤1：解析 KB ID
        # 步骤2：获取 Embedding Model
        # 步骤3：获取 Rerank Model（可选）
        # 步骤4：变量模板替换 query
        # 步骤5：元数据过滤（可选）
        # 步骤6：跨语言查询扩展（可选）
        # 步骤7：核心检索
        kbinfos = await settings.retriever.retrieval(
            query, embd_mdl, tenant_ids, kb_ids, ...)
        # 步骤8：TOC 增强 + 子块展开（可选）
        # 步骤9：Knowledge Graph 增强（可选）
        # 步骤10：格式化输出
        #    - canvas.add_reference() 存储引用
        #    - kb_prompt() 格式化内容
        #    - set_output("formalized_content", ...)
```

### 11.3 完整 Tool Call 数据流

```
用户提问 "帮我查一下上季度的销售数据"
    ↓
Agent._invoke_async()
    ↓
chat_mdl (LLM推理) → 返回 tool_choice: {
    "function": {"name": "retrieval_0", "arguments": {"query": "上季度销售数据"}}
}
    ↓
LLMToolPluginCallSession.tool_call_async("retrieval_0", {"query": "上季度销售数据"})
    ↓
Retrieval.invoke_async(query="上季度销售数据")
    ↓
Retrieval._retrieve_kb("上季度销售数据")
    ↓
settings.retriever.retrieval(...)  → 返回 chunks + doc_aggs
    ↓
canvas.add_reference(chunks, doc_infos)  → 存储引用
    ↓
set_output("formalized_content", kb_prompt(chunks))
    ↓
返回工具结果给 LLMToolPluginCallSession
    ↓
工具结果注入回 LLM 上下文
    ↓
LLM 下一轮推理 → 生成最终回答
    ↓
Agent.stream_output_with_tools_async()
    ↓
Canvas → Message 组件 → SSE 流式推送到前端
```

---

## 第十二章：流式输出与事件系统

### 12.1 事件类型定义

```python
class EventType(Enum):
    workflow_started  = "workflow_started"    # 工作流开始
    node_started      = "node_started"        # 节点开始执行
    node_finished     = "node_finished"       # 节点执行完成
    message           = "message"             # 流式消息
    message_end       = "message_end"         # 消息结束（含引用和附件）
    user_inputs       = "user_inputs"         # 等待用户输入
    workflow_finished = "workflow_finished"   # 工作流完成
```

### 12.2 事件数据结构

```python
# decorate() 函数统一包装
{
    "event": "message",
    "message_id": "uuid",
    "created_at": 1712345678,
    "task_id": "task-uuid",
    "data": {
        "content": "这是流式输出的文字块",
        "audio_binary": None,
        "start_to_think": False,
        "end_to_think": False,
    }
}
```

### 12.3 SSE 推送格式

每个事件通过 Web 的 SSE 通道推送：

```
event: message
data: {"event":"message","data":{"content":"你好","audio_binary":null,...}}

event: message
data: {"event":"message","data":{"content":"世界","audio_binary":null,...}}

event: message_end
data: {"event":"message_end","data":{"status":"ok","reference":{...}}}

event: workflow_finished
data: {"event":"workflow_finished","data":{"outputs":{...},"elapsed_time":3.5}}
```

---

## 总结：核心设计亮点汇总

### ✨ RAG 模块亮点

| 亮点 | 实现位置 | 设计思想 |
|------|----------|----------|
| **混合检索** | `search.py L122-L128` | 全文+向量加权融合，0.05:0.95权重偏向语义 |
| **二次降级** | `search.py L136-L146` | 空结果时降低 min_match和similarity 重试 |
| **多因子重排序** | `search.py L270-L333` | 词权重30% + 向量70% + 标签加分 + PageRank |
| **Adaptive IDF** | `term_weight.py` | NER×3 实体加权 + POS 词性加权 + IDF混合 |
| **双重引用保障** | `L653-L654 + L177-L267` | Prompt引导 + 后处理强制插入 |
| **迭代阈值降级引用** | `L234-L249` | thr=0.63 → 逐步×0.8 确保引用覆盖 |
| **16种重排模型** | `rerank_model.py` | 统一接口抽象，支持热插拔 |
| **细粒度中文分词** | `query.py L94-L168` | 1~5-grams 增强中文查询召回 |

### ✨ Agent 模块亮点

| 亮点 | 实现位置 | 设计思想 |
|------|----------|----------|
| **JSON DSL 编排** | `canvas.py L43-L80` | 可视化工作流 → 可序列化 JSON |
| **依赖解析** | `canvas.py L464-L472` | 变量引用 `{cpn@var}` → 自动检测上游完成状态 |
| **5路并行执行** | `canvas.py L443-L482` | asyncio.Semaphore(5) + Gather |
| **同步工具适配** | `L451` | loop.run_in_executor 线程池化 |
| **MCP 协议支持** | `agent_with_tools.py L100-L106` | 标准化第三方工具接口 |
| **Structured Output** | `L236-L250` | JSON Schema + json_repair + 重试 |
| **流式 + TTS** | `canvas.py L516-L568` | 每16字符 TTS + `<think>` 标签处理 |
| **路径推进状态机** | `L599-L627` | Loop/Iteration/Switch/Categorize 统一状态流转 |
| **异常跳转** | `L578-L587` | exception_handler → goto/default_value 两级容错 |
| **UserFillUp 暂停** | `L634-L648` | 工作流暂停等待用户交互输入 |
| **工具去重** | `L82` | `{tool_name}_{idx}` 索引前缀防冲突 |
| **Categorize 意图路由** | `categorize.py L109-L165` | LLM分类 → 统计最高频类别 → 路由 |

---

### 📋 快速定位索引

| 要查找的功能 | 直接跳转 |
|-------------|----------|
| RAG 完整流程入口 | `api/db/services/dialog_service.py#async_chat` L455 |
| 混合检索 | `rag/nlp/search.py#search` L74 |
| 全文查询构造 | `rag/nlp/query.py#question` L41 |
| 词权重计算 | `rag/nlp/term_weight.py#weights` |
| 检索精排 | `rag/nlp/search.py#retrieval` L364 |
| 重排序（本地） | `rag/nlp/search.py#rerank` L270 |
| 重排序（外部模型） | `rag/nlp/search.py#rerank_by_model` L335 |
| 引用插入 | `rag/nlp/search.py#insert_citations` L177 |
| Agent Canvas 执行 | `agent/canvas.py#Canvas.run` L375 |
| Tool Call 会话 | `agent/tools/base.py#LLMToolPluginCallSession` L50 |
| Agent 主执行 | `agent/component/agent_with_tools.py#_invoke_async` L188 |
| 组件基类 invoke | `agent/component/base.py#ComponentBase.invoke` L407 |
| 流式输出 with tools | `agent/component/agent_with_tools.py#stream_output_with_tools_async` L261 |
| Message 组件流式 | `agent/component/message.py#_invoke` L182 |