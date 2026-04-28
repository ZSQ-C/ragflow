# RAGFlow 三层 RAG 全流程代码实现详解

> **目标**：精通 RAG 全流程 —— 从文档入库到 LLM 生成回答的每一行代码都了然于心
> **文件位置**：`e:\AI\GitHub\RagFlow\`

---

## 📊 总览：三层架构与代码入口

```
第一层（离线索引）      第二层（在线检索）          第三层（生成回答）
文档上传                  用户提问                    构建 Prompt
  │                        │                          │
格式检测                  查询优化                    Token 裁剪
  │ (rag/app/naive.py)     │ (dialog_service.py)       │ (generator.py)
解析器选择                混合检索                    LLM 流式生成
  │ (PARSERS 字典)         │ (search.py#search)        │ (llm_service.py)
OCR+布局+表格             降级重试                    引用插入后处理
  │ (deepdoc/)             │ (search.py#L136)          │ (search.py#insert_citations)
文本分块                  重排序                      SSE 返回
  │ (naive_merge)          │ (search.py#rerank)        │ (conversation_app.py)
Embedding 向量化          阈值过滤
  │ (embedding_model.py)   │ (search.py#L440)
存储到 ES/Infinity        结果聚合
  (doc_store/)             │ (search.py#L497)
```

---

# 第一层：离线索引（文档入库）

---

## 步骤1：文档上传

**做什么**：用户通过 Web UI 或 API 上传文件到 MinIO

**代码位置**：`api/apps/document_app.py` → `api/db/services/file_service.py`

**关键代码**：
```python
# 文件上传 → 写入 MinIO 存储
# FileService.upload_file() → MinIO SDK put_object()
# 数据库记录到 file 表（id, tenant_id, name, size, type, parent_id, location）
```

**数据流**：
```
前端上传文件 → multipart/form-data → Quart 接收 → MinIO 存储 → file 表记录
```

---

## 步骤2：格式检测 + 解析器选择

**做什么**：根据文件扩展名选择对应的文档解析器

**代码位置**：`rag/app/naive.py` L254-L261（解析器字典）、L829-L995（扩展名路由）

**关键代码**：
```python
# L254-L261: 5 种 PDF 解析引擎
PARSERS = {
    "deepdoc": by_deepdoc,       # 自研 ONNX OCR（默认，免费）
    "mineru": by_mineru,         # MinerU 商业 API（精度最高）
    "docling": by_docling,       # IBM 开源（支持数学公式）
    "tcadp parser": by_tcadp,    # 腾讯云（云原生免运维）
    "paddleocr": by_paddleocr,   # 百度 PaddleOCR（中文最优）
    "plaintext": by_plaintext,   # 纯文本（速度最快）
}

# L829-L995: 按扩展名路由
if re.search(r"\.pdf$", filename):
    parser = PARSERS.get(name, by_plaintext)
    sections, tables = parser(filename, binary, ...)
elif re.search(r"\.docx$", filename):
    # DocxParser
elif re.search(r"\.xlsx?$|\.csv$", filename):
    # ExcelParser
```

**数据流**：
```
filename.ext → 正则匹配 → 选择 PARSERS[ext] → 调用对应解析器
```

---

## 步骤3：OCR 检测 + 布局识别 + 表格提取

**做什么**：将 PDF 页面转为图片 → OCR 文字检测+识别 → 布局分类 → 表格结构识别

**代码位置**：
- `deepdoc/parser/pdf_parser.py` L1-L2057（RAGFlowPdfParser 主流程）
- `deepdoc/vision/ocr.py` L425-L542（OCR 协调器）
- `deepdoc/vision/layout_recognizer.py`（布局识别器，11 种标签）
- `deepdoc/vision/table_structure_recognizer.py`（表格结构识别器）

**关键代码**：
```python
# 步骤1：pdfplumber 逐页渲染为图片 → ONNX OCR 模型推理
images = pdfplumber.render_pages(pdf)          # 200 DPI
ocr_result = self.ocr.ocr(images)              # OCR: detect → recognize
# 返回: [{"text": "Hello", "x0": 100, "y0": 200, "x1": 300, "y1": 250}, ...]

# 步骤2：布局识别 → 标记每个文框类型
# 11 种标签：Text / Title / Figure / Figure caption / Table / Table caption
#           Header / Footer / Reference / Equation / _background_
layout_result = self.layout_recognizer(boxes)

# 步骤3：表格结构识别 → 生成 HTML <table>（含 colspan/rowspan）
# 支持旋转检测（0°/90°/180°/270°，用 OCR 置信度评估最佳角度）
html_table = self.table_recognizer.construct_table(figure_area)
```

**乱码检测与 OCR 降级**（`pdf_parser.py` L202-L300）：
```python
# 三重检测
_CID_PATTERN = re.compile(r"\(cid\s*:\s*\d+\s*\)")   # CID 模式直接判定乱码

def _is_garbled_char(ch):
    cp = ord(ch)
    if 0xE000 <= cp <= 0xF8FF: return True    # PUA 字符
    if cp == 0xFFFD:             return True    # 替换字符
    if unicodedata.category(ch) in ("Cn","Cs"): return True

def _is_garbled_by_font_encoding(page_chars):
    # 子集字体占比 > 50% + CJK 字符 < 5% → 字体编码错乱
    ...

# 自适应阈值触发 OCR 降级
lower = max(15, total_chars * 0.2)    # 至少15个乱码或20%
upper = min(35, total_chars * 0.3)    # 最多35个或30%
if garbled > upper:
    ocr_result = self.ocr.ocr(images)  # 降级到 ONNX OCR
```

---

## 步骤4：文本分块

**做什么**：将解析后的连续文本按分隔符切分，再用 Token 限制合并成块

**代码位置**：
- `rag/app/naive.py` L729-L1078（`chunk()` 总入口）
- `rag/nlp/__init__.py` L1070-L1126（`naive_merge()` 通用分块）
- `rag/nlp/__init__.py` L1463-L1485（`naive_merge_docx()` DOCX 专用分块）
- `rag/nlp/__init__.py` L302-L327（`tokenize_chunks()` chunk 的 token 化）

**关键代码 — 通用分块（`naive_merge`）**：
```python
def naive_merge(sections, chunk_token_num=512, delimiter="\n!?。；！？", overlapped_percent=0):
    # 步骤1：按分隔符切分
    regex_delimiter = f"[{delimiter}]"
    pieces = []
    for text in sections:
        pieces.extend(re.split(regex_delimiter, text))

    # 步骤2：按 token 限制合并
    chunks = []
    current_chunk = ""
    for piece in pieces:
        if num_tokens_from_string(current_chunk + piece) <= chunk_token_num:
            current_chunk += piece
        else:
            chunks.append(current_chunk)
            current_chunk = piece       # 开始新块

    return chunks
```

**关键代码 — DOCX 专用分块**：
```python
def naive_merge_docx(sections, chunk_token_num=512, ...):
    # 先构建 cks 列表，区分 text / image / table 三种类型
    cks = _build_cks(sections)          # 解析 (text, image, table) 三元组

    # 图片/表格附加上下文
    _add_context(cks, table_context_size, image_context_size)

    # 合并连续的 text chunk，image/table 独立保留不参与合并
    chunks = _merge_cks(cks, chunk_token_num)
```

**Token 化（写入 ES 前的最后一步）**：
```python
def tokenize_chunks(chunks, ...):
    for d in chunks:
        d["content_with_weight"] = d["text"]
        d["content_ltks"] = rag_tokenizer.tokenize(clean_text).split()     # 粗粒度 token
        d["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(clean_text) # 细粒度 token
```

---

## 步骤5：Embedding 向量化

**做什么**：将每个 chunk 的文本转为稠密向量

**代码位置**：
- `rag/llm/embedding_model.py`（各种 Embedding 模型封装）
- `rag/nlp/search.py` L52-L60（`Dealer.get_vector()` 调用入口）

**关键代码**：
```python
# L52-L60: Embedding 调用（线程池避免阻塞事件循环）
async def get_vector(self, txt, emb_mdl, topk=10, similarity=0.1):
    qv, _ = await thread_pool_exec(emb_mdl.encode_queries, txt)
    embedding_data = [get_float(v) for v in qv]
    vector_column_name = f"q_{len(embedding_data)}_vec"  # 动态列名：q_768_vec / q_1536_vec
    return MatchDenseExpr(vector_column_name, embedding_data, 'float', 'cosine', topk, {"similarity": similarity})
```

**注意 `q_{dim}_vec` 动态列名**：不同 Embedding 模型产出不同维度（BERT 768维、BGE 1024维、OpenAI 1536维），动态列名让同一知识库能混存不同维度的向量。

---

## 步骤6：存储到 ES / Infinity

**做什么**：将 chunk 的文本、向量和元数据写入向量数据库

**代码位置**：
- `common/doc_store/doc_store_base.py`（抽象接口 `DocStoreConnection`）
- `common/doc_store/es_conn_base.py`（ES 实现）
- `common/doc_store/infinity_conn_base.py`（Infinity 实现）
- `common/doc_store/ob_conn_base.py`（OceanBase 实现）

**关键抽象接口**：
```python
class DocStoreConnection(ABC):
    @abstractmethod
    def search(self, select_fields, src_fields, ...):
        """多路检索统一入口"""
    @abstractmethod
    def insert(self, rows, idx_nm):
        """批量插入 chunk"""
    @abstractmethod
    def delete(self, condition, idx_nm):
        """条件删除"""
```

**ES 存储结构示例**：
```json
{
  "_index": "ragflow_tenant_uuid",
  "_source": {
    "content_with_weight": "RAG 是检索增强生成技术...",
    "content_ltks": "RAG 检索 增强 生成",
    "content_sm_ltks": "RAG 检索 增强 生成 检索增强 增强生成",
    "q_768_vec": [0.12, -0.34, 0.56, ...],    // 768 维向量
    "docnm_kwd": "技术文档.pdf",
    "doc_id": "uuid-xxx",
    "kb_id": "kb-uuid",
    "page_num_int": 3,
    "position_int": [100, 500],
    "title_tks": "第1章 RAG概述",
    "important_kwd": ["RAG", "检索", "生成"],
    "tag_kwd": {"PAGERANK_FLD": 0.85}
  }
}
```

---

# 第二层：在线检索（用户提问）

---

## 步骤1：查询优化（多轮合并）

**做什么**：将多轮对话历史发送给 LLM，重构为一个独立完整的问题

**代码位置**：`api/db/services/dialog_service.py` L538-L541

**关键代码**：
```python
if len(questions) > 1 and prompt_config.get("refine_multiturn"):
    questions = [await full_question(
        dialog.tenant_id, dialog.llm_id, messages)]
else:
    questions = questions[-1:]  # 只用最后一条问题
```

**示例**：
- 输入：Q1="什么是RAG？" A1="RAG是检索增强生成..." Q2="它有什么优势？"
- 输出：`"RAG（检索增强生成）技术相比纯LLM有哪些优势和特点？"`

---

## 步骤2：查询优化（跨语言翻译）

**代码位置**：`dialog_service.py` L543-L544

```python
if prompt_config.get("cross_languages"):
    questions = [await cross_languages(
        dialog.tenant_id, dialog.llm_id,
        questions[0], prompt_config["cross_languages"])]
```

---

## 步骤3：查询优化（关键词提取）

**代码位置**：`dialog_service.py` L556-L557

```python
if prompt_config.get("keyword", False):
    questions[-1] += await keyword_extraction(
        chat_mdl, questions[-1])
```

---

## 步骤4：混合检索（全文 matchText + 向量 matchDense）

**做什么**：构造全文查询表达式 + 调用 Embedding 向量化问题 + weighted_sum 融合

**代码位置**：`rag/nlp/search.py` L74-L171（`Dealer.search()`）

**关键代码**：
```python
async def search(self, req, idx_names, kb_ids, emb_mdl=None):
    qst = req.get("question", "")

    # ===== 步骤A：全文查询 =====
    matchText, keywords = self.qryr.question(qst, min_match=0.3)
    # → FulltextQueryer.question() 构造 MatchTextExpr
    # → 包含：词权重计算（IDF×NER×POS）+ 同义词扩展 + 细粒度分词（1~5-grams）

    # ===== 步骤B：向量查询 =====
    matchDense = await self.get_vector(qst, emb_mdl, topk, 0.1)
    # → emb_mdl.encode_queries() 将问题转为向量
    # → 返回 MatchDenseExpr(cosine, topk, similarity=0.1)

    # ===== 步骤C：加权融合（weighted_sum） =====
    fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
    matchExprs = [matchText, matchDense, fusionExpr]
    # → 全文5% + 向量95%

    res = await thread_pool_exec(
        self.dataStore.search, ...)
```

**全文查询构造的详细链路**（`rag/nlp/query.py` L41-L168）：
```python
class FulltextQueryer:
    query_fields = [
        "title_tks^10",         # 标题 ×10 权重
        "title_sm_tks^5",       # 细粒度标题 ×5
        "important_kwd^30",     # 重要关键词 ×30（最高）
        "important_tks^20",     # 重要 token ×20
        "question_tks^20",      # 问题文本 ×20
        "content_ltks^2",       # 正文内容 ×2
        "content_sm_ltks",      # 细粒度正文 ×1
    ]

    def question(self, txt, min_match=0.6):
        # 中文路径：细粒度分词 + 同义词 + 双层表达式
        for tt in self.tw.split(txt)[:256]:
            twts = self.tw.weights([tt])       # 计算词权重
            syns = self.syn.lookup(tt)         # 查找同义词
            sm = rag_tokenizer.fine_grained_tokenize(tk)  # 1~5-grams
```

**词权重计算链路**（`rag/nlp/term_weight.py` L164-L247）：
```python
def weights(self, tks):
    idf1 = np.array([idf(freq(t), 10000000) for t in tks])    # 词频 IDF
    idf2 = np.array([idf(df(t), 1000000000) for t in tks])    # 文档 IDF
    wts = (0.3*idf1 + 0.7*idf2) * np.array([ner(t)*postag(t) for t in tks])
    #      混合IDF              NER系数(公司/地名×3) 词性系数(副词×0.3)
    S = np.sum([s for _, s in tw])
    return [(t, s/S) for t, s in tw]  # 归一化
```

---

## 步骤5：降级重试

**做什么**：第一轮检索无结果时，降低阈值重试

**代码位置**：`rag/nlp/search.py` L136-L146

**关键代码**：
```python
# 第一轮：total == 0（无结果）
if total == 0:
    if filters.get("doc_id"):
        # 指定文档 → 去掉所有检索条件直接返回
        res = await thread_pool_exec(
            self.dataStore.search, src, [], filters, [], ...)
    else:
        # 降低阈值重试
        matchText, _ = self.qryr.question(qst, min_match=0.1)   # 0.3→0.1
        matchDense.extra_options["similarity"] = 0.17            # 0.1→0.17
        res = await thread_pool_exec(
            self.dataStore.search, src, highlightFields, filters,
            [matchText, matchDense, fusionExpr], ...)
```

**三级降级策略**：
```
第一轮：min_match=0.3, similarity=0.1
第二轮：min_match=0.1, similarity=0.17
第三轮：无过滤条件（仅 doc_ids 场景）
```

---

## 步骤6：重排序（本地多因子）

**做什么**：对候选结果重新排序，使用 token 相似度 + 向量余弦 + 标签加权 + PageRank 四因子

**代码位置**：`rag/nlp/search.py` L270-L333（`Dealer.rerank()`）、L335-L356（`Dealer.rerank_by_model()`）

**关键代码 — 本地重排序**：
```python
def rerank(self, sres, query, tkweight=0.3, vtweight=0.7, rank_feature=None):
    # 步骤1：构建 token 权重矩阵（不同字段加权）
    for i in sres.ids:
        tks = content_ltks + title_tks*2 + important_kwd*5 + question_tks*6

    # 步骤2：计算混合相似度
    sim, tksim, vtsim = self.qryr.hybrid_similarity(
        sres.query_vector, ins_embd, keywords, ins_tw, tkweight, vtweight)

    # 步骤3：标签加权
    rank_fea = self._rank_feature_scores(rank_feature, sres)

    # 步骤4：融合
    return 0.3*tksim + 0.7*vtsim + rank_fea + pagerank, tksim, vtsim
```

**关键代码 — 外部模型重排序**：
```python
def rerank_by_model(self, rerank_mdl, sres, query, tkweight=0.3, vtweight=0.7):
    tksim = self.qryr.token_similarity(keywords, ins_tw)     # token 相似度
    vtsim, _ = rerank_mdl.similarity(query, texts)            # 调用外部 API
    rank_fea = self._rank_feature_scores(rank_feature, sres)  # 标签加分
    return tkweight*array(tksim) + vtweight*vtsim + rank_fea, tksim, vtsim
```

---

## 步骤7：阈值过滤 + 结果聚合

**代码位置**：`rag/nlp/search.py` L364-L521（`Dealer.retrieval()` 全流程）

**关键代码**：
```python
async def retrieval(self, question, embd_mdl, tenant_ids, kb_ids, ...):
    # ===== 粗排：search() 取 top-1024 =====
    req = {"size": RERANK_LIMIT, "topk": 1024, ...}
    sres = await self.search(req, ...)

    # ===== 精排：rerank() 对前64个排序 =====
    sim, tsim, vsim = self.rerank(sres, question, ...)

    # ===== 阈值过滤 =====
    sorted_idx = np.argsort(sim * -1)           # 降序
    valid_idx = [i for i in sorted_idx if sim[i] >= post_threshold]

    # ===== 分页截取 =====
    page_idx = valid_idx[begin:end]

    # ===== 按文档聚合 =====
    for i in valid_idx:
        dnm = sres.field[i].get("docnm_kwd", "")
        ranks["doc_aggs"][dnm]["count"] += 1
    # → {"技术文档.pdf": {"doc_id":"xxx","count":5}, "产品手册.pdf":{"count":3}, ...}
```

---

# 第三层：生成回答

---

## 步骤1：构建 Prompt

**做什么**：将知识块格式化为 LLM Prompt 模板 → 添加引用提示词 → 拼接对话历史

**代码位置**：`api/db/services/dialog_service.py` L638-L667

**关键代码**：
```python
# 知识块格式化（rag/prompts/generator.py）
knowledges = kb_prompt(kbinfos, max_tokens)

# 拼接到 Prompt 模板
kwargs["knowledge"] = "\n------\n" + "\n\n------\n\n".join(knowledges)

# 构建系统消息
msg = [{"role": "system", "content": prompt_config["system"].format(**kwargs)}]

# 引用提示词（双重保障之一）
if knowledges and prompt_config.get("quote", True):
    prompt4citation = citation_prompt()
    msg[0]["content"] += prompt4citation

# 追加对话历史
msg.extend([{"role": m["role"], "content": m["content"]}
            for m in messages if m["role"] != "system"])
```

**Prompt 模板示例**：
```
You are an intelligent assistant.
**Essential Rules:**
- When information is available: Summarize the content.
- When information is unavailable: "The answer is not found in the knowledge base!"

## Knowledge Base:
------
文档名: 技术文档.pdf
袁隆平先生（1930-2021）是中国著名的农业科学家，被誉为"杂交水稻之父"...
------
文档名: 百科摘要.pdf
袁隆平，男，汉族，江西省九江市人，中国杂交水稻育种专家...

## Current Date: 2026-04-09

## Citation Requirement:
When using information from the knowledge base, cite the source using [ID:n] format.

[User's question here]
```

---

## 步骤2：Token 裁剪

**做什么**：确保总 token 数不超过模型限制（95% max_tokens）

**代码位置**：`dialog_service.py` L656 → `rag/prompts/generator.py`（`message_fit_in()`）

**关键代码**：
```python
used_token_count, msg = message_fit_in(msg, int(max_tokens * 0.95))
```

**内部逻辑**：从最早的消息开始逐条删除 → 每次删完重新计算总 token → 直到 < 目标值。系统消息永远不删（它是 Prompt 模板）。

---

## 步骤3：LLM 流式生成

**做什么**：调用 LLM 的流式 API，逐字生成并 yield

**代码位置**：
- `dialog_service.py` L750-L769
- `api/db/services/llm_service.py` L451-L492（`LLMBundle.async_chat_streamly_delta()`）

**关键代码**：
```python
# 流式生成
if stream:
    stream_iter = chat_mdl.async_chat_streamly_delta(
        prompt + prompt4citation, msg[1:], gen_conf)

    # 思考标签解析（DeepSeek-R1 等模型）
    async for kind, value, state in _stream_with_think_delta(stream_iter):
        yield {
            "answer": value,              # 当前文本块
            "reference": {},              # 引用信息
            "audio_binary": tts(tts_mdl, value),  # TTS 语音
            "final": False                # 是否最后一块
        }
```

**`_stream_with_think_delta()` 内部逻辑**：
```
检测 <think>  → 切换到"思考模式"，内容存入 think_content
检测 </think> → 切换到"输出模式"，后续内容正常 yield
普通内容     → 直接 yield
```

---

## 步骤4：引用插入后处理

**做什么**：将 LLM 回答切为句子，与检索 chunks 做语义匹配，插入 `[ID:n]` 引用

**代码位置**：`rag/nlp/search.py` L177-L267（`Dealer.insert_citations()`）

**关键代码**：
```python
def insert_citations(self, answer, chunks, chunk_v, embd_mdl,
                     tkweight=0.1, vtweight=0.9):
    # 步骤1：保护代码块不切分
    pieces = re.split(r"(```)", answer)

    # 步骤2：句子切分 + 过滤短句
    pieces = re.split(r"([^\|][；。？!！\n]|...)", answer)
    pieces_ = [t for t in pieces if len(t) >= 5]

    # 步骤3：对句子和 chunks 做 Embedding
    ans_v, _ = embd_mdl.encode(pieces_)

    # 步骤4：迭代阈值降级匹配
    thr = 0.63
    while thr > 0.3 and not cites:
        for i, sentence in enumerate(pieces_):
            sim = self.qryr.hybrid_similarity(
                ans_v[i], chunk_v, sentence_tks, chunks_tks,
                tkweight=0.1, vtweight=0.9)
            mx = np.max(sim) * 0.99
            if mx >= thr:
                cites[idx[i]] = [c for c in range(len(chunks)) if sim[c] > mx][:4]
        thr *= 0.8  # 降低阈值（0.63→0.50→0.40→0.32→...）

    # 步骤5：插入引用标记
    for i, p in enumerate(pieces):
        res += p
        if i in cites:
            for c in cites[i]:
                if c not in seted:
                    res += f" [ID:{c}]"
                    seted.add(c)
    return res, seted
```

**注意 tkweight=0.1/vtweight=0.9**：引用匹配更依赖语义——LLM 可能改写原文，"袁隆平先生是杂交水稻之父"可能被改写为"袁隆平被称为杂交水稻之父"，词面相似度低但语义相同。

---

## 步骤5：SSE 返回

**做什么**：将流式答案块以 SSE 格式推送给前端

**代码位置**：`api/apps/conversation_app.py` L224-L243

**关键代码**：
```python
async def stream():
    async for ans in async_chat(dia, msg, True, **req):
        ans = structure_answer(conv, ans, message_id, conv.id)
        yield "data:" + json.dumps({
            "code": 0, "message": "",
            "data": {
                "answer": ans["answer"],
                "reference": ans["reference"],
                "audio_binary": tts(tts_mdl, value),
                "final": False
            }
        }, ensure_ascii=False) + "\n\n"

    ConversationService.update_by_id(conv.id, conv.to_dict())
    yield "data:" + json.dumps({"code": 0, "data": True}) + "\n\n"

# SSE Response
resp = Response(stream(), mimetype="text/event-stream")
resp.headers["Cache-control"] = "no-cache"
resp.headers["Connection"] = "keep-alive"
resp.headers["X-Accel-Buffering"] = "no"       # 禁用 Nginx 缓冲
resp.headers["Content-Type"] = "text/event-stream; charset=utf-8"
return resp
```

**SSE 数据格式**：
```
data: {"code":0,"data":{"answer":"袁","reference":{},"final":false}}

data: {"code":0,"data":{"answer":"隆","reference":{},"final":false}}

data: {"code":0,"data":{"answer":"平","reference":{},"final":false}}

data: {"code":0,"data":true}    ← 结束标志
```

---

# 📊 补充：超出三层框架的关键技术点

## 补充1：Text2SQL 检索（结构化知识库优先通道）

**代码位置**：`dialog_service.py` L514-L525 → `use_sql()`

```python
# 如果知识库有字段映射（结构化数据），先尝试 SQL 精确查询
if field_map:
    ans = await use_sql(questions[-1], field_map, tenant_id,
                        chat_mdl, quote, kb_ids)
    if ans and (ans.get("reference", {}).get("chunks") or ans.get("answer")):
        yield ans   # SQL 成功 → 直接返回
        return
    # SQL 失败 → 降级到向量检索（静默降级，不报错）
```

**内部流程**：用户问题 → LLM 生成 SQL → Infinity/ES/OB 执行 SQL → 返回结构化结果

---

## 补充2：TOC 目录增强检索 + 子块展开

**代码位置**：`dialog_service.py` L621-L625

```python
# TOC 增强：用 LLM 从目录树中精选相关 chunk，替换原结果
if prompt_config.get("toc_enhance"):
    cks = await retriever.retrieval_by_toc(
        " ".join(questions), kbinfos["chunks"],
        tenant_ids, chat_mdl, dialog.top_n)
    if cks:
        kbinfos["chunks"] = cks

# 子块展开：检索到父块后，同时获取其子块
kbinfos["chunks"] = retriever.retrieval_by_children(
    kbinfos["chunks"], tenant_ids)
```

---

## 补充3：KG 知识图谱检索补充

**代码位置**：`dialog_service.py` L631-L636

```python
if prompt_config.get("use_kg"):
    ck = await settings.kg_retriever.retrieval(
        " ".join(questions), tenant_ids, dialog.kb_ids,
        embd_mdl, LLMBundle(tenant_id, default_chat_model))
    if ck["content_with_weight"]:
        kbinfos["chunks"].insert(0, ck)   # KG 结果插入最前（最高优先级）
```

---

## 补充4：Deep Research 深度研考模式

**代码位置**：`dialog_service.py` L569-L602

```python
if prompt_config.get("reasoning", False):
    reasoner = DeepResearcher(chat_mdl, prompt_config,
        partial(retriever.retrieval, ...))
    queue = asyncio.Queue()  # 异步通信
    task = asyncio.create_task(reasoner.research(...))
    while True:
        msg = await queue.get()
        # <START_DEEP_RESEARCH> → 开始展示思考过程
        # <END_DEEP_RESEARCH>   → 结束思考
        yield {"answer": msg, "reference": {}, ...}
```

---

# 📋 完整源码索引（面试速查表）

| 步骤 | 代码位置 | 核心函数 | 行号 |
|------|----------|----------|------|
| **格式检测+解析器选择** | `rag/app/naive.py` | `chunk()` / `PARSERS` | L254-L261, L729-L995 |
| **OCR 检测+识别** | `deepdoc/vision/ocr.py` | `OCR.ocr()` | L425-L542 |
| **布局识别** | `deepdoc/vision/layout_recognizer.py` | `LayoutRecognizer.__call__()` | 全文 |
| **表格结构识别** | `deepdoc/vision/table_structure_recognizer.py` | `construct_table()` | L92-L318 |
| **乱码检测+OCR降级** | `deepdoc/parser/pdf_parser.py` | `_is_garbled_text()` | L202-L300 |
| **文本分块（通用）** | `rag/nlp/__init__.py` | `naive_merge()` | L1070-L1126 |
| **文本分块（DOCX）** | `rag/nlp/__init__.py` | `naive_merge_docx()` | L1463-L1485 |
| **Chunk Token化** | `rag/nlp/__init__.py` | `tokenize_chunks()` | L302-L327 |
| **Embedding 向量化** | `rag/nlp/search.py` | `Dealer.get_vector()` | L52-L60 |
| **向量存储** | `common/doc_store/es_conn_base.py` | `insert()` | — |
| **RAG 流程总调度** | `api/db/services/dialog_service.py` | `async_chat()` | L455-L781 |
| **多轮合并** | `dialog_service.py` | `full_question()` | L538-L541 |
| **跨语言翻译** | `dialog_service.py` | `cross_languages()` | L543-L544 |
| **关键词提取** | `dialog_service.py` | `keyword_extraction()` | L556-L557 |
| **全文查询构造** | `rag/nlp/query.py` | `FulltextQueryer.question()` | L41-L168 |
| **词权重计算** | `rag/nlp/term_weight.py` | `Dealer.weights()` | L164-L247 |
| **同义词查找** | `rag/nlp/synonym.py` | `Dealer.lookup()` | L78-L103 |
| **混合检索** | `rag/nlp/search.py` | `Dealer.search()` | L74-L171 |
| **weighted_sum 融合** | `rag/nlp/search.py` | `FusionExpr(...)` | L127-L128 |
| **降级重试** | `rag/nlp/search.py` | 空结果处理 | L136-L146 |
| **本地重排序** | `rag/nlp/search.py` | `Dealer.rerank()` | L270-L333 |
| **外部重排序** | `rag/nlp/search.py` | `Dealer.rerank_by_model()` | L335-L356 |
| **阈值过滤+聚合** | `rag/nlp/search.py` | `Dealer.retrieval()` | L364-L521 |
| **Prompt 构建** | `rag/prompts/generator.py` | `kb_prompt()` / `citation_prompt()` | — |
| **Token 裁剪** | `rag/prompts/generator.py` | `message_fit_in()` | — |
| **LLM 流式生成** | `api/db/services/llm_service.py` | `async_chat_streamly_delta()` | L451-L492 |
| **引用插入后处理** | `rag/nlp/search.py` | `insert_citations()` | L177-L267 |
| **SSE 响应** | `api/apps/conversation_app.py` | `stream()` | L224-L243 |
| **Text2SQL 检索** | `dialog_service.py` | `use_sql()` | L514-L525 |
| **TOC 增强+子块展开** | `dialog_service.py` | `retrieval_by_toc()` / `retrieval_by_children()` | L621-L625 |
| **KG 知识图谱** | `dialog_service.py` | `use_kg` | L631-L636 |
| **Deep Research** | `dialog_service.py` | `reasoning` 模式 | L569-L602 |
