# RAGFlow 项目 — 面试脱稿讲解指南（7 个职责完整版）

**目标**：每个职责能脱稿讲解 2~3 分钟，7 个职责总计 15~20 分钟，覆盖所有面试官可能的提问点。

**背诵方法**：
1. 先记住"场景→核心问题→解决方案→成果"四段式结构
2. 每个职责记住 1~2 个关键代码片段（不用背完整代码，记核心逻辑）
3. 每个职责准备 3 个量化数据
4. 用 STAR 法则组织语言：Situation → Task → Action → Result

---

## 职责一：PDF 乱码检测与 OCR 回退机制（2~3 分钟）

### 脱稿讲解稿

"我负责的一个功能是 PDF 文档的乱码自动检测和 OCR 回退。场景是这样的：客户上传了很多内部 PDF，比如合同、技术手册，其中大概 30% 是扫描件或者用了特殊字体。用 pdfplumber 直接提取的话，会出现大量乱码，比如 `㐀`、`궅` 这种 PUA 字符，用户反馈说知识库答案全是乱码。

我实现了**两层检测策略**。

**第一层是 PUA/CID 字符检测**。代码在 `pdf_parser.py` 里，核心逻辑是统计文本中 PUA 字符和 CID 占位符的占比：

```python
def _is_garbled_text(text, threshold=0.5):
    if RAGFlowPdfParser._CID_PATTERN.search(text):
        return True
    garbled_count = sum(1 for ch in text if RAGFlowPdfParser._is_garbled_char(ch))
    return garbled_count / total >= threshold
```

如果乱码字符占比超过 50%，就判定这页是乱码。

**第二层是字体编码检测**。有些 PDF 用了子集化字体，把中文字符映射到了 ASCII 码点上，提取出来全是标点符号。检测逻辑是：如果页面用了子集字体，且 CJK 字符占比极低（<5%）、标点占比极高（>40%），就判定为编码异常。

```python
def _is_garbled_by_font_encoding(page_chars, min_chars=20):
    subset_font_count = sum(1 for c in page_chars if _has_subset_font_prefix(c.get("fontname")))
    cjk_like = sum(1 for c in page_chars if is_cjk_char(c))
    ascii_punct = sum(1 for c in page_chars if is_ascii_punct(c))
    return cjk_like / total < 0.05 and ascii_punct / total > 0.4
```

检测到乱码后，系统自动把页面渲染成图片，走 OCR 管道。OCR 用的是 ONNX Runtime 本地推理，包括 DB 文本检测模型定位文字区域，CTC 模型识别文字，最后 LayoutRecognizer 做 11 类版式分类。

**成果**：扫描件 PDF 的可读内容提取率从 0% 提升到 92% 以上，误判率控制在 2% 以下，客户乱码工单下降了 90%。"

### 面试官可能提问的点

| 问题 | 回答要点 |
|-----|---------|
| "PUA 字符是什么？" | Unicode 的私人使用区（Private Use Area），PDF 解析器用这些码点表示无法映射的字符 |
| "为什么用 0.5 作为阈值？" | 经验值，测试了 0.3/0.5/0.7，0.5 在准确率和误判率间平衡最好 |
| "OCR 比 pdfplumber 慢多少？" | 大约 5~8 倍，所以双层检测很重要，避免对正常文档误触发 OCR |
| "DB 算法和 CTC 算法是什么？" | DB（Differentiable Binarization）是可微分二值化的文本检测算法，CTC（Connectionist Temporal Classification）是不需要字符级对齐的文本识别算法 |
| "如果 OCR 也失败了怎么办？" | 标记文档为解析失败，记录错误日志，通知用户更换文档或调整解析器 |

---

## 职责二：多后端文档解析适配层（2~3 分钟）

### 脱稿讲解稿

"我负责的第二个功能是文档解析的多后端适配层。场景是不同客户对解析效果有不同偏好：金融客户要求表格精确还原，偏好 MinerU；法律客户要求排版保真，偏好 DeepDOC；有些私有化环境没有 GPU，只能用 Docling。

原来的代码里，各解析器接口不统一，新增一个解析方式要改上层分块逻辑，扩展成本很高。

我的解决方案是**策略模式**。在 `rag/app/naive.py` 里定义了一个 `PARSERS` 字典：

```python
PARSERS = {
    "deepdoc": by_deepdoc,
    "mineru": by_mineru,
    "docling": by_docling,
    "tcadp parser": by_tcadp,
    "paddleocr": by_paddleocr,
    "plaintext": by_plaintext,
}
```

每个 `by_*()` 函数保证相同的输入签名和返回值格式 `(sections, tables, pdf_parser)`。上层 `chunk()` 函数完全不需要关心底层用的是哪个解析器。

以 MinerU 接入为例，配置获取有三级优先级：传入参数 > 数据库查询 > 环境变量自动创建。`ensure_mineru_from_env()` 方法会读取环境变量（如 `MINERU_APISERVER`），自动在 `tenant_llm` 表中创建模型记录，实现零配置接入。

```python
def by_mineru(filename, ..., tenant_id=None, **kwargs):
    env_name = TenantLLMService.ensure_mineru_from_env(tenant_id)
    candidates = TenantLLMService.query(tenant_id=tenant_id, llm_factory="MinerU")
    mineru_llm_name = candidates[0].llm_name if candidates else env_name
    ocr_model = LLMBundle(tenant_id, model_config=ocr_model_config, lang=lang)
    sections, tables = ocr_model.mdl.parse_pdf(filepath=filename, ...)
    return sections, tables, pdf_parser
```

**成果**：新增解析后端的接入成本从 3~5 天降到半天以内，上层 `chunk()` 代码量减少 40%，支持 5 种引擎热插拔切换。"

### 面试官可能提问的点

| 问题 | 回答要点 |
|-----|---------|
| "为什么不用 if-elif？" | 违反开闭原则，新增解析器要改原有代码；策略模式新增只需注册到字典 |
| "返回值为什么统一成三元组？" | `sections` 是文本段落列表，`tables` 是表格列表，`pdf_parser` 是解析器实例用于后续提取位置信息 |
| "`ensure_*_from_env` 做了什么？" | 从环境变量读取配置，检查数据库是否已有相同配置，没有则自动创建 tenant_llm 记录 |
| "如果 MinerU 解析失败了怎么办？" | 捕获异常，记录错误日志，通过 callback 通知用户，返回 None 让上层处理 |
| "不同解析器的结果格式一致吗？" | 通过 `vision_figure_parser_pdf_wrapper()` 后处理，统一表格和图片的格式 |

---

## 职责三：重叠分块策略（2~3 分钟）

### 脱稿讲解稿

"我负责的第三个功能是文本分块策略的优化。场景是用户提问'合同的违约责任条款有哪些'，但违约责任条款恰好被切在两个 chunk 的边界，每个 chunk 只有半个条款，检索到的内容不完整，LLM 无法给出完整回答。这类问题在 FAQ 查询中占比约 20%。

核心实现是 `rag/nlp/__init__.py` 里的 `naive_merge()` 函数：

```python
def naive_merge(sections, chunk_token_num=128, delimiter="\n。；！？", overlapped_percent=0):
    cks = [""]
    tk_nums = [0]
    
    def add_chunk(t, pos):
        tnum = num_tokens_from_string(t)
        # 如果当前 chunk 超过阈值，开启新 chunk
        if cks[-1] == "" or tk_nums[-1] > chunk_token_num * (100 - overlapped_percent) / 100.:
            # 取上一个 chunk 的尾部作为重叠部分
            overlapped = cks[-1]
            t = overlapped[int(len(overlapped) * (100 - overlapped_percent) / 100.):] + t
            cks.append(t)
            tk_nums.append(tnum)
        else:
            cks[-1] += t
            tk_nums[-1] += tnum
```

核心优化点有三个：

**第一，重叠分块**。`overlapped_percent` 参数控制，比如设 10%，则每个新 chunk 会包含上一个 chunk 尾部 10% 的内容。这样跨边界的信息不会丢失。

**第二，子分隔符递归切分**。支持自定义分隔符，比如 Markdown 文档可以用 `##`、`###` 作为二级分隔符，保持层级结构不被破坏。

**第三，图片/表格上下文关联**。通过 `table_context_size` 和 `image_context_size` 参数，在表格和图片前后附加相邻文本，避免孤立的表格无法理解。

**成果**：跨边界截断问题从 20% 降到 3% 以下，FAQ 回答完整度评分从 3.2 提升到 4.6（5 分制）。"

### 面试官可能提问的点

| 问题 | 回答要点 |
|-----|---------|
| "Chunk size 为什么选 512？" | 实验对比了 256/512/1024，256 语义不完整，1024 检索粒度太粗，512 是平衡点 |
| "Overlap 设多少合适？" | 通常 10%~20%，太小补偿不够，太大会增加 20%+ 存储和计算开销 |
| "表格被切断了怎么办？" | `attach_media_context()` 在表格前后附加上下文文本，或整段保留不截断 |
| "怎么计算 token 数？" | 用 `num_tokens_from_string()`，基于 tiktoken 或 sentencepiece 的分词器 |
| "如果 chunk 超过模型最大长度怎么办？" | `memory_prompt()` 硬截断到 `max_tokens * 0.97`，优先保留前面的 chunk |

---

## 职责四：混合检索与降级重试（2~3 分钟）

### 脱稿讲解稿

"我负责的第四个功能是混合检索和降级重试。场景是在医疗、法律领域，用户经常输入缩写词或专业术语，比如'CT'、'GPL'，纯向量检索容易把这些短词匹配到错误语境；同时有些冷门问题首次检索可能返回空结果。

核心实现在 `rag/nlp/search.py` 的 `search()` 和 `retrieval()` 方法。

**混合检索**包含三个部分：

```python
async def search(self, req, idx_names, kb_ids, emb_mdl=None, ...):
    # ① 全文检索（BM25）
    matchText, keywords = self.qryr.question(qst, min_match=0.3)
    
    # ② 向量检索（余弦相似度）
    matchDense = await self.get_vector(qst, emb_mdl, topk, req.get("similarity", 0.1))
    
    # ③ 加权融合
    if not settings.DOC_ENGINE_INFINITY:
        # ES 模式：用 should 子句同时匹配文本和向量
        matchExprs = [matchText, matchDense]
    else:
        # Infinity 模式：用 FusionExpr 加权融合
        fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
        matchExprs = [matchText, matchDense, fusionExpr]
```

BM25 权重 5%，向量权重 95%。BM25 虽然权重低，但对专业术语和缩写词的精确匹配能力很强，能补偿向量检索的不足。

**降级重试机制**：

```python
if not res["hits"]["total"]:
    if min_match > 0.1:
        # 降低阈值重试
        res = await self.retrieval(..., min_match=0.1, similarity=0.17)
```

当首次检索无结果时，BM25 的 `min_match` 从 0.3 降到 0.1，向量的 `similarity_threshold` 从 0.1 降到 0.17，召回范围显著扩大。

**成果**：专业术语 Top-5 命中率提升 15%~25%，零结果率从 8% 降到 1% 以下。"

### 面试官可能提问的点

| 问题 | 回答要点 |
|-----|---------|
| "为什么不用纯向量检索？" | 向量检索擅长语义匹配，但对短词、缩写词、专业术语容易误匹配；BM25 擅长关键词精确匹配 |
| "权重 0.05:0.95 怎么定的？" | 经验值，BM25 主要起补偿作用，不过度依赖；可根据业务调整 |
| "降级重试会不会返回很多不相关结果？" | 会，但这是权衡——有结果总比没结果好，且重排阶段会进一步筛选 |
| "ES 和 Infinity 有什么区别？" | ES 用 should 子句同时匹配文本和向量；Infinity 原生支持 FusionExpr 加权融合 |
| "如果检索结果还是为空怎么办？" | 返回提示"未找到相关内容，请尝试其他关键词"，或降级为纯 LLM 回答 |

---

## 职责五：混合重排算法（2~3 分钟）

### 脱稿讲解稿

"我负责的第五个功能是检索结果的混合重排。场景是混合检索返回 Top-50 候选，但排序主要依赖向量相似度，对于同义词替换的内容排名靠后。比如用户问'如何申请休假'，文档写的是'请假流程'，向量相似度低导致排在第 30 名之后，没进入最终的 Top-10。

核心实现在 `rag/nlp/search.py` 的 `rerank()` 方法：

```python
def rerank(self, sres, query, tkweight=0.3, vtweight=0.7, ...):
    _, keywords = self.qryr.question(query)
    
    for i in sres.ids:
        # Token 相似度：查询关键词与 chunk 文本的匹配程度
        content_ltks = sres.field[i]["content_ltks"].split()
        title_tks = sres.field[i].get("title_tks", "").split()
        important_kwd = sres.field[i].get("important_kwd", [])
        tks = content_ltks + title_tks * 2 + important_kwd * 5
        ins_tw.append(tks)
    
    tksim = self.qryr.token_similarity(keywords, ins_tw)
    
    # 向量相似度
    vector = sres.field[i].get(f"q_{vector_size}_vec", zero_vector)
    vtsim = cosine_similarity(query_vec, vector)
    
    # PageRank + 标签特征
    rank_fea = self._rank_feature_scores(rank_feature, sres)
    
    # 三维加权融合
    sim = tkweight * tksim + vtweight * vtsim + rank_fea
```

重排有三个维度：

**Token 相似度**（权重 30%）：查询关键词与 chunk 文本的精确匹配程度，对同义词不敏感但对抗干扰强。

**向量相似度**（权重 70%）：语义层面的匹配，能捕捉同义词和近义表达。

**PageRank + 标签特征**：基于文档引用关系计算的权威性分数，以及查询标签与文档标签的匹配度。

如果有专用 Rerank 模型（如 Jina、BGE-Reranker），会调用 `rerank_by_model()` 用 Cross-Encoder 做精排。

**成果**：同义内容排名从第 28 位提升到前 5 位，Top-3 准确率提升 18%。"

### 面试官可能提问的点

| 问题 | 回答要点 |
|-----|---------|
| "重排和检索有什么区别？" | 检索是召回阶段，目标是找全；重排是精排阶段，目标是找准 |
| "Cross-Encoder 和 Bi-Encoder 有什么区别？" | Bi-Encoder 分别编码 query 和 doc，速度快但精度低；Cross-Encoder 联合编码，精度高但速度慢 |
| "PageRank 怎么算到文档上的？" | 基于文档间的引用关系，被引用越多分数越高，类似网页排名 |
| "为什么 Token 相似度权重只有 30%？" | Token 匹配对同义词不敏感，主要起辅助作用；向量相似度才是主力 |
| "如果重排后结果还是不好怎么办？" | 分析 bad case，调整权重，或引入更强大的 Rerank 模型 |

---

## 职责六：Prompt 引擎与引用标注（2~3 分钟）

### 脱稿讲解稿

"我负责的第六个功能是 Prompt 模板引擎和引用标注机制。场景是用户反馈两个问题：一是 AI 经常编造文档里没有的信息（幻觉），二是无法追溯回答来源。

核心实现在 `rag/prompts/generator.py`，基于 Jinja2 模板引擎：

```python
PROMPT_JINJA_ENV = jinja2.Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)

def citation_prompt(user_defined_prompts: dict = {}) -> str:
    template = PROMPT_JINJA_ENV.from_string(
        user_defined_prompts.get("citation_guidelines", CITATION_PROMPT_TEMPLATE)
    )
    return template.render()
```

核心能力有四个：

**引用标注**：在 System Prompt 中要求 LLM 使用 `[ID:x]` 格式标注每条信息的来源。比如：

```
## 知识库
[ID:1] 员工请假需提前 3 天申请...
[ID:2] 年假天数按工龄计算...

请基于以上知识回答，并使用 [ID:x] 标注来源。
```

**关键词自动提取**：用 LLM 为每个 chunk 生成 3 个关键词，写入 `important_kwd` 字段，辅助检索。

**问题自动生成**：为每个 chunk 生成 3 个潜在用户问题，写入 `question_kwd` 字段，提升长尾问题召回率。

**上下文长度控制**：`memory_prompt()` 按 token 数硬截断，保留 `max_tokens * 0.97` 以内，防止超出模型上下文窗口。

```python
def memory_prompt(message_list, max_tokens):
    used_token_count = 0
    for message in message_list:
        current_tokens = num_tokens_from_string(message["content"])
        if used_token_count + current_tokens > max_tokens * 0.97:
            break
        content_list.append(message["content"])
        used_token_count += current_tokens
    return content_list
```

**成果**：引用标注使回答可追溯率达到 100%，长尾问题召回率提升 12%，用户可信度评分提升 1.4 分（5 分制）。"

### 面试官可能提问的点

| 问题 | 回答要点 |
|-----|---------|
| "怎么防止 LLM 幻觉？" | 引用标注约束 + 检索到的上下文作为证据 + 要求模型只基于提供的内容回答 |
| "LLM 不遵守引用格式怎么办？" | 在 Prompt 中给出示例 + 后处理正则提取 + 不强制要求（ graceful degradation）|
| "上下文超过模型限制怎么办？" | `memory_prompt()` 硬截断到 97%，优先保留前面的 chunk |
| "关键词提取用的是什么模型？" | 复用对话所用的 LLM，通过 Prompt 要求生成关键词，temperature 设 0.2 保证稳定 |
| "Prompt 模板怎么管理的？" | Jinja2 模板引擎，支持用户自定义覆盖默认模板，存储在 `rag/prompts/*.md` 文件中 |

---

## 职责七：异步任务 Pipeline（2~3 分钟）

### 脱稿讲解稿

"我负责的第七个功能是异步任务 Pipeline 的并发控制。场景是客户一次性上传 500 份 PDF 合同，总计 2 万多页，同步处理导致 API 超时；同时相同文件重复上传每次都重新解析，资源浪费严重。

核心实现在 `rag/svr/task_executor.py`，基于 asyncio.Semaphore 实现多级并发限流：

```python
MAX_CONCURRENT_TASKS = int(os.environ.get('MAX_CONCURRENT_TASKS', '5'))
task_limiter = asyncio.Semaphore(MAX_CONCURRENT_TASKS)          # 任务级
chunk_limiter = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_BUILDERS) # 分块级
embed_limiter = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_BUILDERS) # 向量化级
minio_limiter = asyncio.Semaphore(10)                            # IO级
```

为什么用 Semaphore 而不是 Lock？因为 Semaphore 允许同时有 N 个协程通过，适合控制并发度；Lock 只能有一个，粒度太粗。

**任务复用优化**：基于 xxhash 摘要匹配判断是否是同一任务。

```python
hasher = xxhash.xxh64()
for field in sorted(chunking_config.keys()):
    hasher.update(str(chunking_config[field]).encode("utf-8"))
hasher.update(str(task.get("from_page", "")).encode("utf-8"))
task_digest = hasher.hexdigest()
# 如果 prev_task.digest == task.digest 且已完成 → 直接复用 chunk_ids
```

**向量化批处理优化**：

```python
async def embedding(docs, mdl, parser_config=None, callback=None):
    batch_size = mdl.batch_size or 16
    title_w = float(parser_config.get("filename_embd_weight", 0.1))
    # 标题向量加权融合
    vects = title_w * tts + (1 - title_w) * cnts
```

标题向量的权重是 0.1，意思是标题对整体向量贡献 10%，内容贡献 90%。这样同一文档的 chunk 会有相似的向量方向，提升文档级别检索效果。

**成果**：上传 API 响应时间从分钟级降到 2 秒以内，单机稳定处理 1000+ 页 PDF，重复上传节省 90% 计算资源。"

### 面试官可能提问的点

| 问题 | 回答要点 |
|-----|---------|
| "为什么用异步而不是多线程？" | Python GIL 限制，多线程不适合 CPU 密集型；asyncio 适合 IO 密集型（网络请求、文件读写） |
| "Semaphore 和 Lock 的区别？" | Semaphore 允许 N 个同时通过，Lock 只允许 1 个；Semaphore 更适合控制并发度 |
| "xxhash 冲突怎么办？" | 冲突概率极低（64 位哈希）；即使冲突，最多是误复用，不会导致错误结果 |
| "为什么标题权重是 0.1？" | 实验得出，太高会导致内容信息被稀释，太低失去标题的聚合作用 |
| "如果任务执行到一半挂了怎么办？" | 任务状态持久化到 MySQL，Worker 重启后从 Redis 队列重新消费未完成任务 |

---

## 脱稿练习计划

### 第 1 天：理解阶段
- 通读每个职责的讲解稿，理解"场景→问题→方案→成果"的逻辑链
- 对照代码文件，确认每个技术点的实现位置

### 第 2~3 天：记忆阶段
- 每个职责背诵"3 个核心点 + 1 个代码片段 + 3 个量化数据"
- 用录音或对着镜子练习，确保流畅

### 第 4~5 天：模拟阶段
- 找朋友或自己模拟面试官，随机提问
- 练习从"场景描述"自然过渡到"技术实现"

### 第 6~7 天：优化阶段
- 控制每个职责的讲解时间在 2~3 分钟
- 确保 7 个职责总计能在 15~20 分钟内讲完
- 准备应对"夺命连环问"的递进回答

### 关键记忆口诀

| 职责 | 一句话概括 | 核心代码文件 |
|-----|-----------|-------------|
| ① 乱码检测 | "两层检测，PUA+字体，乱码走 OCR" | `pdf_parser.py` |
| ② 适配层 | "策略模式，PARSERS 字典，统一接口" | `naive.py` |
| ③ 重叠分块 | " overlapped_percent，尾部拼头部，防截断" | `rag/nlp/__init__.py` |
| ④ 混合检索 | "BM25+向量，FusionExpr，降级重试" | `search.py` |
| ⑤ 混合重排 | "Token+向量+PageRank，三维加权" | `search.py` |
| ⑥ Prompt 引擎 | "Jinja2模板，引用标注，防幻觉" | `generator.py` |
| ⑦ 异步 Pipeline | "Semaphore 限流，xxhash 复用，批处理" | `task_executor.py` |

---

## 面试现场应答 checklist

- [ ] 开场 30 秒："我主要负责了 7 个模块，分别是..."
- [ ] 每个职责讲完后停顿 2 秒，观察面试官反应
- [ ] 如果面试官点头，快速进入下一个职责
- [ ] 如果面试官皱眉或记录，准备应对追问
- [ ] 被追问时，先确认问题："您是想了解...对吗？"
- [ ] 不会的问题："这个我没有深入研究过，但我理解其核心是..."
- [ ] 结束前 30 秒：总结量化成果，强调业务价值
