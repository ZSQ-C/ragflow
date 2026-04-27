# 🔍 附录：简历技能点 ↔ 项目源码逐行对照

> 对应 `RAGFlow项目简历.md` 中核心工作第 4~8 条的技术点，在 RAGFlow 源码中的具体实现位置和详细解读。  
> **阅读方式**：先看"代码位置"找到文件，再看"执行流程"理解数据流，最后看"关键代码解读"掌握细节。

---

## 一、查询扩展与优化模块

> **简历描述**：词权重动态计算（IDF × NER × POS）、同义词三级查找（词典→WordNet→Redis）、细粒度中文分词（1~5-grams）

### 1.1 词权重动态计算

**代码位置**：`rag/nlp/term_weight.py` L27-L247（`Dealer` 类）

**资源文件**：
- `rag/res/ner.json`：命名实体识别字典，存储 `{词: (实体类型, 词频, 文档数)}`
- `rag/res/term.freq`：词频统计字典

#### 执行流程

```
用户问题："介绍一下华为公司的最新产品"
      ↓
FulltextQueryer.question() 调用 self.tw.weights(tokens)
      ↓
term_weight.Dealer.weights() 逐词计算
      ↓
每个词走三个子函数：freq(t) → idf → df(t) → idf → ner(t) → postag(t)
      ↓
最终权重 = (0.3×词频IDF + 0.7×文档IDF) × NER系数 × 词性系数
      ↓
归一化处理后返回 [(词, 权重), ...]
```

#### 关键代码解读

**（1）NER 命名实体函数（L170-L179）**

```python
def ner(t):
    if num_pattern.match(t):
        return 2               # 数字类：中等权重
    if short_letter_pattern.match(t):
        return 0.01            # 单字母：几乎忽略
    if not self.ne or t not in self.ne:
        return 1               # 不在字典中：基准权重
    m = {"toxic": 2, "func": 1, "corp": 3, "loca": 3, "sch": 3, "stock": 3, "firstnm": 1}
    return m[self.ne[t]]       # 按实体类型返回系数
```

**设计意图**：`corp`（公司名）、`loca`（地名）、`sch`（学校）、`stock`（股票）获得 **×3** 加权，因为它们是查询的最核心锚点。用户搜"华为"就是想要关于华为公司的文档，该词应该是搜索的主导因子。

**（2）词性标注函数（L181-L191）**

```python
def postag(t):
    t = rag_tokenizer.tag(t)    # 调用词性标注
    if t in set(["r", "c", "d"]):
        return 0.3              # 副词/连词/数词：低权重
    if t in set(["ns", "nt"]):
        return 3                # 地名/机构名：高权重
    if t in set(["n"]):
        return 2                # 名词：较高权重
    return 1                    # 其他：基准
```

**设计意图**：`"r"`（副词，如"很"、"极"）、`"c"`（连词，如"和"、"与"）、`"d"`（数词）被降到 0.3——它们几乎不含检索信息量。`"ns"`（地名）、`"nt"`（机构名）被提到 3——和 NER 互补增强。

**（3）词频 IDF 混合公式（L225-L234）**

```python
def idf(s, N):
    return math.log10(10 + ((N - s + 0.5) / (s + 0.5)))

# 最终权重 = (0.3×词频IDF + 0.7×文档IDF) × NER系数 × 词性系数
idf1 = np.array([idf(freq(t), 10000000) for t in tks])
idf2 = np.array([idf(df(t), 1000000000) for t in tks])
wts = (0.3 * idf1 + 0.7 * idf2) * np.array([ner(t) * postag(t) for t in tks])
```

**为什么混合 0.3:0.7**：词频 IDF（70%）反映词在**整个语料**中出现的总频次——频次越低权重越高（稀有词更重要）；文档频 IDF（30%）反映词在**多少篇文档**中出现过——出现文档越少权重越高（专业术语更重要）。两者混合避免单一 IDF 的偏差：有些词总频次低但出现在很多短文档中，混合后仍能获得合理权重。

**（4）最终归一化（L246-L247）**

```python
S = np.sum([s for _, s in tw])
return [(t, s / S) for t, s in tw]
```

所有词的权重除以总和归一化到 [0,1]，确保不同长度的查询之间权重可比较。

---

### 1.2 同义词三级查找

**代码位置**：`rag/nlp/synonym.py` L33-L103（`Dealer` 类）

#### 执行流程

```
lookup(tk) → 第一级：self.dictionary.get(tk)
                ↓ 命中 → 直接返回，最多 8 个同义词
                ↓ 未命中
            第二级：wordnet.synsets(tk) （仅纯字母 token）
                ↓ 命中 → 返回 WordNet 同义词集
                ↓ 未命中
            第三极：返回 [] （无同义词）
```

#### 关键代码解读

**（1）自定义词典加载（L40-L43）**

```python
path = os.path.join(get_project_base_directory(), "rag/res", "synonym.json")
self.dictionary = json.load(open(path, 'r'))
```

`synonym.json` 是业务自定义的同义词字典，格式 `{"token": ["同义词1", "同义词2", ...]}`。优先级最高，允许业务定制（如行业术语映射）。

**（2）查词主函数（L78-L103）**

```python
def lookup(self, tk, topn=8):
    # 第一级：自定义词典
    key = re.sub(r"[ \t]+", " ", tk.strip())
    res = self.dictionary.get(key, [])
    if res:
        return res[:topn]

    # 第二级：WordNet（仅纯字母）
    if re.fullmatch(r"[a-z]+", tk):
        wn_set = {re.sub("_", " ", syn.name().split(".")[0])
                  for syn in wordnet.synsets(tk)}
        wn_set.discard(tk)
        return [t for t in wn_set if t][:topn]

    # 第三级：无同义词
    return []
```

**（3）Redis 动态加载（L56-L75）**

```python
def load(self):
    if not self.redis: return
    if self.lookup_num < 100: return
    tm = time.time()
    if tm - self.load_tm < 3600: return       # 每小时最多加载一次

    self.load_tm = time.time()
    d = self.redis.get("kevin_synonyms")
    if d:
        self.dictionary = json.loads(d)       # Redis 覆盖本地字典
```

**设计意图**：Redis 中的同义词可以动态更新（运营人员通过管理界面添加），无需重启服务。每小时最多从 Redis 加载一次，每 100 次查询触发一次检测，既保证时效性又不频繁访问 Redis。

---

### 1.3 细粒度中文分词（1~5-grams）

**代码位置**：`rag/nlp/query.py` L94-L117

#### 关键代码解读

```python
def need_fine_grained_tokenize(tk):
    if len(tk) < 3:
        return False                    # 长度<3 不切（如"华为"）
    if re.match(r"[0-9a-z\.\+#_\*-]+$", tk):
        return False                    # 纯数字/字母/符号 不切
    return True

for tk, w in sorted(twts, key=lambda x: x[1] * -1):
    sm = (
        rag_tokenizer.fine_grained_tokenize(tk).split()
        if need_fine_grained_tokenize(tk)
        else []
    )
```

**过滤规则设计意图**：
- `len(tk) < 3`：两个字以下的词（如"电子"、"华为"）不需要再切 n-gram，否则会产生无意义的 1-gram
- 纯数字/字母：`"SKU-12345"` 不需切分，它是一个完整的专有名词
- 中文字词：`"检索增强生成"`→切出 `["检索","增强","生成","检索增强","增强生成","检索增强生成"]`

**为什么能提升召回**：用户可能输入任意子串组合——"增强检索"、"检索生成"——标准分词只能匹配到完整词，而 n-grams 覆盖了所有子串组合。

---

## 二、JSON DSL Agent 工作流编排引擎

> **简历描述**：Graph + Canvas 双层架构，18 种组件，5 路并行执行（asyncio.Semaphore），变量依赖解析，条件分支路由

**代码位置**：`agent/canvas.py` L283-L667（Canvas 类）、L42-L281（Graph 类）

### 2.1 Graph 层 —— DSL 反序列化

**代码位置**：`agent/canvas.py` L94-L130

```python
def load(self):
    self.components = self.dsl["components"]
    for k, cpn in self.components.items():
        # 步骤1：工厂模式创建参数对象
        param = component_class(cpn["obj"]["component_name"] + "Param")()
        param.update(cpn["obj"]["params"])
        param.check()                    # 参数校验
        # 步骤2：工厂模式创建组件实例
        cpn["obj"] = component_class(cpn["obj"]["component_name"])(self, k, param)
    self.path = self.dsl["path"]
```

`component_class()` 是一个工厂函数（`agent/component/__init__.py`），自动扫描 `agent.component`、`agent.tools`、`rag.flow` 三个包，通过 `inspect.getmembers` 注册所有组件类。

### 2.2 Canvas 层 —— 5 路并行执行

**代码位置**：`agent/canvas.py` L435-L482

```python
async def _run_batch(f, t):
    max_concurrency = getattr(self._thread_pool, "_max_workers", 5)
    sem = asyncio.Semaphore(max_concurrency)    # 并发信号量

    async def _invoke_one(cpn_obj, sync_fn, call_kwargs, use_async: bool):
        async with sem:                          # 获取信号量许可
            if use_async:
                await cpn_obj.invoke_async(...)  # 异步组件 → await
            else:
                await loop.run_in_executor(       # 同步组件 → 线程池
                    self._thread_pool, partial(sync_fn, ...))

    for each component in path[f:t]:
        # 依赖检查：变量的上游组件是否已完成？
        for _, ele in cpn.get_input_elements().items():
            if ele.get("_cpn_id") not in already_executed:
                self.path.pop(i)   # 依赖未满足，从当前批次移除
                break
        else:
            tasks.append(_invoke_one(...))

    await asyncio.gather(*tasks)                   # 并行等待所有任务
```

**执行机制**：
1. `asyncio.Semaphore(5)` 保证同时最多 5 个组件在跑
2. 异步组件（有 `_invoke_async` 方法）→ 直接在事件循环中 await
3. 同步组件 → `loop.run_in_executor(thread_pool, fn)` 放到独立线程池
4. 所有任务通过 `asyncio.gather` 并行等待完成

### 2.3 变量依赖解析

**代码位置**：`agent/canvas.py` L464-L472

变量引用格式：`{component_id@variable_name}`，如 `{retrieval_0@formalized_content}`。正则匹配后提取 `_cpn_id`，检查该组件是否已完成执行（在 `self.path[:i]` 中）。未完成 → 从当前批次移除，等上游完成后再执行。

### 2.4 路径推进状态机

**代码位置**：`agent/canvas.py` L599-L627

| 组件类型 | 推进逻辑 | 代码行 |
|----------|----------|--------|
| 普通组件 / Begin | `_extend_path(downstream)` | L627 |
| Switch / Categorize | `_extend_path(output("_next"))` | L619 |
| Loop / Iteration | `_append_path(get_start())` | L621 |
| LoopItem 完成 | `_extend_path(parent.downstream)` | L617 |
| ExitLoop | `_extend_path(parent.downstream)` | L623 |

---

## 三、OpenAI Function Calling 工具调用全链路

> **简历描述**：同步/异步/MCP 三种工具统一适配（thread_pool_exec 异步化），Retrieval/CodeExec/Crawler 等工具协同，最大轮次控制防无限循环

### 3.1 Agent 初始化 —— 工具加载与绑定

**代码位置**：`agent/component/agent_with_tools.py` L76-L110

```python
class Agent(LLM, ToolBase):
    def __init__(self, canvas, id, param):
        LLM.__init__(self, canvas, id, param)
        self.tools = {}

        # 步骤1：加载配置的工具实例
        for idx, cpn in enumerate(self._param.tools):
            cpn = self._load_tool_obj(cpn)          # 工厂创建工具对象
            indexed_name = f"{original_name}_{idx}"   # 索引去重
            self.tools[indexed_name] = cpn

        # 步骤2：创建 LLMBundle，max_rounds 防无限循环（默认 5 轮）
        self.chat_mdl = LLMBundle(tenant_id, chat_model_config,
            max_rounds=self._param.max_rounds)

        # 步骤3：构建 OpenAI Function Calling 格式元数据
        for indexed_name, tool_obj in self.tools.items():
            indexed_meta = deepcopy(original_meta)
            indexed_meta["function"]["name"] = indexed_name
            self.tool_meta.append(indexed_meta)

        # 步骤4：加载 MCP 工具
        for mcp in self._param.mcp:
            tool_call_session = MCPToolCallSession(mcp_server, ...)
            self.tools[tnm] = tool_call_session

        # 步骤5：绑定到 LLM（开启 Function Calling）
        self.toolcall_session = LLMToolPluginCallSession(self.tools, callback)
        self.chat_mdl.bind_tools(self.toolcall_session, self.tool_meta)
```

### 3.2 工具调用执行 —— 三种工具统一适配

**代码位置**：`agent/tools/base.py` L58-L73

```python
class LLMToolPluginCallSession(ToolCallSession):
    async def tool_call_async(self, name, arguments):
        tool_obj = self.tools_map[name]

        # 类型1：MCP 工具 → 线程池同步执行，60秒超时
        if isinstance(tool_obj, MCPToolCallSession):
            resp = await thread_pool_exec(
                tool_obj.tool_call, name, arguments, 60)

        # 类型2：异步工具 → 直接 await
        elif hasattr(tool_obj, "invoke_async") and \
             asyncio.iscoroutinefunction(tool_obj.invoke_async):
            resp = await tool_obj.invoke_async(**arguments)

        # 类型3：同步工具 → 线程池执行，避免阻塞事件循环
        else:
            resp = await thread_pool_exec(
                tool_obj.invoke, **arguments)

        # 记录到 Redis 日志
        self.callback(name, arguments, resp, elapsed_time=elapsed)
        return resp
```

### 3.3 完整 Tool Call 数据流

```
LLM 推理 → 返回 tool_choice: {"function": {"name":"retrieval_0","args":{"query":"..."}}}
    ↓
LLMToolPluginCallSession.tool_call_async("retrieval_0", {"query":"..."})
    ↓
判断工具类型 → MCP/异步/同步 → 执行
    ↓
canvas.tool_use_callback() → 记录到 Redis ({task_id}-{message_id}-logs)
    ↓
工具结果注入回 LLM 上下文 → 下一轮推理
    ↓
...最多 max_rounds=5 轮...
    ↓
LLM 生成最终回答
```

---

## 四、流式事件系统

> **简历描述**：7 种事件类型（workflow_started/node_started/node_finished/message/message_end/user_inputs/workflow_finished），SSE 协议推送

**代码位置**：`agent/canvas.py` L375-L667（所有事件均在此函数内 yield）

### 4.1 7 种事件的生命周期与代码位置

```
workflow_started  →  L432: yield decorate("workflow_started", {"inputs": ...})
    ↓
node_started      →  L503-L509: 每个组件开始执行前
    ↓
message           →  L528-L565: Message 组件流式输出内容
    │               ├─ 普通文本块：{"content":"...", "audio_binary":null}
    │               ├─ <think> 开始：{"content":"","start_to_think":true}
    │               └─ </think> 结束：{"content":"","end_to_think":true}
    ↓
message_end       →  L567-L568: 消息结束，含引用和附件
    ↓
node_finished     →  L484-L494: inputs/outputs/error/elapsed_time
    ↓
    ├─ 需要用户输入 → L647: user_inputs
    └─ 否则 → L651-L657: workflow_finished
```

### 4.2 Message 流式输出核心逻辑（L516-L568）

```python
if isinstance(cpn_obj.output("content"), partial):
    stream = cpn_obj.output("content")()    # partial() → 触发延迟执行
    async for m in stream:
        if m == "<think>":
            yield decorate("message", {"start_to_think": True})
        elif m == "</think>":
            yield decorate("message", {"end_to_think": True})
        else:
            buff_m += m
            if len(buff_m) > 16:             # 每16字符做一次 TTS
                yield decorate("message", {
                    "content": m,
                    "audio_binary": self.tts(tts_mdl, buff_m)})
                buff_m = ""
            else:
                yield decorate("message", {"content": m})
```

---

## 五、PDF 深度文档解析管道

> **简历描述**：三重乱码检测（PUA 字符 + CID 模式 + 字体编码），自适应阈值 + ONNX OCR 降级，5 种 PDF 引擎热切换

**代码位置**：`deepdoc/parser/pdf_parser.py` L202-L300

### 5.1 检测1：PUA 字符检测（L205-L230）

```python
@staticmethod
def _is_garbled_char(ch):
    cp = ord(ch)
    if 0xE000 <= cp <= 0xF8FF:   return True   # PUA Basic
    if 0xF0000 <= cp <= 0xFFFFF: return True   # PUA Supplementary-A
    if cp == 0xFFFD:             return True   # Unicode 替换字符
    cat = unicodedata.category(ch)
    if cat in ("Cn", "Cs"):     return True    # 未分配/代理对
    return False
```

PUA（Private Use Area，U+E000-F8FF）是 Unicode 留给字体厂商的私有区域。PDF 内嵌字体编码映射错误时，字符被映射到 PUA 区——检测到 PUA 字符 = 字体编码有问题。

### 5.2 检测2：CID 模式检测（L202, L233-L254）

```python
_CID_PATTERN = re.compile(r"\(cid\s*:\s*\d+\s*\)")

def _is_garbled_text(text, threshold=0.5):
    if _CID_PATTERN.search(text):
        return True                  # 直接判定乱码
    garbled_count = 0
    for ch in text:
        if _is_garbled_char(ch): garbled_count += 1
    return garbled_count / total >= threshold
```

`(cid:123)` 是 PDF 字体未解码的标志——文字变成了 CID 编号而非实际字符。一旦检测到立即判定乱码。

### 5.3 检测3：字体编码检测（L257-L300）

```python
@staticmethod
def _has_subset_font_prefix(fontname):
    return bool(re.match(r"^[A-Z0-9]{2,6}\+", fontname))

def _is_garbled_by_font_encoding(page_chars, min_chars=20):
    # 统计子集字体字符比例 vs CJK字符比例
    # 子集字体占比 > 50% 且 CJK < 5% → 字体编码错乱
```

子集字体（如 `"ABCDEF+SimSun"`）在某些旧版 PDF 标准中将 CJK 字形错误映射到 ASCII 码位。

### 5.4 自适应阈值与 OCR 降级

```python
lower = max(15, total_chars * 0.2)   # 至少15个，或 20%
upper = min(35, total_chars * 0.3)   # 最多35个，或 30%
if garbled_chars > upper or CID_found or font_encoding_garbled:
    # 触发 ONNX OCR 降级重新识别
```

### 5.5 5 种 PDF 引擎热切换

**代码位置**：`rag/app/naive.py` L254-L261

```python
PARSERS = {
    "deepdoc": by_deepdoc,       # 自研 ONNX OCR 引擎（默认）
    "mineru": by_mineru,         # MinerU 商业 API
    "docling": by_docling,       # IBM 开源 Docling
    "tcadp parser": by_tcadp,    # 腾讯云文档解析
    "paddleocr": by_paddleocr,   # 百度 PaddleOCR
    "plaintext": by_plaintext,   # 纯文本提取
}
```

---

## 📋 源码快速索引

| 简历技能点 | 核心文件 | 关键行号 |
|-----------|----------|---------|
| 词权重 IDF×NER×POS | `rag/nlp/term_weight.py` | L164-L247 |
| 同义词三级查找 | `rag/nlp/synonym.py` | L78-L103 |
| Redis 动态加载 | `rag/nlp/synonym.py` | L56-L75 |
| 细粒度分词过滤 | `rag/nlp/query.py` | L94-L117 |
| JSON DSL 反序列化 | `agent/canvas.py` | L94-L130 |
| 5路并行+依赖解析 | `agent/canvas.py` | L435-L482 |
| 路径推进状态机 | `agent/canvas.py` | L599-L627 |
| Tool Call 三种适配 | `agent/tools/base.py` | L58-L73 |
| Agent 工具绑定 | `agent/component/agent_with_tools.py` | L76-L110 |
| 7种事件系统 | `agent/canvas.py` | L375-L667 |
| Message 流式输出 | `agent/canvas.py` | L516-L568 |
| PUA字符乱码检测 | `deepdoc/parser/pdf_parser.py` | L205-L230 |
| CID模式检测 | `deepdoc/parser/pdf_parser.py` | L202, L233-L254 |
| 字体编码检测 | `deepdoc/parser/pdf_parser.py` | L257-L300 |
| 5种PDF引擎切换 | `rag/app/naive.py` | L254-L261 |
