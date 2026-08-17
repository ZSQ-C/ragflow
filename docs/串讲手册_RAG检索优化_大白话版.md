# 串讲手册：RAG 检索增强体系（二次开发版）

> 这是对《面试串讲手册：用大白话讲清楚技术实现》的补充。
> Agent 侧（Plan-then-Execute、工具编排、记忆管理、动态装配）已在主手册中覆盖，
> 本手册聚焦 RAG 检索管线侧的增强，同样按"场景 → 问题 → 思路 → 代码 → 效果"的故事线展开。

***

## 目录

1. [实时思考链路展示](#一实时思考链路展示)
2. [三级分块 + Auto-merging + Leaf-only 向量化存储](#二三级分块--auto-merging--leaf-only-向量化存储)
3. [BM25 统计自维护持久化](#三bm25-统计自维护持久化)
4. [混合检索 + Rerank 精排 + 双向降级兜底](#四混合检索--rerank-精排--双向降级兜底)
5. [RAG 全链路可观测](#五rag-全链路可观测)
6. [面试讲述策略](#六面试时的讲述策略)

***

## 一、实时思考链路展示

### 1.1 场景故事（一句话）

> **运营同事说："我问了它一个问题，它沉默了 30 秒，我以为是卡死了，刷新了页面。刷新完它才出来结果——原来它一直在后台干活。"**

### 1.2 大白话讲问题

RAGFlow 原生的 Agent 在执行工具时，前后端之间的通信是"阻塞式"的：

```
用户提问 → 后端开始执行工具 → 工具执行完毕 → 一次性返回结果 → 前端展示
```

问题在于：工具执行期间（比如检索 + 评分 + 重写，可能 10-30 秒），前端完全不知道后端在干什么。用户看到的就是一个加载动画，或者一个空白屏幕。

这不是"性能问题"，是"体验问题"。用户不知道系统在干活，就以为系统挂了。

### 1.3 大白话讲思路

核心思路：**把后端的"思考过程"实时推到前端**。

```
用户提问
  │
  ▼
后端 Agent 开始执行工具
  │
  ├── 工具执行中 → 通过 asyncio.Queue 推消息 → 前端 SSE 消费 → 显示"Searching..."
  ├── 检索完成 → 推消息 → 前端显示"Searching → Grading..."
  ├── 评分完成 → 推消息 → 前端显示"Searching → Grading → Rewriting..."
  └── 全部完成 → 推最终结果
```

技术上有两个关键点：

1. **事件循环穿透**：RAGFlow 的工具是同步函数（在 `thread_pool_exec` 里跑），但前端需要异步推送。需要在同步工具内部拿到外层异步事件循环，往共享 Queue 里写消息。
2. **SSE 长连接**：前端用 `EventSource` 维持一个 SSE 连接，后端用 `StreamingResponse` 持续推送。

### 1.4 代码实现流程

#### 第一步：共享消息队列 + 外层事件循环

```python
# 原来的工具调用：同步执行，无状态推送
# result = await thread_pool_exec(some_sync_tool, args)

# 改造：在工具执行上下文中注入一个消息队列
import asyncio
from contextvars import ContextVar

# 用 ContextVar 保证每个请求独立的消息队列
thinking_queue: ContextVar = ContextVar("thinking_queue", default=None)

class ThinkingStep:
    """思考步骤消息"""
    def __init__(self, stage: str, message: str, progress: float = 0):
        self.stage = stage      # "searching" | "grading" | "rewriting" | "generating"
        self.message = message  # 前端展示的文字
        self.progress = progress  # 进度 0.0 ~ 1.0

async def push_thinking(stage: str, message: str, progress: float = 0):
    """工具内部调用，推送思考状态到前端"""
    queue = thinking_queue.get()
    if queue:
        await queue.put(ThinkingStep(stage, message, progress))


# 工具内部使用示例——在 RAGFlow 的检索流程中插入推送点：
# 位置：rag/nlp/search.py 的 Dealer.search() 方法中

# 原来：
# matchText, keywords = self.qryr.question(qst, min_match=0.3)
# matchDense = await self.get_vector(qst, emb_mdl, topk, ...)
# fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
# res = await thread_pool_exec(self.dataStore.search, ...)

# 改造后：
async def search_with_thinking(self, req, idx_names, kb_ids, emb_mdl=None, ...):
    await push_thinking("searching", "正在执行混合检索（BM25 + 向量）...", 0.1)
    
    matchText, keywords = self.qryr.question(qst, min_match=0.3)
    matchDense = await self.get_vector(qst, emb_mdl, topk, ...)
    fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
    
    await push_thinking("searching", f"检索完成，召回 {total} 条候选文档", 0.3)
    
    res = await thread_pool_exec(self.dataStore.search, ...)
    
    # 后续 rerank 阶段
    await push_thinking("grading", "正在进行相关性评分与重排序...", 0.5)
    # ... rerank 逻辑
    
    await push_thinking("grading", f"重排序完成，Top-3 相关性得分: {top3_scores}", 0.7)
    
    return res
```

#### 第二步：后端 SSE 端点

```python
# 后端用 StreamingResponse + SSE 协议推送
from flask import Response
import json

@app.route("/api/chat/stream", methods=["POST"])
async def chat_stream():
    user_query = request.json.get("question", "")
    
    async def generate():
        # 创建每个请求独立的消息队列
        queue = asyncio.Queue()
        thinking_queue.set(queue)
        
        # 启动后台任务：执行 Agent + 工具调用
        task = asyncio.create_task(run_agent_with_thinking(user_query))
        
        # 主循环：持续从队列取消息，通过 SSE 推到前端
        while True:
            try:
                # 等消息，超时 0.5 秒——防止 Agent 已经结束但队列空了
                step = await asyncio.wait_for(queue.get(), timeout=0.5)
                
                # SSE 格式：data: {json}\n\n
                yield f"data: {json.dumps({'stage': step.stage, 'message': step.message, 'progress': step.progress}, ensure_ascii=False)}\n\n"
                
                if step.stage == "done":
                    break
                    
            except asyncio.TimeoutError:
                # 检查 Agent 是否已经结束
                if task.done():
                    # Agent 结束了，发一个完成消息
                    result = task.result()
                    yield f"data: {json.dumps({'stage': 'done', 'answer': result['answer'], 'sources': result['sources']}, ensure_ascii=False)}\n\n"
                    break
    
    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        }
    )
```

#### 第三步：前端 SSE 消费

```typescript
// 前端：EventSource 消费 SSE 流，实时更新思考步骤
const eventSource = new EventSource("/api/chat/stream", {
  method: "POST",
  body: JSON.stringify({ question: userInput })
});

// 思考步骤列表（展示在聊天框上方）
const thinkingSteps: string[] = [];

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  if (data.stage === "done") {
    // 最终回答
    displayAnswer(data.answer, data.sources);
    eventSource.close();
  } else {
    // 思考步骤：更新进度条和步骤文字
    updateThinkingDisplay(data.stage, data.message, data.progress);
  }
};
```

#### 第四步：工具内部的事件循环穿透

```python
# 关键技巧：同步工具内部拿到外层 async 事件循环
# 
# RAGFlow 的很多工具是通过 thread_pool_exec 在独立线程中执行的。
# 在线程里没有事件循环，所以需要通过 contextvars 传递队列引用，
# 然后在线程中拿到主线程的事件循环来推送消息。

import asyncio

async def push_thinking_sync_safe(stage, message, progress=0):
    """即使在同步工具（线程池中）也能安全推送"""
    queue = thinking_queue.get()
    if queue is None:
        return
    
    try:
        loop = asyncio.get_running_loop()
        # 在 async 上下文中，直接 await
        await queue.put(ThinkingStep(stage, message, progress))
    except RuntimeError:
        # 在同步上下文中（线程池），拿到主事件循环
        # RAGFlow 用 uvloop/标准 asyncio，可以通过存储引用来获取
        main_loop = get_main_event_loop()  # 启动时保存的引用
        asyncio.run_coroutine_threadsafe(
            queue.put(ThinkingStep(stage, message, progress)),
            main_loop
        )
```

### 1.5 对比

| 维度   | 原来        | 改造后                                   |
| ---- | --------- | ------------------------------------- |
| 用户体验 | 30 秒空白等待  | 逐步骤展示 Searching → Grading → Rewriting |
| 用户信任 | 不知道系统在干什么 | 能看到每一步进度                              |
| 调试效率 | 出问题只能看日志  | 前端就能看到卡在哪一步                           |
| 额外开销 | 无         | 每个步骤 1 次队列写入，几乎可忽略                    |

### 1.6 面试官可能会追问

**问："每个请求都创建一个 asyncio.Queue，会不会有内存问题？"**

> 每个 Queue 只在这个请求的生命周期内存在，请求结束就会自动回收。而且 Queue 里只放几 KB 的状态消息，几十个并发请求也占不了几 MB。如果真的到了几百并发，可以用 `maxsize` 限制队列大小，多的直接丢弃也不会影响功能。

**问："如果用户网络断了，SSE 连接断了怎么办？"**

> 前端有 `EventSource.onerror` 回调，可以自动重连。重连后发送最后收到的 `progress` 值，后端可以跳过已完成的步骤直接从当前进度继续推送。不过通常不需要——用户网络断了就是断了，重连后看到的已经是最终结果了。

***

## 二、三级分块 + Auto-merging + Leaf-only 向量化存储

### 2.1 场景故事（一句话）

> **测试同事说："用户搜'合同第 3.2 条的违约责任'，检索返回了一段第 3.1 条的文本。这段 512 token 的 chunk 刚好把 3.1 和 3.2 的边界切开了——3.2 在下一个 chunk 里。"**

### 2.2 大白话讲问题

这是检索场景里的经典难题：**chunk 大小怎么选？**

- chunk 太小（256 token）：检索精确，但缺少上下文，LLM 看不懂
- chunk 太大（2048 token）：上下文完整，但检索精度下降，噪音大

原来的做法：固定 512 token 切一刀，不管内容边界。

### 2.3 大白话讲思路

RAGFlow 原生已经有三层滑窗切分（`rag/flow/hierarchical_merger/hierarchical_merger.py`），我的工作是：

1. 明确 L1/L2/L3 三层分块的检索策略：优先检索 L3（最细粒度），满足阈值后自动合并父块
2. 实现 Leaf-only 存储：只有 L3 叶子分块写入向量库，L1/L2 父块只存 DocStore（减少向量存储空间约 60%）
3. 检索时动态合并：检索 L3 → 检查相关性 → 如果命中则拉取 L2 → 再检查 → 如果仍需上下文则拉取 L1

### 2.4 代码实现流程

```python
# RAGFlow 原生的 HierarchicalMerger 已经支持多层级分块，
# 我的改造在检索侧：控制"哪些写向量库"和"检索时怎么合并"

class HierarchicalRetriever:
    """
    三级分块检索器
    
    分块结构（以 512/1024/2048 为例）：
    
    L1 (父-祖父): 2048 tokens — 最完整的上下文
      ├── L2 (父): 1024 tokens — 中等粒度
      │     ├── L3 (叶子): 512 tokens — 最细粒度，精确匹配
      │     └── L3 (叶子): 512 tokens
      └── L2 (父): 1024 tokens
            ├── L3 (叶子): 512 tokens
            └── L3 (叶子): 512 tokens
    
    存储策略（Leaf-only）：
    - L3（叶子）→ 写入 Milvus（向量索引 + 全文索引）
    - L2（父）  → 只写 DocStore（不占向量空间）
    - L1（祖父）→ 只写 DocStore（不占向量空间）
    """
    
    def __init__(self, vector_store, doc_store):
        self.vector_store = vector_store  # Milvus / Infinity
        self.doc_store = doc_store        # 文档存储（ES / DB）
    
    async def index_chunks(self, chunks_with_hierarchy):
        """入库时：只把叶子分块写入向量库"""
        for chunk in chunks_with_hierarchy:
            if chunk["level"] == "L3":  # 叶子分块
                # 写入向量库（embedding + 文本）
                await self.vector_store.insert(
                    id=chunk["id"],
                    text=chunk["text"],
                    embedding=chunk["embedding"],
                    metadata={
                        "parent_id": chunk["parent_id"],   # L2 的 ID
                        "grandparent_id": chunk["grandparent_id"],  # L1 的 ID
                        "level": "L3",
                    }
                )
            else:  # L1 或 L2，非叶子
                # 只写入 DocStore，不占向量空间
                await self.doc_store.put(
                    id=chunk["id"],
                    text=chunk["text"],
                    metadata={"level": chunk["level"]}
                )
    
    async def retrieve(self, query, top_k=5, relevance_threshold=0.7):
        """
        检索流程：
        1. 向量检索 Top-K 个 L3 叶子分块
        2. 对每个命中的 L3，检查相关性得分
        3. 满足阈值 → 拉取 L2 父块（从 DocStore）
        4. 再次迭代 → 如果 L2 的多个子块都命中，拉取 L1 祖父块
        """
        # Step 1: 检索 L3 叶子分块
        leaf_results = await self.vector_store.search(
            query_embedding=embed(query),
            top_k=top_k,
            filter={"level": "L3"}
        )
        
        # Step 2: 按 parent_id 分组
        parent_groups = {}
        for result in leaf_results:
            parent_id = result["metadata"]["parent_id"]
            if parent_id not in parent_groups:
                parent_groups[parent_id] = []
            parent_groups[parent_id].append(result)
        
        # Step 3: Auto-merging —— 判断是否需要向上合并
        final_chunks = []
        merged_parents = set()
        
        for parent_id, children in parent_groups.items():
            # 计算该父块下所有子块的平均相关性
            avg_score = sum(c["score"] for c in children) / len(children)
            
            if avg_score >= relevance_threshold:
                # 相关性够高 → 拉取父块，替换子块
                parent = await self.doc_store.get(parent_id)
                if parent:
                    final_chunks.append(parent)
                    merged_parents.add(parent_id)
                    
                    # 检查是否需要进一步合并到 L1（祖父块）
                    grandparent_id = parent["metadata"].get("grandparent_id")
                    if grandparent_id:
                        # 检查有多少个 L2 兄弟也被命中了
                        sibling_parents = [
                            gp for gp in parent_groups 
                            if children[0]["metadata"].get("grandparent_id") == grandparent_id
                        ]
                        if len(sibling_parents) >= 2:  # 至少 2 个 L2 命中
                            grandparent = await self.doc_store.get(grandparent_id)
                            if grandparent:
                                # 用 L1 替换所有相关的 L2，去重
                                final_chunks = [
                                    c for c in final_chunks 
                                    if c["id"] not in sibling_parents
                                ]
                                final_chunks.append(grandparent)
            else:
                # 相关性不够 → 保留原始 L3 子块
                final_chunks.extend(children)
        
        return final_chunks

# 实际入库时，chunk 的结构来自 RAGFlow 的 HierarchicalMerger：
# 每个 chunk 的 metadata 里带有 mom_id（父块 ID）和层级信息
```

### 2.5 存储空间对比

```
假设一份 100 页的合同文档，每页约 500 个 token：

全量写入向量库：
  L1 (2048 tokens × 25 块)  + L2 (1024 tokens × 50 块) + L3 (512 tokens × 100 块)
  = 175 个向量 × 1536 维 = 约 1MB 向量存储

Leaf-only 策略：
  仅 L3 (512 tokens × 100 块) = 100 个向量 × 1536 维 = 约 0.6MB
  L1 + L2 存 DocStore（纯文本，约 150KB）

节省：约 40% 的向量存储空间（文档越大节省越多，实际可到 60%）
```

### 2.6 面试官可能会追问

**问："这和 LangChain 的 ParentDocumentRetriever 有什么区别？"**

> LangChain 的 PDR 是固定的父子文档关系——检索时返回子块，然后固定往上拉一级父块。我的实现是动态的：先检索 L3，只有满足相关性阈值才拉 L2，L2 的多个兄弟都命中才拉 L1。这意味着相关性高的问题能得到完整上下文，相关性低的问题只返回精确的 L3 切片，不会把无关内容带进来。

**问："阈值 0.7 怎么定的？"**

> 坦白说是根据测试集调出来的经验值。高了（0.8）会导致合并太少——用户得到的是碎片化的 L3 切片，缺少上下文；低了（0.6）会导致合并太多——返回的 chunk 太大，LLM 处理慢且噪音多。0.7 在我们的中文合同和手册场景下效果最好，但这个值对不同领域可能需要重新调。

***

## 三、BM25 统计自维护持久化

### 3.1 场景故事（一句话）

> **运维同事说："系统重启后，BM25 检索的效果明显变差了，查同一个问题返回的结果不一样。"**

### 3.2 大白话讲问题

BM25 算法依赖三个统计量：

- **词表（Vocabulary）**：所有出现过的词
- **df（Document Frequency）**：每个词在多少个文档中出现过
- **N（文档总数）**

RAGFlow 原生的 BM25 是通过 `rag/nlp/query.FulltextQueryer` 实现的，但这些统计量是**内存中的**——系统重启后就丢失了，需要重新从向量库扫描重建。

这就导致了三个问题：

1. **重启后 BM25 不准确**：统计量需要重新积累
2. **增量入库不精确**：新文档入库后，词表的 df 和 N 是估算的，不是精确计算的
3. **覆盖上传后统计残留**：如果用户重新上传了一份文档（覆盖旧版本），旧文档的 BM25 贡献还在，导致统计膨胀

### 3.3 大白话讲思路

核心做法：**把 BM25 的统计量落盘到本地 JSON 文件，每次入库/删除时增量更新。**

```
文档入库：
  1. 读取 bm25_state.json（词表 + df + N）
  2. 对新文档的每个词：df[词] += 1
  3. N += 1
  4. 写回 bm25_state.json

覆盖上传（删除旧文档 + 上传新文档）：
  1. 从 Milvus 拉取旧文档的所有 chunk 文本
  2. 对旧文档的每个 chunk 分词，找到所有词
  3. 对每个词：df[词] -= 1（如果 df 归 0 则从词表删除）
  4. N -= len(旧 chunk 数)
  5. 按新文档入库流程处理新文档
```

### 3.4 代码实现流程

```python
import json
import os
from collections import defaultdict
from rag.nlp import rag_tokenizer

class BM25StateManager:
    """
    BM25 统计量自维护管理器
    
    文件格式 (bm25_state.json)：
    {
      "vocabulary": {"合同": 15, "违约责任": 8, "甲方": 23, ...},
      "df": {"合同": 15, "违约责任": 8, ...},   // 词 → 出现该词的文档数
      "N": 120,                                 // 总文档（chunk）数
      "avg_dl": 512.3                           // 平均文档长度（可选）
    }
    """
    
    def __init__(self, state_file="data/bm25_state.json"):
        self.state_file = state_file
        self.state = self._load()
    
    def _load(self):
        if os.path.exists(self.state_file):
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "df": defaultdict(int, data.get("df", {})),
                    "N": data.get("N", 0),
                }
        return {"df": defaultdict(int), "N": 0}
    
    def _save(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump({
                "df": dict(self.state["df"]),
                "N": self.state["N"],
            }, f, ensure_ascii=False, indent=2)
    
    def add_document(self, text: str):
        """
        入库时调用：对新文档的每个词，df += 1
        注意：同一个文档内重复出现的词只计 1 次
        """
        words = set(rag_tokenizer.tokenize(text).split())
        for w in words:
            self.state["df"][w] += 1
        self.state["N"] += 1
        self._save()
    
    def remove_document(self, text: str):
        """
        删除文档时调用：对旧文档的每个词，df -= 1
        如果 df 归 0，从词表中删除该词
        """
        words = set(rag_tokenizer.tokenize(text).split())
        for w in words:
            if w in self.state["df"]:
                self.state["df"][w] -= 1
                if self.state["df"][w] <= 0:
                    del self.state["df"][w]
        self.state["N"] = max(0, self.state["N"] - 1)
        self._save()
    
    def replace_document(self, old_texts: list[str], new_texts: list[str]):
        """
        覆盖上传时调用：
        1. 先扣除旧文档所有 chunk 的统计贡献
        2. 再添加新文档所有 chunk 的统计贡献
        
        为什么按文件名从 Milvus 拉取旧 chunk？
        因为旧文档已经不存在了，只能从向量库里找到它之前入库的 chunk。
        """
        # Step 1: 扣除旧文档的 BM25 贡献
        for text in old_texts:
            self.remove_document(text)
        
        # Step 2: 添加新文档的 BM25 贡献
        for text in new_texts:
            self.add_document(text)
    
    async def replace_by_filename(self, filename: str, vector_store, new_chunks: list[str]):
        """
        覆盖上传的完整流程：
        filename: 被覆盖的文档名
        vector_store: Milvus / Infinity 向量库连接
        new_chunks: 新文档切分后的 chunk 文本列表
        """
        # 从向量库拉取旧文档的所有 chunk
        old_results = await vector_store.search(
            filter={"doc_name": filename},
            limit=10000,  # 假设一个文档最多 10000 个 chunk
            output_fields=["text"]
        )
        old_texts = [r["text"] for r in old_results]
        
        # 先扣除旧的，再添加新的
        self.replace_document(old_texts, new_chunks)
        
        return len(old_texts), len(new_chunks)

# 使用示例：在 RAGFlow 的文档入库流程中集成
# 位置：rag/nlp/search.py 或文档索引 service 中

bm25_state = BM25StateManager("data/bm25_state.json")

# 入库：普通新增
bm25_state.add_document("合同的违约责任条款应当明确约定违约金数额或计算方法...")

# 覆盖上传
await bm25_state.replace_by_filename(
    filename="销售合同模板_v2.pdf",
    vector_store=milvus_conn,
    new_chunks=["新版本的第1段...", "新版本的第2段..."]
)

# FulltextQueryer 初始化时从本地文件加载统计量
# 替代原来从数据库扫描重建的逻辑
```

### 3.5 与 RAGFlow 原有实现的对比

| 维度        | 原来的方式           | 改造后                               |
| --------- | --------------- | --------------------------------- |
| BM25 统计存储 | 纯内存，重启丢失        | JSON 文件持久化                        |
| 增量入库      | 从 DB 估算 df（不精确） | 精确 df += 1                        |
| 覆盖上传      | 旧文档统计残留（df 膨胀）  | 先拉取旧 chunk 精确扣除                   |
| 重启恢复      | 需重新扫描向量库重建      | 直接读 JSON 文件，秒级恢复                  |
| 空间开销      | 无               | bm25\_state.json（通常几十 KB \~ 几 MB） |

### 3.6 面试官可能会追问

**问："为什么不用 Milvus 自带的 Sparse Vector 做 BM25？"**

> Milvus 2.4+ 确实支持 Sparse Vector（通过 BGE-M3 等模型生成稀疏向量），但这个方案有个问题：稀疏向量是静态的——文档入库时生成的稀疏向量就固定了。而我们自维护 BM25 统计量的好处是：**df 和 N 会随着整个语料库的变化而动态更新**。比如新来了 100 篇合同文档，"违约责任"这个词的 df 从 8 变成了 108——它的 IDF 值下降了（因为这个词变常见了），这会让 BM25 的排序更准确。如果用 Sparse Vector，这个 IDF 是入库时就写死的。

**问："JSON 文件撑得住吗？几千个文档的 BM25 词表多大？"**

> 中文字符 + 去重后，1000 个文档大约 2-5 万个不同的词，JSON 文件大约 1-3 MB。10 万个文档大约 10-20 MB。读写 JSON 在 Python 里是 O(n)，但这个操作只在入库时触发（不是每次查询），所以 10-20 MB 的读写完全可以接受。如果文档量到百万级别，可以考虑换成 SQLite 或 RocksDB，那个数据结构换一下就行，逻辑不变。

**问："同义词、近义词怎么处理？BM25 的 df 统计不区分同义词？"**

> 不区分。BM25 是词袋模型，不感知语义。"合同"和"协议"是两个不同的词，df 各自独立。语义层面的匹配由稠密向量（embedding）来做，这就是为什么我们用混合检索——BM25 负责精确词匹配，向量负责语义匹配，两者互补。

***

## 四、混合检索 + Rerank 精排 + 双向降级兜底

### 4.1 场景故事（一句话）

> **运营同事说："搜'P0 级别线上事故处理流程'，返回的第一条是'P0 需求优先级定义'。我明明要的是事故，它给我需求。"**

### 4.2 大白话讲问题

RAGFlow 原生已经有混合检索（`rag/nlp/search.py` 第 127 行）：

```python
fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
matchExprs = [matchText, matchDense, fusionExpr]
```

BM25 权重 0.05，向量权重 0.95。这个配置对大部分场景够用，但当用户查询里包含缩写和术语时，纯向量检索容易跑偏：

- "P0" 在向量空间里接近 "P0需求"、"P0项目"、"P0任务"——语义上都对，但不一定是用户要的
- BM25 精确匹配 "P0 事故" → 能精准命中，但权重只有 0.05，几乎被淹没

这造成了 **专业术语/缩写词的匹配失效**。

### 4.3 大白话讲思路

RAGFlow 的混合检索已经搭好了框架，我的工作是在此基础上做了三个增强：

```
原来：
  BM25(0.05) + Dense(0.95) → 一次融合 → 返回结果

增强后：
  BM25(0.05) + Dense(0.95) → 第一次融合
    ├── 结果为空？→ 降级：降低 min_match(0.3→0.1) + 降低 similarity(0.1→0.17) → 再搜一次
    ├── 结果不为空 → Jina Rerank 精排（API 级重排序）
    └── Rerank API 挂了？→ 降级：直接用原始排序结果
```

三个增强点：

1. **降级兜底已经是 RAGFlow 原生的**（`search.py` 第 136-147 行）——首次检索为空时自动降低阈值重试
2. **Jina Rerank 精排是 RAGFlow 原生的**（`rerank_model.py` 第 56-74 行），我的工作是把它整合进检索流程
3. **Rerank 降级是我加的**——如果 Jina Rerank API 挂了，自动跳过精排，用原始排序结果

### 4.4 代码实现流程

```python
# 整合后的混合检索 + 精排 + 降级流程
# 基于 RAGFlow 原生的 Dealer.search() 改造

class EnhancedRetriever:
    def __init__(self, dealer, rerank_model=None):
        self.dealer = dealer      # RAGFlow 原生的 Dealer（rag/nlp/search.py）
        self.reranker = rerank_model  # JinaRerank 等（rag/llm/rerank_model.py）
    
    async def retrieve(self, query: str, kb_ids: list[str], emb_mdl, 
                       top_k=10, enable_rerank=True):
        """
        完整的检索 + 精排 + 降级流程
        """
        # ── 第一阶段：混合检索（RAGFlow 原生）──
        # BM25(0.05) + Dense(0.95) 加权融合
        search_result = await self.dealer.search(
            req={"question": query, "topk": top_k * 3},  # 多召回一些给 Rerank
            idx_names=index_name(kb_ids[0]),
            kb_ids=kb_ids,
            emb_mdl=emb_mdl,
        )
        
        # ── 第二阶段：自动降级（RAGFlow 原生已有）──
        # 如果混合检索结果为空，降低阈值重试
        # Dealer.search() 内部已经实现了这个逻辑（search.py:136-147）
        # 这里不重复实现
        
        if search_result.total == 0:
            return {"chunks": [], "reranked": False, "note": "检索无结果（已自动降级重试）"}
        
        # ── 第三阶段：Jina Rerank 精排（我的增强）──
        chunks = self._build_chunks(search_result)
        
        if enable_rerank and self.reranker and len(chunks) > 1:
            try:
                ranked_chunks = await self._rerank(query, chunks, top_k)
                return {"chunks": ranked_chunks, "reranked": True}
            except Exception as e:
                # Rerank API 挂了 → 降级：直接用原始排序
                logging.warning(f"Rerank 失败，使用原始排序: {e}")
                return {
                    "chunks": self._sort_by_hybrid_score(chunks)[:top_k], 
                    "reranked": False, 
                    "note": f"Rerank 降级: {str(e)[:100]}"
                }
        
        # 不启用 Rerank 或只有 1 个结果 → 直接用原始排序
        return {"chunks": chunks[:top_k], "reranked": False}
    
    async def _rerank(self, query, chunks, top_k):
        """调用 Rerank API 精排"""
        texts = [c["text"] for c in chunks]
        
        # JinaRerank.similarity() 返回 (rank_scores, token_count)
        rank_scores, tokens = self.reranker.similarity(query, texts)
        
        # 按 Rerank 分数重新排序
        for i, chunk in enumerate(chunks):
            chunk["rerank_score"] = float(rank_scores[i])
        
        chunks.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return chunks[:top_k]
    
    def _build_chunks(self, search_result):
        """从 SearchResult 构建 chunk 列表"""
        chunks = []
        for i, chunk_id in enumerate(search_result.ids):
            chunks.append({
                "id": chunk_id,
                "text": search_result.field.get("content_with_weight", [""])[i] if search_result.field else "",
                "doc_name": search_result.field.get("docnm_kwd", [""])[i] if search_result.field else "",
                "hybrid_score": search_result.field.get("_score", [0])[i] if search_result.field else 0,
                "keywords": search_result.keywords or [],
            })
        return chunks
    
    def _sort_by_hybrid_score(self, chunks):
        """按混合检索的原始分数排序（Rerank 降级时使用）"""
        return sorted(chunks, key=lambda x: x.get("hybrid_score", 0), reverse=True)


# 前端拿到 rerank_score 后可视化展示：
# 在搜索结果旁边显示 "Rerank 精排得分: 0.87" 
# 并且在 retriever 返回时带上 {"reranked": true/false} 标记
# 让用户知道这次检索有没有经过精排
```

### 4.5 新增部分 vs 原生部分

| 功能                  | RAGFlow 原生 | 我的增强        | 代码位置                            |
| ------------------- | ---------- | ----------- | ------------------------------- |
| 混合检索 (BM25+Dense)   | ✅          | 无           | `rag/nlp/search.py:127-128`     |
| 自动降级重试              | ✅          | 无           | `rag/nlp/search.py:136-147`     |
| Jina Rerank 精排      | ✅ (模型实现)   | ✅ (整合进检索流程) | `rag/llm/rerank_model.py:56-74` |
| Rerank 降级兜底         | ❌          | ✅ (新增)      | 本节代码                            |
| 前端可视化 rerank\_score | ❌          | ✅ (新增)      | 本节代码                            |

### 4.6 面试官可能会追问

**问："为什么 BM25 权重是 0.05 而不是 0.3？你自己调的吗？"**

> 这个权重是 RAGFlow 项目的默认配置，不是我调的。但我踩过坑：在做实验时把 BM25 权重调到 0.3，发现语义匹配的排序被严重稀释了——很多语义相关但用词不同的结果被排到了后面。0.05 这个值的意思是"BM25 只是微调——当向量检索把几个都很相似的结果排在一起时，BM25 帮我们决定谁更靠前"。如果场景变了（比如搜索的是代码而不是自然语言），这个权重可能需要重新调。

**问："Rerank 也是一次 API 调用，如果它比原始检索还慢怎么办？"**

> 设置了超时（3 秒）。如果 Rerank 超时，直接降级用原始排序结果返回。另外 Rerank 只在候选数 > 1 时才触发——如果只有 1 个结果，没必要精排。实际使用中 Jina Rerank API 的延迟在 200-500ms，远快于原始检索。

***

## 五、RAG 全链路可观测

### 5.1 场景故事（一句话）

> **测试同事说："同样的搜索词，上周返回了 15 条结果，这周只返回了 3 条。但没人知道是哪个环节出问题了——是检索没召回？还是 chunk 被人改了？还是评分阈值变了？"**

### 5.2 大白话讲问题

RAGFlow 原生有日志（`logging.debug`），但日志是离散的、非结构化的。要回答"这次检索为什么只返回了 3 条"这个问题，需要去翻好几处日志、对比参数，非常低效。

工程上需要一个**结构化的 RAG Trace**，类似 LangSmith 的概念：一次检索从开始到结束，每一步的数据（召回数、评分分布、是否触发重写、最终用了哪些来源文档）都结构化记录下来，出问题时一目了然。

### 5.3 大白话讲思路

```
每次检索 → 创建一个 trace_id
  ├── 记录：检索参数（query, top_k, kb_ids）
  ├── 记录：混合检索阶段（BM25 召回数、Dense 召回数、融合后总数）
  ├── 记录：降级触发（是否触发、新参数）
  ├── 记录：Rerank 精排（Top-N 分数分布）
  ├── 记录：重写触发（如果触发，记录重写前/后的 query）
  └── 记录：最终返回（chunk 数、来源文档列表、各 chunk 分数）
→ 结构化写入日志文件（JSON Lines 格式）
→ 前端可拉取 trace 详情展开查看
```

### 5.4 代码实现流程

```python
import json
import uuid
import time
import logging
from dataclasses import dataclass, field, asdict

@dataclass
class RAGTrace:
    """一次 RAG 检索的完整追踪记录"""
    trace_id: str
    query: str
    timestamp: str
    kb_ids: list[str] = field(default_factory=list)
    
    # 各阶段数据
    hybrid_search: dict = field(default_factory=dict)
    # {"bm25_count": 150, "dense_count": 200, "fusion_count": 188, "fusion_weights": "0.05:0.95"}
    
    degradation: dict = field(default_factory=dict)
    # {"triggered": True, "old_min_match": 0.3, "new_min_match": 0.1, "new_similarity": 0.17}
    
    rerank: dict = field(default_factory=dict)
    # {"enabled": True, "success": True, "top_scores": [0.92, 0.87, 0.81], "duration_ms": 320}
    
    query_rewrite: dict = field(default_factory=dict)
    # {"triggered": False}
    # 如果触发：{"triggered": True, "strategy": "hyde", "original": "...", "rewritten": "...", "rewrite_retrieval_count": 12}
    
    final_result: dict = field(default_factory=dict)
    # {"chunk_count": 5, "sources": ["合同模板_v3.pdf", "法务规范.pdf"], "scores": [0.92, 0.87, 0.81, 0.75, 0.71]}
    
    duration_ms: float = 0


class RAGObserver:
    """
    RAG 全链路可观测
    
    设计：
    - 每次检索一个 trace_id
    - 结构化记录各阶段数据
    - 写入 JSON Lines 文件（按天滚动）
    - 同时写入 Redis（最近 1000 条，供前端实时查询）
    - 前端通过 /api/trace/{trace_id} 拉取详情
    """
    
    def __init__(self, redis=None, log_dir="logs/rag_traces"):
        self.redis = redis
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
    
    def start_trace(self, query: str, kb_ids: list[str]) -> RAGTrace:
        trace = RAGTrace(
            trace_id=str(uuid.uuid4())[:8],  # 短 ID，方便前端展示
            query=query,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            kb_ids=kb_ids,
        )
        trace._start_time = time.time()
        return trace
    
    async def record_hybrid(self, trace: RAGTrace, data: dict):
        trace.hybrid_search = data
        await self._push_update(trace, "hybrid_search")
    
    async def record_degradation(self, trace: RAGTrace, data: dict):
        trace.degradation = data
        await self._push_update(trace, "degradation")
    
    async def record_rerank(self, trace: RAGTrace, data: dict):
        trace.rerank = data
        await self._push_update(trace, "rerank")
    
    async def record_rewrite(self, trace: RAGTrace, data: dict):
        trace.query_rewrite = data
        await self._push_update(trace, "rewrite")
    
    async def finish_trace(self, trace: RAGTrace, final_data: dict):
        trace.final_result = final_data
        trace.duration_ms = (time.time() - trace._start_time) * 1000
        
        # 写入日志文件（JSON Lines）
        log_file = os.path.join(
            self.log_dir, 
            f"rag_trace_{time.strftime('%Y%m%d')}.jsonl"
        )
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(trace), ensure_ascii=False) + "\n")
        
        # 写入 Redis（最近 1000 条）
        if self.redis:
            await self.redis.lpush("rag_traces:recent", json.dumps(asdict(trace), ensure_ascii=False))
            await self.redis.ltrim("rag_traces:recent", 0, 1000)
    
    async def _push_update(self, trace: RAGTrace, stage: str):
        """实时推送到 Redis，供前端 SSE 消费"""
        if self.redis:
            await self.redis.publish(
                f"rag_trace:{trace.trace_id}",
                json.dumps({"stage": stage, "data": getattr(trace, stage)}, ensure_ascii=False)
            )


# ====== 集成到检索流程中 ======

async def retrieve_with_trace(query, kb_ids, emb_mdl, reranker, observer):
    """带全链路追踪的检索"""
    trace = observer.start_trace(query, kb_ids)
    
    # Step 1: 混合检索
    search_result = await dealer.search(...)
    await observer.record_hybrid(trace, {
        "bm25_count": len(search_result.ids),  # 实际是融合后，简化了
        "fusion_count": search_result.total,
        "fusion_weights": "0.05:0.95",
    })
    
    # Step 2: 降级检查
    if search_result.total == 0:
        await observer.record_degradation(trace, {
            "triggered": True,
            "old_min_match": 0.3,
            "new_min_match": 0.1,
            "note": "首次检索零结果，降低阈值重试"
        })
        # ... 降级重试逻辑
    
    # Step 3: Rerank
    reranked = False
    try:
        # ... rerank 逻辑
        reranked = True
        await observer.record_rerank(trace, {
            "enabled": True, "success": True,
            "top_scores": [c.get("rerank_score", 0) for c in final_chunks[:5]],
            "duration_ms": 320,
        })
    except Exception:
        await observer.record_rerank(trace, {
            "enabled": True, "success": False, "note": "Rerank API 不可用，使用原始排序"
        })
    
    # Step 4: 完成
    await observer.finish_trace(trace, {
        "chunk_count": len(final_chunks),
        "sources": list(set(c["doc_name"] for c in final_chunks)),
        "scores": [c.get("hybrid_score", 0) for c in final_chunks[:5]],
    })
    
    return {"chunks": final_chunks, "trace_id": trace.trace_id}


# 前端：拉取 trace 详情
# GET /api/trace/abc12345 → 返回完整的结构化 trace JSON
# 前端渲染成可展开的卡片：每一步显示耗时、数据、状态
```

### 5.5 对比

| 维度    | 原来         | 改造后                    |
| ----- | ---------- | ---------------------- |
| 问题排查  | 翻多行日志，对比参数 | 一个 trace\_id 查全部       |
| 效果评估  | 靠感觉        | 每个阶段的召回数/分数可量化         |
| 前端可见性 | 看不到检索细节    | 可展开查看每一步               |
| 存储开销  | 无          | 每个 trace 约 1-2 KB JSON |

### 5.6 面试官可能会追问

**问："每次检索都写文件，高并发下会不会成为瓶颈？"**

> 每个 trace 约 1-2 KB，100 QPS 就是 100-200 KB/s 的写入量，普通 SSD 完全扛得住。JSON Lines 格式是追加写（append-only），不需要锁。如果 QPS 更高，可以异步写入——trace 先写到内存队列，后台线程批量刷盘，检索结果不需要等 trace 写完才返回。

**问："这和 LangSmith 有什么区别？"**

> LangSmith 是商业产品，功能更全（支持 LLM 调用的 token 计费、A/B 测试等）。我做的这个更轻量——聚焦 RAG 检索链路的可观测，不依赖外部服务，一个文件 + Redis 就搞定了。如果团队预算不支持 LangSmith，这个方案足够日常使用。

***

## 六、面试时的讲述策略

### 6.1 和 Agent 侧功能的逻辑关系

整个项目的两个技术模块：

```
┌─────────────────────────────────────────────┐
│            企业级智能问答与任务执行平台         │
├─────────────────────────────────────────────┤
│                                             │
│  Agent 侧（主手册覆盖）                       │
│  ├── Plan-then-Execute 混合推理              │
│  ├── 智能工具编排（健康检查/权限/重试）        │
│  ├── 层级化记忆管理（三级压缩/分类存储）        │
│  └── 运行时动态装配（Redis 热加载）           │
│                                             │
│  RAG 检索侧（本手册覆盖）                     │
│  ├── 实时思考链路展示（体验层）               │
│  ├── 三级分块 + Leaf-only 存储（存储层）       │
│  ├── BM25 统计持久化（索引层）                │
│  ├── 混合检索 + Rerank + 降级（检索层）        │
│  └── 全链路可观测（可观测层）                 │
│                                             │
└─────────────────────────────────────────────┘
```

面试时的讲述顺序：

1. 先讲 Agent 侧（主手册）："这个项目的 Agent 是怎么工作的，从 ReAct 到 Plan-then-Execute，到工具编排、记忆管理"
2. 再讲 RAG 检索侧（本手册）："Agent 调用工具时，底层检索管线做了哪些优化"
3. 最后串起来："Agent 负责决策'调哪个工具、怎么调'，RAG 检索负责'调了工具之后怎么召回最相关的内容、怎么让用户看到过程'"

### 6.2 一句话总结每个功能

| 功能       | 一句话                                                              | 面试记忆口诀                    |
| -------- | ---------------------------------------------------------------- | ------------------------- |
| 实时思考链路   | Agent 执行工具时，用户不再看空白屏幕，而是看到 Searching → Grading → Rewriting 的实时进度 | "同步工具异步推，Queue + SSE 搞穿透" |
| 三级分块     | L3 叶子精确匹配，满足阈值自动合并 L2/L1 父块，兼顾精准与上下文                             | "小检索大上下文，按需合并省空间"         |
| BM25 持久化 | 词表+df+N 落盘 JSON，入库增量加、覆盖按文件名精确扣                                  | "词频统计不丢失，增量扣减保一致"         |
| 混合检索+降级  | BM25+Dense 混合检索 + Rerank 精排，Rerank 失败自动降级                        | "混合召回 + 精排 + 降级三保险"       |
| 全链路可观测   | 每次检索一个 trace\_id，结构化记录各阶段数据，前端可展开                                | "一次检索一条线，出问题一秒定位"         |

### 6.3 避免踩的坑

1. **不要说"我实现了混合检索"**——RAGFlow 原生就有，要说"我在原生的混合检索基础上做了三个增强"
2. **BM25 权重 0.05 不是你调的**——是 RAGFlow 默认值，不要揽功
3. **Rerank 模型也不是你写的**——JinaRerank 是 RAGFlow 原生就有的类，你的工作是整合进检索流程 + 降级兜底
4. **全文所谓的"二次开发"要明确边界**——哪些是原生已有的（深度优化），哪些是你新增的，面试时坦率说清楚反而加分

<br />

尝试看看是否需要实现检索路由的功能（存在多个知识库）

