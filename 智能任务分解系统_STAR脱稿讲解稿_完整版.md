# 智能任务分解系统 — STAR 完整版脱稿讲解稿（8 职责）

> 定位：Agent / 智能体方向面试项目（Plan-Execute-Report 架构，LangGraph/LangChain 技术栈）
> 目标：每个职责 2~3 分钟，8 个职责合计 16~20 分钟
> 背诵方法：S（场景一句话）→ T（任务一句话）→ A（2~3 个实现点+代码）→ R（3 个量化数据）→ 金句收尾

---

## 开场 30 秒（项目总览）

"我参与的项目是一个智能任务分解与执行系统，核心是 Plan-Execute-Report 架构：把用户一个复杂查询自动拆成多个可独立执行的子任务，按依赖关系编排执行，最后汇总成结构化报告。整体技术栈是 LangGraph + LangChain + FastAPI，Pydantic 做状态建模，Redis 做缓存。我负责 8 个核心模块：任务分解、状态管理、检索执行器、证据追踪、章节生成、文档预处理与分块、API 接口与流式输出、缓存与性能优化。整体效果是：多条件查询分解覆盖 80%、执行过程 100% 可追溯、高频查询响应从 5 秒降到 500 毫秒。"

---

## 职责一：智能任务分解与执行计划生成（约 3 分钟）

### S — 场景

"用户一个复杂查询往往需要多次检索和推理。比如'对比 A 产品和 B 产品的性能差异并给出选型建议'，它不是一次搜索能回答的，至少得拆成四个子任务：检索 A 数据、检索 B 数据、对比分析、给建议，而且对比依赖两个检索结果。没有任务分解，这类问题要么答不全，要么一次塞太多超出上下文。"

### T — 任务

"实现任务分解器：把复杂查询拆成带依赖关系的子任务图，并且能稳定解析 LLM 的输出。"

### A — 实现（LLM 分解 + 容错 JSON 解析 + 数据清洗）

"核心在 task_decomposer.py 的 decompose 方法，三步走。第一步，调用 LLM 分析意图：

```python
def decompose(self, query: str) -> TaskDecompositionResult:
    prompt = TASK_DECOMPOSE_PROMPT.format(query=query, max_tasks=self._max_tasks)
    response = self._invoke_llm(prompt)
```

Prompt 用 few-shot 示例要求输出 JSON，每个子任务含 id、description、task_type、depends_on 四个字段，max_tasks 限制最多 6 个。

第二步，容错解析。这里有个坑：LLM 有时返回非标准 JSON，前后带说明文字或用 markdown 代码块包裹。所以我用 extract_json_text 做了两层容错——先匹配 ```json 代码块，取不到就找第一个 `{` 到最后一个 `}`：

```python
fenced = _CODE_BLOCK_RE.search(text)   # 匹配 ```json ... ```
if fenced:
    return fenced.group(1).strip()
start, end = text.find("{"), text.rfind("}")
return text[start:end + 1]
```

第三步，数据清洗。LLM 可能自创 task_type、depends_on 给成字符串。我的策略：不合法 task_type 映射成 custom 并保留原始值；缺失字段补默认值；depends_on 字符串统一拆成列表，最后 validate_dependencies 检查循环依赖。"

### R — 成果（3 个量化数据）

"任务分解覆盖了 80% 的多条件查询场景；任务分类准确率达到 90%；剩下 20% 分解失败的由上层 BasePlanner 用原始查询单任务兜底，不阻塞。"

### 金句

"**分解的本质是把'一个大问题'变成'一组可验证的小问题'，LLM 负责拆，代码负责兜底。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么用 LLM 不用规则匹配？ | 用户表达千变万化（比较/对比/分析差异），规则写不完；LLM 做语义理解配合输出格式约束更稳 |
| max_tasks 为什么是 6？ | 少于 3 拆得不够细，多于 8 执行时间过长且 LLM 质量下降，6 是平衡点 |
| 为什么输出 JSON 不是自然语言？ | JSON 可直接解析成结构化数据，省掉二次信息提取 |
| LLM 输出不是 JSON 怎么办？ | 两层容错提取 + 解析失败抛异常，上层用原始查询单任务兜底 |
| 依赖关系怎么验证？ | validate_dependencies 检查循环依赖和悬空引用 |

---

## 职责二：任务执行状态管理与持久化（约 3 分钟）

### S — 场景

"多子任务执行时系统是黑盒：不知道每个任务执行到哪、结果是什么、哪些失败需要重试。出了问题没法排查。"

### T — 任务

"设计贯穿 Plan-Execute-Report 全流程的状态模型，让执行过程 100% 可追溯。"

### A — 实现（四层 Pydantic 状态模型）

"核心在 state.py，我用 Pydantic 设计了四层状态。最外层 PlanExecuteState 聚合三个阶段：

```python
class PlanExecuteState(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    input: str = ""
    plan_context: Optional[PlanContext] = None
    execution_context: Optional[ExecutionContext] = None
    report_context: Optional[ReportContext] = None
```

关键设计有两个。第一，三层 Context 职责分离：Plan 阶段只管 PlanContext（原始查询、澄清历史、用户偏好），Execute 阶段用 ExecutionContext，Report 阶段用 ReportContext，互不耦合。第二，ExecutionContext 里用 intermediate_results 做任务间数据传递，key 是 task_id，value 是 `{"answer": 文本, "raw_result": 原始数据}`——后续任务按 task_id O(1) 取用。还有 model_post_init 钩子：初始化时如果 Context 是 None 就自动创建默认实例，杜绝空指针。"

### R — 成果（3 个量化数据）

"实现了 100% 的执行过程可追溯；失败任务自动识别；重试成功率提升 70%。"

### 金句

"**状态管理解决的不是'能不能跑'，而是'坏了能不能查'。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么用 Pydantic 不用 dataclass？ | 自动类型校验、model_dump() 一行序列化存 Redis、嵌套模型自动处理 |
| 为什么分三层 Context 不平铺？ | 职责分离，每个阶段只关心自己的状态，避免耦合和字段爆炸 |
| intermediate_results 为什么用字典？ | 按 task_id O(1) 查找，且后续任务可能依赖多个前序结果 |
| 状态怎么持久化？ | Pydantic model_dump() 序列化后存 Redis，重启可恢复 |
| 任务执行到一半失败？ | 状态里记录错误，重试时从失败点恢复而不是从头开始 |

---

## 职责三：检索执行器与搜索工具集成（约 3 分钟）

### S — 场景

"系统里有 local_search、global_search、hybrid_search、chain_exploration 等多种搜索工具，调用方式不一样、返回格式不统一。调用方直接操作工具，代码非常混乱。"

### T — 任务

"封装检索执行器：对外提供统一接口和统一返回格式，屏蔽工具差异。"

### A — 实现（工具注册表 + 方法降级 + 统一包装）

"核心在 retrieval_executor.py 的 execute_task，四步流程。第一步，从工具注册表拿实例，带缓存：

```python
def _get_tool_instance(self, task_type: str) -> Any:
    if task_type in self._tool_registry:
        if task_type not in self._tool_cache:
            self._tool_cache[task_type] = self._tool_registry[task_type]()
        return self._tool_cache[task_type]
```

同类型工具只初始化一次，因为初始化可能加载模型、建立连接。第二步，调用时做方法降级：优先 structured_search，没有就降级 search 并把字符串结果包装成统一字典：

```python
if hasattr(tool, "structured_search"):
    return tool.structured_search(payload)
if hasattr(tool, "search"):
    result = tool.search(payload)
    return result if isinstance(result, dict) else {"answer": result, "retrieval_results": []}
```

用 hasattr 做方法检查而不是定义统一接口，因为部分工具来自第三方库，无法强制实现接口。第三步，提取证据并去重注册；第四步，构建执行记录更新状态，成功写 intermediate_results，失败记 errors。"

### R — 成果（3 个量化数据）

"封装了 3 种搜索策略；搜索失败自动降级；系统可用性达到 99%。"

### 金句

"**统一接口的价值不在省代码，而在让上层'无感知'。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么用注册表不用 if-else？ | 符合开闭原则，加新工具只需注册一行，不改执行器逻辑 |
| 为什么用 hasattr 不用统一基类？ | 第三方工具无法强制实现接口，hasattr 更灵活 |
| 为什么按优先级尝试六个 key？ | 不同工具返回结构不同：local 用 answer、global 用 response、hybrid 用 summary |
| 证据追踪失败会影响主流程吗？ | 不会，try-except 包裹后降级用原始结果，辅助功能不阻塞主流程 |
| 工具初始化为什么缓存？ | 初始化可能加载模型/建连接，重复创建浪费资源 |

---

## 职责四：证据追踪与引用标注（约 2.5 分钟）

### S — 场景

"用户看到回答后经常问'这个结论的依据是什么'。没有证据追踪，回答就像无源之水，尤其在技术文档场景，用户需要知道结论出自哪份文档的哪一段。"

### T — 任务

"实现证据追踪：记录每条回答信息的来源，支持溯源和去重。"

### A — 实现（双映射注册表 + 去重 + 惰性创建）

"核心在 evidence_tracker.py。EvidenceTracker 维护 by_key 和 by_id 两个映射。register 是入口，用 source_id:granularity 组合键去重——比如 doc1:chunk 和 doc1:entity 是不同来源，同源同粒度只保留分数最高的：

```python
key = self._make_key(item)   # "source_id:granularity"
stored = self.registry["by_key"].get(key)
if stored is None:
    self.registry["by_key"][key] = {"result": item, "occurrences": 1}
    ...
```

一个关键细节：当新证据分数更高时，要更新 by_id 中所有指向同一 key 的映射，保证引用一致性。resolve 方法按 result_id 溯源，返回来源、证据、元数据。工厂函数 get_evidence_tracker 从 ExecutionContext.evidence_registry 惰性创建——不是所有查询都需要追踪，简单查询就不创建，省资源。"

### R — 成果（3 个量化数据）

"回答内容 100% 可追溯；用户点击引用可跳转原文；阅读效率提升 50%。"

### 金句

"**可追溯不是功能，是信任。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么用 source_id:granularity 去重？ | result_id 每次检索都不同无法去重；来源+粒度才能识别重复证据 |
| occurrences 有什么用？ | 统计引用次数，被引用越多的证据越重要 |
| 为什么用 dataclass 不用 Pydantic？ | 纯逻辑组件无需序列化校验，dataclass 更轻量 |
| 为什么惰性创建？ | 简单查询不需要追踪，按需初始化省资源 |
| 证据冲突怎么处理？ | 同源同粒度保留分数更高者，并同步更新所有映射保证一致性 |

---

## 职责五：报告章节生成与格式化输出（约 3 分钟）

### S — 场景

"复杂查询的回答需要结构化展示；而且证据多时，一次性塞给 LLM 会超出上下文窗口——一个章节可能关联 30 条证据，每条约 200 字，总 6000 字，加上大纲和指令超出 8K 上下文。"

### T — 任务

"实现章节写作器：按大纲和证据生成各章节 Markdown，用分批写作突破 LLM token 限制。"

### A — 实现（分批写作 + 前文摘要 + 输出清洗）

"核心在 section_writer.py 的 write_section，四步。第一步选证据：优先用大纲指定的 evidence_ids，没有就用备选，再没有用全部。第二步分批：按 max_evidence_per_call 每批 8 条切分。第三步逐批调用 LLM，多批时在 Prompt 里注入前文摘要：

```python
if self.config.enable_multi_pass and len(batches) > 1:
    context_instruction = f"**写作阶段**: 第{batch_index}/{len(batches)}批，请确保与前文衔接。\n{self._extract_previous_summary(contents)}"
```

前文摘要取之前批次最后 800 字，让 LLM 知道前面写了什么，避免重复或矛盾。第四步清洗：LLM 经常重复生成章节标题，_sanitize_content 做归一化比对后跳过重复标题行。还有 _build_outline_snapshot：不传完整大纲，只传报告标题、章节索引、总章节数、前后章节标题，既让 LLM 知道位置又省 token。"

### R — 成果（3 个量化数据）

"支持 5 种格式化元素输出；生成报告可读性评分提升 45%；技术文档类查询用户满意度提升 60%。"

### 金句

"**长文生成的本质是'分而治之 + 上下文衔接'，不是一次硬塞。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么每批 8 条证据？ | 每条摘要约 200 字，8 条 1600 字+大纲 400+指令 600+前文 800≈3400 字，8K 窗口留足生成空间 |
| 前文摘要为什么取 800 字？ | 全部前文太长占 token；800 字 2~3 段足以衔接 |
| 为什么传 snapshot 不传完整大纲？ | 完整大纲可能超 1000 字，snapshot 只留位置信息约 200 字 |
| 多批结果怎么拼接？ | 按顺序 append，最后统一清洗去重标题 |
| 某批生成失败怎么办？ | 重试该批或降级用该批证据的原文摘要 |

---

## 职责六：文档预处理与中文文本分块优化（约 2.5 分钟）

### S — 场景

"企业内部文档格式多样；中文和英文不同，没有空格分词，按固定字数硬切会把一句话切成两半，检索时上下文断裂。比如'人工智能是一种模拟人类智能的技术'按 10 字硬切，变成'人工智能是一种模'和'拟人类智能的技术'。"

### T — 任务

"实现中文友好分块器：保证语义完整，控制块大小，处理超长文本。"

### A — 实现（HanLP 分词 + 句子边界切分 + 重叠）

"核心在 document_processor.py 和 text_chunker.py。先用 HanLP 做中文分词，按 chunk_size 累加；超过阈值时不在中间硬切，而是找下一个句子结束符（。！？）在句子边界断开：

```python
if end_pos < len(all_tokens):
    sentence_end = self._find_next_sentence_end(all_tokens, end_pos)
    if sentence_end <= start_pos + self.chunk_size + 100:
        end_pos = sentence_end
```

下一块起始位置考虑 overlap 重叠，并且尽量也在句子边界开始，保证跨块语义衔接。超长文本先 _preprocess_large_text 按段落预分割，确保每段不超过 max_text_length，避免 HanLP 处理超时或 OOM。"

### R — 成果（3 个量化数据）

"支持 5 种文档格式统一处理；中文分块语义完整性提升 40%；检索召回率提升 25%。"

### 金句

"**中文分块的关键是'先分词、再在句子边界切'，而不是按字数硬切。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么用 HanLP 不用 jieba？ | HanLP 基于深度学习的粗粒度分词对专业术语和新词更准 |
| chunk_size 为什么 500？ | 300 太碎、800 太杂混主题，500 是实验平衡点 |
| overlap 为什么 50？ | 50 字约 1~2 句话够衔接；太大导致大量重复浪费存储计算 |
| 超长文本怎么处理？ | 按段落预分割成多段，逐段分块，防止 HanLP 超时/OOM |
| 跨块信息丢失怎么办？ | overlap 重叠保证边界语义衔接 |

---

## 职责七：API 接口与前端联调（约 2 分钟）

### S — 场景

"后端的智能问答能力要通过 API 暴露给前端：普通查询 RESTful 一次返回；复杂查询需要流式输出，让用户实时看到思考过程。"

### T — 任务

"实现 RESTful + SSE 流式两类接口，设计请求响应模型，完成前端联调。"

### A — 实现（FastAPI + SSE）

"用 FastAPI 实现两类接口。普通查询：

```python
@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    result = orchestrator.run(request.query)
    return QueryResponse(answer=result["answer"], references=result.get("references", []), ...)
```

流式查询用 SSE：

```python
@app.post("/api/query/stream")
async def query_stream(request: QueryRequest):
    async def generate():
        async for chunk in orchestrator.run_stream(request.query):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

SSE 格式必须严格是 data: 前缀 + 双换行，否则前端 EventSource 解析不了。请求校验交给 Pydantic 自动处理，query 为空或 max_iterations 为负直接返回 422。"

### R — 成果（3 个量化数据）

"实现 RESTful + SSE 两类接口；平均响应时间小于 3 秒；前端联调一次通过率 100%。"

### 金句

"**接口设计一半在协议，一半在让前端'好接'。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么选 FastAPI 不选 Flask？ | 原生 async 适合流式、自动 OpenAPI 文档、Pydantic 零代码校验 |
| 为什么用 SSE 不用 WebSocket？ | SSE 单向推送、浏览器原生 EventSource 自动重连；WebSocket 双向需要心跳，对纯服务端推送太重 |
| SSE 格式注意什么？ | 必须 data: 前缀 + \n\n 双换行，[DONE] 标记结束 |
| 接口超时怎么办？ | Nginx proxy_read_timeout 调到 300 秒 |
| 并发请求怎么处理？ | FastAPI 异步 + 信号量限流，防止 LLM 并发打爆 |

---

## 职责八：缓存机制与性能优化（约 2 分钟）

### S — 场景

"相同或相似的查询重复执行非常浪费：每次都调 LLM API，既慢又贵。"

### T — 任务

"设计两级缓存，让高频查询直接命中缓存，把响应时间和 API 成本降下来。"

### A — 实现（内存+Redis 两级缓存）

"核心在 CacheManager：

```python
class CacheManager:
    def __init__(self, redis_client, ttl=3600):
        self.redis = redis_client
        self.memory_cache = {}

    def get(self, key):
        if key in self.memory_cache:
            return self.memory_cache[key]        # 一级：内存
        cached = self.redis.get(key)             # 二级：Redis
        if cached:
            value = json.loads(cached)
            self.memory_cache[key] = value       # 回填内存
            return value
        return None
```

三个工程细节：缓存雪崩——TTL 加随机偏移（1 小时±10 分钟），避免大量缓存同时过期打爆 LLM；缓存穿透——空结果也缓存，短 TTL 5 分钟；缓存键——查询 JSON 排序后 MD5 哈希，固定 32 字符省内存。"

### R — 成果（3 个量化数据）

"两级缓存命中率 75%；高频查询响应从 5 秒降到 500 毫秒；LLM API 调用成本降低 60%。"

### 金句

"**缓存解决的不只是延迟，还有'每次回答都在花钱'。**"

### 面试官追问预测

| 问题 | 参考回答 |
|-----|---------|
| 为什么两级缓存不只 Redis？ | 内存纳秒级、Redis 毫秒级，高频查询内存更快；但内存限单进程，Redis 可跨进程共享 |
| TTL 为什么 1 小时？ | 知识库约每天更新一次，1 小时内数据基本不变 |
| 为什么 MD5 做键？ | 长查询直接做键浪费内存，MD5 固定 32 字符，冲突概率 2^-128 |
| 缓存一致性怎么保证？ | 写时两级同写；Redis 过期后内存值下次读取被覆盖 |
| 缓存失效策略？ | TTL 随机偏移防雪崩 + 空结果短 TTL 防穿透 |

---

## 八大职责记忆口诀

| 职责 | 一句话概括 | 核心文件 |
|-----|-----------|---------|
| ① 任务分解 | "LLM 拆任务，JSON 容错，清洗兜底" | `task_decomposer.py` |
| ② 状态管理 | "四层 Pydantic，intermediate_results 传数据" | `state.py` |
| ③ 检索执行器 | "注册表+降级，统一字典格式" | `retrieval_executor.py` |
| ④ 证据追踪 | "source:granularity 去重，惰性创建" | `evidence_tracker.py` |
| ⑤ 章节生成 | "分批写作+前文摘要，突破 token 限制" | `section_writer.py` |
| ⑥ 文本分块 | "HanLP 分词，句子边界切，overlap 衔接" | `text_chunker.py` |
| ⑦ API 接口 | "FastAPI，RESTful+SSE，Pydantic 校验" | `api` |
| ⑧ 缓存 | "内存+Redis 两级，MD5 键，随机 TTL" | `cache_manager.py` |

---

## 面试现场 checklist

- [ ] 开场 30 秒项目总览（Plan-Execute-Report 架构 + 8 个职责 + 3 个量化数据）
- [ ] 每个职责按 S→T→A→R 讲，A 部分只贴 1 个关键代码片段
- [ ] 每个职责 3 个量化数据倒背如流
- [ ] 强调架构主线：任务分解（拆）→ 状态管理（记）→ 检索执行（取）→ 证据追踪（证）→ 章节生成（写）→ 分块（备）→ API（出）→ 缓存（快）
- [ ] 被追问先确认问题："您是想了解……对吗？"
- [ ] 不会的问题："这个我没深入做过，但我理解其核心是……"
- [ ] 结束前 30 秒：总结这是一套完整的 Agent 执行闭环
