# 02 — 混合检索与精排全链路：Dealer.search() → retrieval() → rerank()

> **文件位置**：`rag/nlp/search.py` L36-L521
> **核心定位**：RAGFlow 检索系统的核心控制器，负责从查询构造到结果排序的完整链路
> **调用链**：`async_chat()` → `retriever.retrieval()` → `Dealer.search()` + `Dealer.rerank()` / `Dealer.rerank_by_model()`

---

## 一、核心总览（带逻辑关系）

### 1.1 核心定位

`Dealer` 类是 RAGFlow 整个检索系统的**总调度器**，位于 `rag/nlp/search.py` 中。它拥有两个关键属性：`self.qryr`（一个 `FulltextQueryer` 实例，负责将自然语言问题转为全文查询表达式）和 `self.dataStore`（一个 `DocStoreConnection` 实例，是对 Elasticsearch / Infinity / OceanBase 三种向量数据库后端的抽象封装）。`Dealer` 对外提供 `search()`（混合检索）和 `retrieval()`（检索精排全链路）两个高层接口，`async_chat()` 调用的是后者。

**适用场景**：
- PB 级企业知识库的语义搜索
- 多模态内容（文本+图片+表格）的统一检索
- 法律/金融等要求高精度的文档问答

**解决的业务问题**：
- 纯关键词匹配无法捕捉"怎么配置系统"和"系统设置指南"之间的语义关联
- 纯向量检索对专有名词（如"SKU-12345"）的精确匹配能力弱
- 单一排序因子导致相关文档排名不准确

### 1.2 整体流程串讲

当用户提问进入检索系统后，首先经过 `query.py` 中的 `FulltextQueryer.question()` 将自然语言转为 `MatchTextExpr` 全文查询表达式——这一步涉及词权重计算（`term_weight.py`）和同义词扩展（`synonym.py`），产出一个带加权字段的 Elasticsearch/Infinity 查询语句。

同时，`Dealer.get_vector()` 调用 Embedding 模型（如 BGE、OpenAI text-embedding）将问题转为向量，构造 `MatchDenseExpr` 向量查询表达式。

两路查询表达式通过 `FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})` 融合——这是 RAGFlow 的**混合检索**核心：全文查询占 5% 权重，向量查询占 95% 权重。融合理由是 RAG 场景下语义相关性比关键词精确匹配更重要。

融合后的查询提交给底层向量数据库（ES 用 weighted_sum 脚本，Infinity 用 FUSION 语法），返回 top-1024 候选结果。如果第一轮结果为 0，系统会自动降低阈值重试：全文 min_match 从 0.3 降到 0.1，向量 similarity 从 0.1 降至 0.17——这是**降级兜底策略**。

候选结果进入精排阶段。如果有外部重排序模型（如 Jina Rerank、CoHere），调用 `rerank_by_model()`；否则走本地多因子重排序 `rerank()`。公式为：`sim = 0.3 * token_sim + 0.7 * vector_cos + rank_fea + pagerank`。精排后按相似度阈值过滤，最终聚合到 `{"chunks": [...], "doc_aggs": {...}}` 返回。

---

## 二、模块拆分

### 模块1：向量查询构造 —— get_vector()（L52-L60）

**作用**：调用 Embedding 模型将用户问题转为向量，构造 `MatchDenseExpr`。在整体流程中位于检索的第一步（与全文查询并行）。

**与其他模块的配合关系**：产出的 `MatchDenseExpr` 传给 `search()` 与 `MatchTextExpr` 融合。

### 模块2：混合检索主入口 —— search()（L74-L171）

**作用**：协调全文+向量两路查询的构造、融合和提交，处理三分支路由和降级重试。是整个检索流程的"中央调度器"。

**与其他模块的配合关系**：调用模块1（get_vector）和 `FulltextQueryer.question()` 获取查询表达式，融合后提交给 `self.dataStore.search()`。

### 模块3：本地多因子重排序 —— rerank()（L270-L333）

**作用**：对初始检索结果进行 token 相似度 + 向量余弦相似度 + 标签加权 + PageRank 的四因子精排。

### 模块4：外部模型重排序 —— rerank_by_model()（L335-L356）

**作用**：用外部重排 API（Jina/CoHere/通义等16种）替代本地向量相似度计算。

### 模块5：检索精排全链路 —— retrieval()（L364-L521）

**作用**：将模块1-4串联为完整流程：search → 截断到 RERANK_LIMIT（64）→ rerank → 阈值过滤 → 分页 → 聚合。

---

## 三、方法详细解析

### 3.1 get_vector()（L52-L60）—— 向量查询构造

#### 文字流程串讲

方法接收用户问题文本 `txt`、Embedding 模型 `emb_mdl`、返回数量 `topk=10`、相似度阈值 `similarity=0.1`。核心操作是 `qv, _ = await thread_pool_exec(emb_mdl.encode_queries, txt)`——在独立线程池中调用 Embedding 模型的 `encode_queries` 方法，将文本转为浮点数向量数组。`thread_pool_exec` 的作用是避免 Embedding API 的 HTTP 请求阻塞 asyncio 事件循环。

接着检查向量维度：`np.array(qv).shape` 必须是一维的（单个问题恰好一个向量），多维会抛异常。从向量维度动态生成列名 `f"q_{len(embedding_data)}_vec"`——如 768 维生成 `q_768_vec`，1024 维生成 `q_1024_vec`。这是为了支持同一知识库中混存不同维度的向量（不同 Embedding 模型产出不同维度）。

最后构造并返回 `MatchDenseExpr`，参数包括向量列名、embedding 数据、距离度量方式（cosine）、返回数量和相似度阈值。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `txt`（str，用户问题）、`emb_mdl`（LLMBundle，Embedding 模型实例）、`topk`（int，默认10）、`similarity`（float，默认0.1） |
| **核心逻辑** | thread_pool_exec 调用 emb_mdl.encode_queries → 维度校验 → 动态生成列名 → 构造 MatchDenseExpr |
| **输出形式** | `MatchDenseExpr`（含列名、向量、cosine、topk、similarity） |
| **底层关键依赖** | `thread_pool_exec()`、`emb_mdl.encode_queries()`（HTTP API 调用） |
| **关键代码片段** | `qv, _ = await thread_pool_exec(emb_mdl.encode_queries, txt)` |

#### 特殊处理标注
- **线程池隔离**：Embedding API 是同步 HTTP 调用，通过 `thread_pool_exec` 放到独立线程避免阻塞事件循环
- **动态列名**：`q_{维度}_vec` 支持混合维度

### 3.2 search()（L74-L171）—— 混合检索主入口

#### 文字流程串讲

方法入口先通过 `get_filters(req)` 构造过滤器条件——将 `kb_ids` 映射为 `kb_id` 过滤、`doc_ids` 映射为 `doc_id` 过滤，外加知识图谱相关字段过滤。然后解析分页参数：`pg = int(req.get("page",1))-1`、`ps = int(req.get("size", topk))`。

接着定义返回字段 `src`——包含 docnm_kwd（文档名）、content_ltks（内容 token）、kb_id、img_id、title_tks、position_int、page_num_int、doc_id 等 20 个字段，以及 PAGERANK_FLD 和 TAG_FLD。

**三分支路由**：

**分支1-无问题浏览模式**（`if not qst`）：用户没有输入问题，直接按 doc_ids 获取指定文档的全部 chunks，按 page_num_int → top_int → create_timestamp_flt 排序返回。这是用于"查看文档内容"的浏览场景。

**分支2-纯全文检索**（`elif emb_mdl is None`）：没有配置 Embedding 模型时，只用 `self.qryr.question(qst, min_match=0.3)` 构造的 `MatchTextExpr` 做全文搜索。不涉及向量。

**分支3-混合检索**（`else`，正常路径）：
1. 构造全文 `MatchTextExpr`（L115）
2. 调用 `get_vector()` 构造 `MatchDenseExpr`（L122）
3. 构造 `FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})`（L127）——全文 5%、向量 95%
4. 将 `[matchText, matchDense, fusionExpr]` 提交给 `self.dataStore.search()`
5. **降级逻辑**（L136-L146）：`total == 0` 时——如果有 doc_ids，去掉所有检索条件直接返回；否则降低阈值（min_match=0.1，similarity=0.17）重试

最后收集关键词和高亮结果，返回 `SearchResult` dataclass。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `req`（dict，含 question/kb_ids/page/topk 等）、`idx_names`、`kb_ids`、`emb_mdl`（可选）、`highlight`、`rank_feature` |
| **核心逻辑** | 构造过滤器→解析分页→构造查询表达式→三分支→降级重试→收集结果 |
| **输出形式** | `SearchResult`（total, ids, query_vector, field, highlight, aggregation, keywords） |
| **底层关键依赖** | `self.qryr.question()`、`self.get_vector()`、`self.dataStore.search()` |
| **关键代码片段** | `FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})` |

#### 特殊处理标注
- **three-way routing**：无问题/无嵌入/混合，覆盖全部使用场景
- **降级三阶段**：正常(0.3/0.1)→降级(0.1/0.17)→兜底(无条件)

### 3.3 rerank()（L270-L333）—— 本地多因子重排序

#### 文字流程串讲

方法接收 search 结果 `sres`、查询文本 `query`、词权重系数 `tkweight=0.3`、向量权重系数 `vtweight=0.7`。

**步骤1-构建词权重矩阵**：对每个检索到的文档 chunk，从 `sres.field` 中提取 content_ltks、title_tks、question_tks、important_kwd，按不同加权倍数合并成一个 token 列表（`content_ltks + title_tks*2 + important_kwd*5 + question_tks*6`）。

**步骤2-计算相似度**：调用 `self.qryr.hybrid_similarity(sres.query_vector, ins_embd, keywords, ins_tw, tkweight, vtweight)` 计算每个 chunk 与查询问题的混合相似度。内部公式是 `tkweight * token_sim + vtweight * vector_cos`。

**步骤3-标签加分**：调用 `self._rank_feature_scores(rank_feature, sres)` 计算标签特征分数——对每个 chunk 检查其 TAG_FLD 字段中的标签是否与 rank_feature 中的标签匹配，匹配则加分。同时计算 PageRank 分数。

**最终分数**：`sim = 0.3 * tksim + 0.7 * vtsim + rank_fea + pagerank`，返回 `(sim, tksim, vtsim)` 三元组。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `sres`（SearchResult）、`query`（str）、`tkweight=0.3`、`vtweight=0.7`、`cfield="content_ltks"`、`rank_feature` |
| **核心逻辑** | 构建权重矩阵→hybrid_similarity计算→rank_feature加分→融合四因子 |
| **输出形式** | `(sim: np.array, tksim: list, vtsim: list)` 三元组 |
| **底层关键依赖** | `self.qryr.hybrid_similarity()`、`self._rank_feature_scores()` |
| **关键代码片段** | `tks = content_ltks + title_tks * 2 + important_kwd * 5 + question_tks * 6` |

#### 特殊处理标注
- **加权系数**：标题×2、关键词×5、问题×6——反映不同字段对语义匹配的贡献度

### 3.4 retrieval()（L364-L521）—— 检索精排全链路

#### 文字流程串讲

这是 `Dealer` 对外暴露的主要接口，被 `async_chat()` 直接调用。它将 `search()` 和 `rerank()` 串联成一个完整流程。

**步骤1-粗排召回**：构造 `req` 字典（page、size=RERANK_LIMIT=64、topk=1024），调用 `self.search()` 进行混合检索。注意这里 RERANK_LIMIT=64 意味着从 1024 个粗排结果中取前 64 个进入精排流水线。

**步骤2-精排**：如果有重排模型（`rerank_mdl`），走 `rerank_by_model()` 用外部 API 计算语义相似度；否则判断引擎类型——Infinity 引擎已经在融合时做了归一化，直接用 `_score` 字段；ES 引擎需要本地调用 `rerank()` 重算。

**步骤3-阈值过滤**：`sorted_idx = np.argsort(sim * -1)` 按分数降序排列。`post_threshold` 在两个条件下为 0：vector_similarity_weight <= 0（只用全文不做向量阈值过滤）或指定了 doc_ids（用户明确要这些文档，不管分数）。

**步骤4-分页截取**：`max_pages = max(RERANK_LIMIT // max(page_size,1), 1)` 计算总页数，`page_index = (page-1) % max_pages` 取当前页。

**步骤5-构建结果**：遍历分页后的 chunks，构建包含 chunk_id、content_with_weight、doc_id、docnm_kwd、kb_id、similarity、vector_similarity、term_similarity、vector、positions 的字典。

**步骤6-聚合**：按 docnm_kwd 聚合，统计每个文档被检索到的 chunk 数量，按 count 降序排列。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `question`、`embd_mdl`、`tenant_ids`、`kb_ids`、`page`、`page_size`、`similarity_threshold=0.2`、`vector_similarity_weight=0.3`、`top=1024`、`doc_ids`、`aggs=True`、`rerank_mdl` |
| **核心逻辑** | search粗排(1024)→截断(64)→rerank精排→阈值过滤→分页→聚合 |
| **输出形式** | `{"total": N, "chunks": [...], "doc_aggs": {...}}` |
| **底层关键依赖** | `self.search()`、`self.rerank()` / `self.rerank_by_model()` |
| **关键代码片段** | `RERANK_LIMIT = math.ceil(64 / page_size) * page_size if page_size > 1 else 1` |

#### 特殊处理标注
- **粗排→精排**：1024→64 的两阶段架构，平衡效率和精度
- **引擎感知**：Infinity 引擎跳过本地rerank（已归一化），ES 引擎需要

---

## 四、同类逻辑对比表

| 功能名称 | 核心流程 | 入参 | 底层依赖 API | 输出格式 | 差异场景 |
|----------|----------|------|-------------|---------|---------|
| `search()` | 三步路由+融合+降级 | req, idx_names, kb_ids, embd_mdl | FulltextQueryer + Embedding + ES/Infinity | SearchResult | 原始检索，无精排 |
| `retrieval()` | search→截断→rerank→过滤→聚合 | question, embd_mdl, kb_ids... | search() + rerank() | `{chunks, doc_aggs}` | 完整精排链路 |
| `rerank()` | 本地多因子重排 | sres, query, tkweight, vtweight | hybrid_similarity + rank_feature | (sim, tksim, vtsim) | ES 引擎或 Infinity 降级 |
| `rerank_by_model()` | 外部模型重排 | rerank_mdl, sres, query | external API (Jina/CoHere...) | (sim, tksim, vtsim) | 有外部重排模型时 |

---

## 五、疑惑解答

**Q：为什么全文权重仅5%、向量权重95%？**

在 RAG 场景中，语义理解远比关键词匹配重要。用户问"怎么配置系统"，知识库写的是"系统设置指南"——关键词不匹配但语义相同。如果全文权重过高，这种同义表达就会被漏掉。5% 的全文权重主要作为"精确匹配兜底"——比如用户搜索专有名词"SKU-12345"时，全文检索能精确命中。

**Q：为什么粗排取1024，精排只保留64？**

这是信息检索领域的经典二阶段架构。粗排用高效的向量索引（HNSW）快速召回大量候选，精排用更复杂但更慢的计算（向量余弦+词权重+标签分）对候选做精细排序。1024→64 的比例平衡了召回率和计算开销。

---

## 六、规范修正

- "加权求和融合"统一为 `weighted_sum` 
- "重排序"和"精排"指同一概念，本文统一使用"精排"
- "粗排"指 search() 阶段的初始召回

---

## 七、可复现实操步骤

| 步骤 | 操作内容 | 最简代码 | 注意事项 |
|------|----------|---------|---------|
| 1 | 创建 Dealer | `from rag.nlp.search import Dealer; d = Dealer(es_conn)` | 需要 ES/Infinity 连接 |
| 2 | 执行混合检索 | `sres = await d.search(req, idx_names, kb_ids, embd_mdl)` | req 需含 question/kb_ids |
| 3 | 执行精排 | `ranks = await d.retrieval(question, embd_mdl, tids, kb_ids, 1, 10)` | 自动走 rerank |
| 4 | 获取引用 | `ans, cited = d.insert_citations(answer, chunks, chunk_v, embd_mdl)` | 在答案中插入 [ID:n] |

---

## 八、关键模块总览

| 模块名称 | 负责功能 | 在流程中的核心作用 |
|----------|----------|-------------------|
| `Dealer.qryr` (FulltextQueryer) | 全文查询构造 | 自然语言→ES/Infinity 查询表达式 |
| `Dealer.dataStore` | 向量数据库抽象 | ES/Infinity/OB 统一接口 |
| `Dealer.get_vector()` | Embedding 调用 | 文本→向量数组 |
| `Dealer.search()` | 混合检索调度 | 全文+向量融合+降级 |
| `Dealer.rerank()` | 本地精排 | 四因子多维度排序 |
| `Dealer.rerank_by_model()` | 外部精排 | 调用 16 种重排 API |
| `Dealer.retrieval()` | 精排全链路 | 粗排→精排→过滤→聚合 |
| `FusionExpr` | 融合表达式 | weighted_sum 加权求和多路融合 |
| `MatchTextExpr` | 全文表达式 | ES match / Infinity MATCH TEXT |
| `MatchDenseExpr` | 向量表达式 | ES knn / Infinity KNN |
