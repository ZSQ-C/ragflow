# advanced_rag 模块分析报告

## 一、核心总览（带逻辑关系）

### 核心定位
`advanced_rag` 模块实现了**树结构查询分解检索（Tree-Structured Query Decomposition Retrieval）**，也称为 DeepResearcher。该模块通过递归分解复杂查询、多源检索、充分性检查的迭代流程，实现深度研究能力。核心解决的问题是：当单一检索无法满足复杂问题时，如何自动分解问题、多轮检索、逐步逼近答案。

### 整体流程串讲
执行链路从 `research()` 方法入口开始：首先发起初始检索请求 → 调用 `_retrieve_information()` 从知识库/网络/知识图谱获取信息 → 通过 `sufficiency_check()` 检查信息充分性 → 若不充分则调用 `multi_queries_gen()` 生成子问题 → 递归调用 `_research()` 处理子问题 → 最终合并所有检索结果。整个过程采用异步并发执行，支持回调进度通知。

---

## 二、模块拆分（固定顺序 + 关系说明）

### 1. 初始化模块
**作用**：初始化检索器实例，绑定 LLM 模型、配置参数、检索函数。
**位置**：整体流程的起点，为后续检索提供基础设施。
**配合关系**：被 `research()` 方法依赖，提供检索所需的模型和配置。

```python
class TreeStructuredQueryDecompositionRetrieval:
    def __init__(self,
                 chat_mdl: LLMBundle,
                 prompt_config: dict,
                 kb_retrieve: partial = None,
                 kg_retrieve: partial = None
                 ):
        self.chat_mdl = chat_mdl
        self.prompt_config = prompt_config
        self._kb_retrieve = kb_retrieve
        self._kg_retrieve = kg_retrieve
        self._lock = asyncio.Lock()
```

### 2. 核心入口方法模块
**作用**：对外暴露的研究入口，包装递归调用并添加生命周期标记。
**位置**：整体流程的入口点，协调递归检索过程。
**配合关系**：调用 `_research()` 执行实际递归逻辑，通过回调通知外部进度。

```python
async def research(self, chunk_info, question, query, depth=3, callback=None):
    if callback:
        await callback("<START_DEEP_RESEARCH>")
    await self._research(chunk_info, question, query, depth, callback)
    if callback:
        await callback("<END_DEEP_RESEARCH>")
```

### 3. 分支逻辑方法模块
**作用**：执行多源信息检索，整合知识库、网络、知识图谱三种数据源。
**位置**：在递归过程中被调用，负责实际的数据获取。
**配合关系**：被 `_research()` 调用，返回整合后的检索结果。

```python
async def _retrieve_information(self, search_query):
    kbinfos = []
    # 1. 知识库检索
    try:
        kbinfos = await self._kb_retrieve(question=search_query) if self._kb_retrieve else {"chunks": [], "doc_aggs": []}
    except Exception as e:
        logging.error(f"Knowledge base retrieval error: {e}")
    
    # 2. 网络检索（Tavily API）
    try:
        if self.prompt_config.get("tavily_api_key"):
            tav = Tavily(self.prompt_config["tavily_api_key"])
            tav_res = tav.retrieve_chunks(search_query)
            kbinfos["chunks"].extend(tav_res["chunks"])
            kbinfos["doc_aggs"].extend(tav_res["doc_aggs"])
    except Exception as e:
        logging.error(f"Web retrieval error: {e}")
    
    # 3. 知识图谱检索
    try:
        if self.prompt_config.get("use_kg") and self._kg_retrieve:
            ck = await self._kg_retrieve(question=search_query)
            if ck["content_with_weight"]:
                kbinfos["chunks"].insert(0, ck)
    except Exception as e:
        logging.error(f"Knowledge graph retrieval error: {e}")
    
    return kbinfos
```

### 4. 具体实现方法模块
**作用**：执行递归检索的核心逻辑，包括充分性检查和子问题生成。
**位置**：递归调用的核心，驱动整个深度研究流程。
**配合关系**：调用 `_retrieve_information()` 获取数据，调用 `sufficiency_check()` 和 `multi_queries_gen()` 进行判断和分解。

```python
async def _research(self, chunk_info, question, query, depth=3, callback=None):
    if depth == 0:
        return ""
    if callback:
        await callback(f"Searching by `{query}`...")
    
    st = timer()
    ret = await self._retrieve_information(query)
    if callback:
        await callback("Retrieval %d results in %.1fms"%(len(ret["chunks"]), (timer()-st)*1000))
    
    await self._async_update_chunk_info(chunk_info, ret)
    ret = kb_prompt(ret, self.chat_mdl.max_length*0.5)
    
    # 充分性检查
    if callback:
        await callback("Checking the sufficiency for retrieved information.")
    suff = await sufficiency_check(self.chat_mdl, question, ret)
    
    if suff["is_sufficient"]:
        if callback:
            await callback(f"Yes, the retrieved information is sufficient for '{question}'.")
        return ret
    
    # 生成子问题
    succ_question_info = await multi_queries_gen(self.chat_mdl, question, query, suff["missing_information"], ret)
    if callback:
        await callback("Next step is to search for the following questions:</br> - " + "</br> - ".join(step["question"] for step in succ_question_info["questions"]))
    
    # 递归处理子问题
    steps = []
    for step in succ_question_info["questions"]:
        steps.append(asyncio.create_task(self._research(chunk_info, step["question"], step["query"], depth-1, callback)))
    results = await asyncio.gather(*steps, return_exceptions=True)
    return "\n".join([str(r) for r in results])
```

### 5. 辅助方法模块
**作用**：线程安全地更新检索结果，避免重复数据。
**位置**：在检索后被调用，负责数据合并。
**配合关系**：被 `_research()` 调用，使用异步锁保证线程安全。

```python
async def _async_update_chunk_info(self, chunk_info, kbinfos):
    async with self._lock:
        if not chunk_info["chunks"]:
            for k in chunk_info.keys():
                chunk_info[k] = kbinfos[k]
        else:
            cids = [c["chunk_id"] for c in chunk_info["chunks"]]
            for c in kbinfos["chunks"]:
                if c["chunk_id"] not in cids:
                    chunk_info["chunks"].append(c)
            
            dids = [d["doc_id"] for d in chunk_info["doc_aggs"]]
            for d in kbinfos["doc_aggs"]:
                if d["doc_id"] not in dids:
                    chunk_info["doc_aggs"].append(d)
```

---

## 三、方法详细解析（强制 5 要素 + 文字流程串讲）

### `__init__` 方法

**文字流程串讲**：
方法接收四个参数进行初始化。首先将 LLM 模型实例 `chat_mdl` 存储为实例属性，用于后续的 LLM 调用（充分性检查、子问题生成）。然后将配置字典 `prompt_config` 存储，用于获取 Tavily API Key 和知识图谱开关。接着存储知识库检索函数 `kb_retrieve` 和知识图谱检索函数 `kg_retrieve`，这两个是偏函数，已绑定知识库 ID 等参数。最后创建一个异步锁 `_lock`，用于保护并发更新 chunk_info 时的数据一致性。

**强制 5 要素**：
| 要素 | 内容 |
|------|------|
| 入参 | `chat_mdl: LLMBundle`（必填，LLM 模型实例）；`prompt_config: dict`（必填，配置字典）；`kb_retrieve: partial`（选填，默认 None）；`kg_retrieve: partial`（选填，默认 None） |
| 核心逻辑 | 存储模型实例、配置、检索函数，创建异步锁 |
| 输出形式 | 无返回值，初始化实例属性 |
| 底层关键依赖 | `asyncio.Lock`（异步锁） |
| 关键代码片段 | `self._lock = asyncio.Lock()` |

---

### `research` 方法

**文字流程串讲**：
方法作为对外入口，接收检索结果容器、原始问题、当前查询、递归深度和回调函数。首先检查是否有回调函数，若有则发送开始标记 `<START_DEEP_RESEARCH>` 通知外部系统研究开始。然后调用内部 `_research()` 方法执行实际的递归检索逻辑。最后在检索完成后，若有回调则发送结束标记 `<END_DEEP_RESEARCH>` 通知研究结束。这种设计将生命周期管理与业务逻辑分离。

**强制 5 要素**：
| 要素 | 内容 |
|------|------|
| 入参 | `chunk_info: dict`（必填，检索结果容器）；`question: str`（必填，原始问题）；`query: str`（必填，当前查询）；`depth: int`（选填，默认 3，递归深度）；`callback: Callable`（选填，默认 None） |
| 核心逻辑 | 包装递归调用，添加生命周期标记 |
| 输出形式 | 无返回值，结果写入 chunk_info |
| 底层关键依赖 | `asyncio`（异步执行） |
| 关键代码片段 | `await callback("<START_DEEP_RESEARCH>")` |

---

### `_retrieve_information` 方法

**文字流程串讲**：
方法执行三阶段检索流程。第一阶段：知识库检索，检查 `_kb_retrieve` 是否存在，若存在则调用该偏函数获取知识库检索结果，异常时记录错误日志并继续。第二阶段：网络检索，检查配置中是否有 `tavily_api_key`，若有则创建 Tavily 客户端并调用 `retrieve_chunks()` 获取网络搜索结果，将结果合并到 kbinfos 中。第三阶段：知识图谱检索，检查配置中 `use_kg` 开关和 `_kg_retrieve` 函数是否存在，若都满足则调用知识图谱检索，将结果插入到 chunks 列表头部（优先展示）。每个阶段都有独立的异常处理，确保单阶段失败不影响其他阶段。

**强制 5 要素**：
| 要素 | 内容 |
|------|------|
| 入参 | `search_query: str`（必填，搜索查询字符串） |
| 核心逻辑 | 三阶段检索：知识库 → 网络 → 知识图谱 |
| 输出形式 | `dict`，包含 chunks 和 doc_aggs 列表 |
| 底层关键依赖 | `Tavily`（网络检索客户端）；`_kb_retrieve`（知识库检索函数）；`_kg_retrieve`（知识图谱检索函数） |
| 关键代码片段 | `kbinfos["chunks"].insert(0, ck)` |

---

### `_research` 方法

**文字流程串讲**：
方法执行递归检索核心逻辑。首先检查递归深度，若 depth 为 0 则返回空字符串终止递归。然后通过回调通知当前搜索的查询内容。接着调用 `_retrieve_information()` 获取检索结果，并通过回调通知检索耗时和结果数量。调用 `_async_update_chunk_info()` 将新结果合并到全局结果容器中，使用 `kb_prompt()` 格式化检索内容。随后调用 `sufficiency_check()` 检查信息是否充分，若充分则通过回调通知并返回结果。若不充分，调用 `multi_queries_gen()` 生成子问题列表，通过回调通知下一步搜索的问题。最后为每个子问题创建异步任务，递归调用 `_research()` 并发执行，使用 `asyncio.gather()` 等待所有任务完成，返回合并后的结果字符串。

**强制 5 要素**：
| 要素 | 内容 |
|------|------|
| 入参 | `chunk_info: dict`（必填）；`question: str`（必填）；`query: str`（必填）；`depth: int`（选填，默认 3）；`callback: Callable`（选填） |
| 核心逻辑 | 递归检索 → 充分性检查 → 子问题生成 → 并发递归 |
| 输出形式 | `str`，合并后的检索结果 |
| 底层关键依赖 | `sufficiency_check()`（充分性检查）；`multi_queries_gen()`（子问题生成）；`asyncio.gather()`（并发执行） |
| 关键代码片段 | `results = await asyncio.gather(*steps, return_exceptions=True)` |

---

## 四、同类逻辑对比表

| 功能名称 | 核心流程 | 入参 | 底层依赖 API | 输出格式 | 差异场景 |
|---------|---------|------|-------------|---------|---------|
| 知识库检索 | 调用预绑定的偏函数 | search_query | `_kb_retrieve` | chunks + doc_aggs | 内部知识库数据 |
| 网络检索 | Tavily API 调用 | search_query | `Tavily.retrieve_chunks()` | chunks + doc_aggs | 需配置 tavily_api_key |
| 知识图谱检索 | 图谱实体关系查询 | search_query | `_kg_retrieve` | content_with_weight | 需启用 use_kg 配置 |

---

## 五、疑惑解答

**Q1: 为什么使用异步锁保护 chunk_info 更新？**
A: 在递归检索过程中，多个子问题可能并发执行，它们都会更新同一个 chunk_info 字典。使用异步锁可以防止并发写入导致的数据竞争和重复数据问题。

**Q2: 递归深度 depth 的作用是什么？**
A: depth 控制递归的最大层数，防止无限递归。默认值 3 表示最多分解 3 层子问题，这是一个经验值，平衡了检索深度和性能开销。

**Q3: 为什么知识图谱结果插入到列表头部？**
A: 知识图谱检索结果通常包含实体关系信息，对于复杂问题的回答更有价值，插入头部可以优先展示这些高质量结果。

---

## 六、规范修正

1. **术语统一**：将 "kb_retrieve" 统一称为 "知识库检索函数"，"kg_retrieve" 统一称为 "知识图谱检索函数"
2. **命名规范**：`_retrieve_information` 方法名建议改为 `_multi_source_retrieve` 更准确表达多源检索含义
3. **异常处理**：建议将各阶段的异常类型细化，区分网络超时、API 限流等不同场景

---

## 七、可复现实操步骤

### 步骤 1：初始化检索器
```python
from rag.advanced_rag import DeepResearcher
from api.db.services.llm_service import LLMBundle
from functools import partial

# 准备 LLM 模型
chat_mdl = LLMBundle(tenant_id, chat_model_config)

# 准备配置
prompt_config = {
    "tavily_api_key": "your-api-key",
    "use_kg": True
}

# 准备检索函数
kb_retrieve = partial(knowledge_base_search, kb_id="kb_123")
kg_retrieve = partial(knowledge_graph_search, kb_id="kb_123")

# 创建检索器
researcher = DeepResearcher(chat_mdl, prompt_config, kb_retrieve, kg_retrieve)
```

### 步骤 2：执行深度研究
```python
import asyncio

chunk_info = {"chunks": [], "doc_aggs": []}

async def progress_callback(msg):
    print(f"[Progress] {msg}")

# 执行研究
asyncio.run(researcher.research(
    chunk_info=chunk_info,
    question="什么是 RAG 技术？",
    query="RAG 技术原理",
    depth=3,
    callback=progress_callback
))
```

### 步骤 3：获取研究结果
```python
# chunk_info 已被填充检索结果
for chunk in chunk_info["chunks"]:
    print(f"Content: {chunk['content_with_weight'][:100]}...")
```

---

## 八、关键模块总览

| 模块名称 | 负责功能 | 在流程中的核心作用 |
|---------|---------|------------------|
| `TreeStructuredQueryDecompositionRetrieval` | 深度研究检索器 | 整体协调递归检索流程 |
| `_retrieve_information` | 多源信息检索 | 整合知识库、网络、图谱三种数据源 |
| `_research` | 递归检索核心 | 驱动问题分解和递归执行 |
| `_async_update_chunk_info` | 结果合并 | 线程安全地合并检索结果 |
| `sufficiency_check` | 充分性检查 | 判断是否需要继续分解问题 |
| `multi_queries_gen` | 子问题生成 | 生成下一步需要搜索的问题 |
