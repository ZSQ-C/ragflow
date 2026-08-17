# RAGFlow 项目 — STAR 完整版脱稿讲解稿（7 职责）

> 定位：大模型应用开发 / RAG 工程师面试主推项目
> 目标：每个职责 2~3 分钟，7 个职责合计 15~20 分钟
> 背诵方法：S（场景一句话）→ T（任务一句话）→ A（2~3 个实现点+代码）→ R（3 个量化数据）→ 金句收尾

---

## 开场 30 秒（项目总览）

"我参与的项目是 RAGFlow，一个基于深度文档理解的开源 RAG 引擎，GitHub 40k+ Star。它解决的问题是：企业文档格式多样、扫描件多，通用 RAG 系统检索质量差、回答爱编造。我们的方案是覆盖'文档解析 → 文本分块 → 向量化 → 混合检索 → 重排 → Prompt 生成'的完整链路，用 Python + Quart 做后端、React 做前端、Docker 微服务部署。我主要负责其中 7 个核心模块：PDF 乱码检测与 OCR 回退、多后端解析适配层、重叠分块策略、混合检索与降级重试、混合重排、Prompt 引擎与引用标注、异步任务 Pipeline。整体效果是：扫描件解析可读率从 0 提到 92%，检索零结果率从 8% 降到 1% 以下，重复上传计算资源节省 90%。"

---

## 职责一：PDF 乱码自动检测与 OCR 回退机制（2~3 分钟）

### S — 场景

"客户上传了大量内部 PDF，合同、技术手册，其中约 30% 是扫描件或用了特殊字体。用 pdfplumber 直接提取会出现大量乱码字符，比如 PUA 区、CID 占位符，检索出来的内容根本没法读，用户工单反馈'知识库答案全是乱码'。"

### T — 任务

"我的任务是在解析链路里加一道'乱码检测闸门'：正常文档走快速文本提取，检测到乱码自动切换到 OCR 管道，同时不能误伤正常文档——因为 OCR 比文本提取慢 5~8 倍。"

### A — 实现（两层检测 + OCR 回退）

"我做了两层检测。第一层是 PUA/CID 字符占比检测，在 `deepdoc/parser/pdf_parser.py` 里：

```python
def _is_garbled_text(self, chars):
    pua_cid_count = sum(1 for c in chars if ord(c) in self.PUA_CHARS or ord(c) in self.CID_CHARS)
    return (pua_cid_count / max(len(chars), 1)) > 0.3   # 占比超30%判乱码
```

第二层是字体编码检测，针对子集化字体把中文映射到 ASCII 码点的情况：如果页面 CJK 字符占比低于 5%、标点占比高于 40%，判定为编码异常：

```python
def _is_garbled_by_font_encoding(self, page_chars):
    return cjk_ratio < 0.05 and punct_ratio > 0.4
```

检测到乱码后，把页面渲染成图片，走 ONNX Runtime 本地推理的 OCR 管道（`deepdoc/vision/ocr.py`）：DB 文本检测模型定位文字区域，CTC 模型逐区域识别，最后 LayoutRecognizer 做 11 类版式分类，再按阅读顺序合并文本块。"

### R — 成果（3 个量化数据）

"扫描件 PDF 可读内容提取率从 0% 提升到 92% 以上；双层检测把正常文档的误判率控制在 2% 以下，避免不必要的 OCR 开销；客户乱码工单下降了 90%。"

### 金句

"**用最小的检测成本，把最贵的 OCR 只用在真正需要它的页面上。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| PUA 字符是什么？ | Unicode 私人使用区，解析器用它表示无法映射的字符，特征是码点落在 E000-F8FF 区间 |
| 为什么阈值取 0.3？ | 经验值+测试：0.2 会把正常文档误判，0.5 会漏掉轻度乱码，0.3 在误判率和漏检率间平衡最好 |
| OCR 比 pdfplumber 慢多少？ | 5~8 倍，所以双层检测的核心价值是避免对正常文档触发 OCR |
| OCR 里 DB 和 CTC 是什么？ | DB 是可微分二值化文本检测；CTC 是免字符级对齐的序列识别 |
| OCR 也失败怎么办？ | 标记解析失败、记录错误日志、通知用户更换文档或调整解析器 |
| 为什么不直接全量走 OCR？ | 成本高 5~8 倍且对文本型 PDF 会引入识别错误，得不偿失 |

---

## 职责二：多后端文档解析适配层（2~3 分钟）

### S — 场景

"不同客户对解析效果偏好不同：金融客户要求表格精确还原，偏好 MinerU；法律客户要求排版保真，偏好 DeepDOC；有些私有化环境没 GPU，只能用 Docling。原来各解析器接口不统一，新增一种解析方式要改上层分块逻辑，扩展成本很高。"

### T — 任务

"设计一个统一适配层：上层分块逻辑不感知具体解析器，新增解析后端只加一个适配函数。"

### A — 实现（策略模式 + 统一三元组）

"我用策略模式，在 `rag/app/naive.py` 里维护一个 `PARSERS` 字典：

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

每个 `by_*()` 函数保证相同的输入签名和返回值格式 `(sections, tables, pdf_parser)`，上层 `chunk()` 完全不用关心底层是哪个解析器。以 MinerU 接入为例，配置获取是三级优先级：传入参数 > 数据库查询 > 环境变量自动创建。`ensure_mineru_from_env()` 会读环境变量，自动在 tenant_llm 表里创建模型记录，实现零配置接入。"

### R — 成果（3 个量化数据）

"新增一种解析后端的工作量从 3~5 天降到半天以内；上层 chunk() 代码量减少 40% 以上，消掉了大量 if-elif 分支；支持 5 种解析引擎热插拔切换。"

### 金句

"**把'每种解析器怎么调'的差异，收敛到一个字典、一套签名里。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么不用 if-elif？ | 违反开闭原则，每加一个解析器都要改主流程；策略模式新增只需往字典注册一行 |
| 返回值为什么统一三元组？ | sections 是文本段落、tables 是表格、pdf_parser 保留给后续位置信息提取 |
| ensure_*_from_env 做了什么？ | 读环境变量配置，查库是否已有相同配置，没有就自动创建 tenant_llm 记录 |
| MinerU 解析失败怎么办？ | 捕获异常、记录日志、callback 通知用户、返回 None 交给上层兜底 |
| 各解析器结果格式一致吗？ | 通过 vision_figure_parser_pdf_wrapper() 后处理统一表格和图片格式 |

---

## 职责三：重叠分块 + 子分隔符递归切分（2~3 分钟）

### S — 场景

"用户问'这份合同的违约责任条款有哪些'，但条款恰好被切在两个 chunk 的边界上，每个 chunk 只有半个条款，检索到也拼不出完整回答。这类因边界截断导致的问题在 FAQ 查询里占比约 20%。"

### T — 任务

"优化分块策略：保证 chunk 语义完整，跨边界信息不丢失，同时控制存储和检索开销。"

### A — 实现（重叠分块 + 递归切分 + 媒体上下文）

"核心是 `rag/nlp/__init__.py` 里的 `naive_merge()`：

```python
def naive_merge(sections, chunk_token_num=512, delimiter="\n", overlapped_percent=10):
    # 当前 chunk 超阈值时，取上一个 chunk 尾部 overlapped_percent% 拼到新 chunk 头部
    overlapped = cks[-1]
    t = overlapped[int(len(overlapped) * (100 - overlapped_percent) / 100.):] + t
```

三个核心点：第一，重叠分块——新 chunk 继承上一个 chunk 尾部 10% 的内容，跨边界信息不丢；第二，子分隔符递归切分——Markdown 文档按 `##`、`###` 作为二级分隔符细切，保持层级结构；第三，图片/表格上下文关联——通过 `table_context_size`、`image_context_size` 参数在表格图片前后附加相邻文本，避免孤立表格无法理解。"

### R — 成果（3 个量化数据）

"因跨边界截断导致的不完整回答从 20% 降到 3% 以下；FAQ 回答完整度评分从 3.2 提升到 4.6（5 分制）；支持按文档类型自定义分隔符配置。"

### 金句

"**分块的本质不是切文本，是保语义。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| Chunk size 为什么选 512？ | 对比 256/512/1024：256 语义不完整，1024 检索粒度太粗，512 是平衡点 |
| Overlap 设多少合适？ | 10%~20%；太小补偿不够，太大增加 20%+ 存储和计算开销 |
| 表格/代码被切断怎么办？ | attach_media_context() 前后附上下文，或整段保留不截断 |
| token 数怎么算？ | num_tokens_from_string()，基于 tiktoken/sentencepiece |
| chunk 超模型上限怎么办？ | memory_prompt() 硬截断到 max_tokens*0.97，优先保留前面的 |

---

## 职责四：混合检索与多级降级重试（2~3 分钟）

### S — 场景

"医疗、法律领域用户经常输入缩写词和专业术语，比如 CT、GPL，纯向量检索容易把这些短词匹配到错误语境；同时冷门问题首次检索可能返回空结果，体验很差。"

### T — 任务

"实现 BM25 全文检索 + 向量检索的混合方案，并加多级降级重试，把零结果率压下去。"

### A — 实现（BM25+向量融合 + 降级重试）

"核心在 `rag/nlp/search.py` 的 `search()` / `retrieval()`：

```python
# ① 全文检索（BM25）
matchText, keywords = self.qryr.question(qst, min_match=0.3)
# ② 向量检索（余弦相似度）
matchDense = await self.get_vector(qst, emb_mdl, topk, req.get("similarity", 0.1))
# ③ 加权融合：全文5% + 向量95%
fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
```

BM25 权重低，但擅长缩写词和术语的精确匹配，能补偿向量检索的语义误匹配。降级重试是关键容错：

```python
if not res["hits"]["total"]:
    if min_match > 0.1:
        res = await self.retrieval(..., min_match=0.1, similarity=0.17)
```

首次无结果时，BM25 的 min_match 从 0.3 降到 0.1、向量相似度阈值从 0.1 降到 0.17，大幅扩大召回。"

### R — 成果（3 个量化数据）

"专业术语/缩写词 Top-5 命中率比纯向量检索提升 15%~25%；多级降级使检索零结果率从 8% 降到 1% 以下；医疗/法律知识库用户满意度提升 0.8 分（5 分制）。"

### 金句

"**向量负责'语义像不像'，BM25 负责'词真的在不在'，两者互补才完整。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么不用纯向量检索？ | 向量对短词/缩写词易误匹配；BM25 关键词精确匹配是有效补偿 |
| 权重 0.05:0.95 怎么定的？ | 经验值，BM25 主要起补偿作用；可按业务调，比如法律场景可加大 BM25 |
| 降级会不会召回一堆不相关结果？ | 会有代价，但有结果比没结果好，且后续重排阶段会再筛选 |
| ES 和 Infinity 的差异？ | ES 用 should 子句同时匹配；Infinity 原生支持 FusionExpr 加权融合 |
| 降级后还为空怎么办？ | 返回提示让用户换关键词，或降级为纯 LLM 回答 |

---

## 职责五：Token + 向量 + PageRank 三维混合重排（2~3 分钟）

### S — 场景

"混合检索返回 Top-50 候选，但排序主要看向量相似度。用户问'如何申请休假'，文档写的是'请假流程'，同义表达向量相似度低，被排到 30 名之后，进不了最终送 LLM 的 Top-10。"

### T — 任务

"加一道重排：用多维度特征把'语义相关但表述不同'的内容提到前面，保证送进 LLM 的是真正相关的内容。"

### A — 实现（三维加权融合）

"核心在 `rag/nlp/search.py` 的 `rerank()`：

```python
tksim = self.qryr.token_similarity(keywords, ins_tw)      # Token 相似度（30%）
vtsim = cosine_similarity(query_vec, vector)              # 向量余弦相似度（70%）
rank_fea = self._rank_feature_scores(rank_feature, sres)  # PageRank + 标签特征
sim = tkweight * tksim + vtweight * vtsim + rank_fea      # 三维加权
```

三个维度：Token 相似度权重 30%，对同义词不敏感但抗干扰强；向量相似度权重 70%，捕捉语义；PageRank + 标签特征，基于文档引用关系算权威性，查询标签与文档标签做匹配。如果配了专用 Rerank 模型（Jina/BGE-Reranker），切换 `rerank_by_model()` 用 Cross-Encoder 精排。"

### R — 成果（3 个量化数据）

"同义表达相关内容平均排名从第 28 位提升到前 5 位；Top-3 准确率提升约 18%；PageRank 权重让高权威文档（公司政策、技术规范）曝光率提升 30%。"

### 金句

"**检索负责'找得全'，重排负责'排得准'，两段式才能兼顾效率和质量。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 重排和检索的区别？ | 检索是召回阶段目标找全，重排是精排阶段目标找准 |
| Bi-Encoder vs Cross-Encoder？ | Bi-Encoder 分开编码、快但精度低；Cross-Encoder 联合编码、精度高但慢，适合精排 |
| PageRank 怎么算到文档上？ | 基于文档引用关系，被引用越多权威性越高，类似网页排名 |
| Token 权重为什么只有 30%？ | Token 匹配对同义词不敏感，起辅助作用，向量才是主力 |
| 重排后还不好怎么办？ | 分析 bad case、调权重、换更强的 Rerank 模型 |

---

## 职责六：Prompt 引擎与引用标注机制（2~3 分钟）

### S — 场景

"用户反馈两个问题：AI 经常编造文档里没有的信息（幻觉）；回答无法追溯来源，没法验证可信度。"

### T — 任务

"用 Prompt 工程约束 LLM 行为：要求基于检索上下文回答，并用 [ID:x] 标注每条信息的来源。"

### A — 实现（Jinja2 模板 + 四类 Prompt 能力）

"核心在 `rag/prompts/generator.py`，基于 Jinja2 模板引擎：

```python
PROMPT_JINJA_ENV = jinja2.Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)

def citation_prompt(user_defined_prompts: dict = {}) -> str:
    template = PROMPT_JINJA_ENV.from_string(
        user_defined_prompts.get("citation_guidelines", CITATION_PROMPT_TEMPLATE)
    )
    return template.render()
```

四类能力：第一，引用标注——System Prompt 要求 LLM 用 `[ID:x]` 格式标注每条信息来源，知识库块按 `[ID:1] 员工请假需提前3天...` 编号；第二，关键词自动提取——用 LLM 给每个 chunk 生成 3 个关键词写进 important_kwd 字段辅助检索；第三，问题自动生成——为 chunk 生成 3 个潜在用户问题写进 question_kwd 提升长尾召回；第四，上下文长度控制——memory_prompt() 按 token 硬截断到 max_tokens*0.97，防止超窗口。"

### R — 成果（3 个量化数据）

"引用标注让回答可追溯率达到 100%；LLM 生成问题关键词使长尾问题召回率提升约 12%；带引用回答的可信度感知评分比无引用高 1.4 分（5 分制）。"

### 金句

"**防幻觉最有效的不是训模型，而是让模型'只能基于证据说话'。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 怎么防幻觉？ | 引用标注约束 + 检索上下文作证据 + 要求只基于提供内容回答 |
| LLM 不遵守引用格式怎么办？ | Prompt 给示例 + 后处理正则提取 + graceful degradation 不强制 |
| 上下文超限怎么办？ | memory_prompt() 硬截断到 97%，优先保留前面 chunk |
| 关键词提取用什么模型？ | 复用对话 LLM，temperature 设 0.2 保证稳定 |
| Prompt 模板怎么管理？ | Jinja2 模板 + 支持用户自定义覆盖，模板存 rag/prompts/*.md |

---

## 职责七：异步任务 Pipeline 并发控制与任务复用（2~3 分钟）

### S — 场景

"客户一次性上传 500+ 份 PDF 合同、总计 2 万多页，同步处理导致 API 超时；相同文件重复上传每次都重新解析，资源浪费严重。"

### T — 任务

"把解析流程异步化，用多级并发限流防止 OOM，并用任务复用避免重复解析。"

### A — 实现（Semaphore 多级限流 + xxhash 任务复用）

"核心在 `rag/svr/task_executor.py`，用 asyncio.Semaphore 做多级限流：

```python
MAX_CONCURRENT_TASKS = int(os.environ.get('MAX_CONCURRENT_TASKS', '5'))
task_limiter = asyncio.Semaphore(MAX_CONCURRENT_TASKS)           # 任务级
chunk_limiter = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_BUILDERS) # 分块级
embed_limiter = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_BUILDERS) # 向量化级
minio_limiter = asyncio.Semaphore(10)                            # IO级
```

选 Semaphore 而不是 Lock，因为要控制的是'同时 N 个协程通过'的并发度。任务复用基于 xxhash 摘要：把分块配置排序后做哈希，摘要一致且任务已完成就直接复用 chunk_ids，跳过解析。向量化还做了标题加权融合，标题权重 0.1、内容 0.9，让同文档 chunk 向量方向接近。"

### R — 成果（3 个量化数据）

"上传 API 响应从分钟级降到 2 秒以内（异步化后立即返回）；信号量限流让单机稳定处理 1000+ 页 PDF 并发解析不 OOM；任务复用使重复上传同文件解析时间节省 90% 以上。"

### 金句

"**异步解决'等不起'，限流解决'扛不住'，复用解决'算重了'。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么用异步不用多线程？ | Python GIL 限制多线程；asyncio 适合 IO 密集型（网络、文件） |
| Semaphore 和 Lock 区别？ | Semaphore 允许 N 个通过，Lock 只允许 1 个，Semaphore 适合控并发度 |
| xxhash 冲突怎么办？ | 64 位哈希冲突概率极低；即使冲突最多是误复用，不会产生错误结果 |
| 标题权重为什么 0.1？ | 实验得出：太高稀释内容信息，太低失去标题聚合作用 |
| 任务执行一半挂了？ | 任务状态持久化 MySQL，Worker 重启后从 Redis 队列重新消费未完成任务 |

---

## 七大职责记忆口诀

| 职责 | 一句话概括 | 核心文件 |
|-----|-----------|---------|
| ① 乱码检测 | "两层检测，PUA+字体，乱码走 OCR" | `pdf_parser.py` |
| ② 适配层 | "策略模式，PARSERS 字典，统一三元组" | `naive.py` |
| ③ 重叠分块 | "overlap 尾部拼头部，防边界截断" | `rag/nlp/__init__.py` |
| ④ 混合检索 | "BM25+向量，FusionExpr，降级重试" | `search.py` |
| ⑤ 混合重排 | "Token+向量+PageRank，三维加权" | `search.py` |
| ⑥ Prompt 引擎 | "Jinja2 模板，引用标注，防幻觉" | `generator.py` |
| ⑦ 异步 Pipeline | "Semaphore 限流，xxhash 复用，批处理" | `task_executor.py` |

---

## 面试现场 checklist

- [ ] 开场 30 秒项目总览（先讲链路，再讲 7 个职责，最后甩 3 个量化数据）
- [ ] 每个职责按 S→T→A→R 讲，A 部分只贴 1 个关键代码片段
- [ ] 每个职责 3 个量化数据倒背如流
- [ ] 职责之间用过渡句衔接："解决了文档解析，下一个问题是……"
- [ ] 被追问先确认问题："您是想了解……对吗？"
- [ ] 不会的问题："这个我没深入做过，但我理解其核心是……"
- [ ] 结束前 30 秒：总结构建了从解析到生成的完整 RAG 链路
