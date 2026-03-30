# RAGFlow NLP模块深度解析报告

## 一、核心总览（带逻辑关系）

### 核心定位

`rag/nlp` 目录是RAGFlow项目的自然语言处理核心模块,承担着查询理解、文本分词、语义检索、相关性计算等关键任务。该模块通过6个Python文件协同工作,构建了一个完整的NLP处理流水线:

- **query.py**: 全文查询构建器,将用户问题转换为搜索引擎可理解的查询表达式
- **rag_tokenizer.py**: 分词器封装层,提供中英文分词能力
- **search.py**: 搜索引擎核心,实现向量检索、全文检索、混合检索和重排序
- **term_weight.py**: 词权重计算器,基于TF-IDF和NER实现关键词权重评估
- **synonym.py**: 同义词扩展器,支持自定义词典和WordNet同义词查询
- **surname.py**: 中文姓氏数据集,用于中文姓名识别

该模块解决的核心业务问题是:**如何将用户的自然语言问题,转化为高质量的检索查询,并从海量文档中召回最相关的知识片段**。

### 整体流程串讲

**完整执行链路**:
```
用户问题输入 
  ↓
[query.py] FulltextQueryer.question() 
  → 文本预处理(中英文识别、特殊字符清理)
  → [rag_tokenizer.py] 分词处理
  → [term_weight.py] 词权重计算
  → [synonym.py] 同义词扩展
  → 构建MatchTextExpr查询表达式
  ↓
[search.py] Dealer.search()
  → 向量化查询
  → 混合检索(向量+全文)
  → 数据库查询
  ↓
[search.py] Dealer.rerank()
  → [query.py] hybrid_similarity() 混合相似度计算
  → 词相似度 + 向量相似度加权融合
  → 排序返回TopK结果
```

**关键底层依赖**:
- `infinity.rag_tokenizer`: 底层C++分词器实现
- `sklearn.metrics.pairwise.cosine_similarity`: 向量相似度计算
- `nltk.corpus.wordnet`: 英文同义词库
- `DocStoreConnection`: 数据存储抽象层

---

## 二、模块拆分（固定顺序 + 关系说明）

### 2.1 初始化模块

#### **query.py - FulltextQueryer.__init__()**
```python
def __init__(self):
    self.tw = term_weight.Dealer()      # 词权重计算器
    self.syn = synonym.Dealer()         # 同义词查询器
    self.query_fields = [...]           # ES查询字段权重配置
```
**作用**: 初始化查询构建所需的核心组件,定义查询字段权重映射(如title_tks权重10,content_ltks权重2)

---

#### **search.py - Dealer.__init__()**
```python
def __init__(self, dataStore: DocStoreConnection):
    self.qryr = query.FulltextQueryer()  # 查询构建器
    self.dataStore = dataStore           # 数据存储连接
```
**作用**: 初始化搜索引擎核心,注入数据存储依赖

---

#### **term_weight.py - Dealer.__init__()**
```python
def __init__(self):
    self.stop_words = set([...])         # 停用词表
    self.ne = {}                         # NER词典
    self.df = {}                         # 文档频率词典
    # 从rag/res/ner.json和term.freq加载
```
**作用**: 加载停用词、命名实体识别词典、词频统计词典

---

#### **synonym.py - Dealer.__init__()**
```python
def __init__(self, redis=None):
    self.dictionary = {}                 # 同义词词典
    self.redis = redis                   # Redis连接(可选)
    # 从rag/res/synonym.json加载
```
**作用**: 加载自定义同义词词典,支持Redis动态更新

---

### 2.2 核心入口方法模块

#### **search.py - Dealer.retrieval()** ⭐主入口
**定位**: RAG检索的主入口方法,协调整个检索流程

**调用关系**:
```
retrieval() 
  → search()           # 执行检索
  → rerank()           # 重排序
  → 返回分页结果
```

---

#### **query.py - FulltextQueryer.question()** ⭐查询构建核心
**定位**: 将用户问题转换为搜索引擎查询表达式

**调用关系**:
```
question()
  → rag_tokenizer.tokenize()           # 分词
  → term_weight.Dealer.weights()       # 权重计算
  → synonym.Dealer.lookup()            # 同义词扩展
  → 返回MatchTextExpr
```

---

### 2.3 分支逻辑方法模块

#### **search.py - Dealer.search()**
**分支逻辑**:
- **分支1**: 无问题查询 → 直接按排序字段返回
- **分支2**: 有问题 + 无向量模型 → 纯全文检索
- **分支3**: 有问题 + 有向量模型 → 混合检索(向量+全文)

---

#### **query.py - FulltextQueryer.question()**
**分支逻辑**:
- **分支1**: 非中文查询 → 英文处理流程
- **分支2**: 中文查询 → 中文处理流程(含细粒度分词)

---

### 2.4 具体实现方法模块

#### **search.py - Dealer.rerank()**
**实现**: 计算混合相似度 = 词相似度×tkweight + 向量相似度×vtweight + 排序特征分数

---

#### **query.py - FulltextQueryer.hybrid_similarity()**
**实现**: 融合向量相似度和词相似度

---

#### **term_weight.py - Dealer.weights()**
**实现**: 基于IDF、NER、词性标注计算词权重

---

### 2.5 辅助方法模块

- **query.py**: `token_similarity()`, `similarity()`, `paragraph()`
- **search.py**: `get_filters()`, `get_vector()`, `_rank_feature_scores()`
- **term_weight.py**: `pretoken()`, `token_merge()`, `split()`
- **synonym.py**: `load()`, `lookup()`
- **rag_tokenizer.py**: `tokenize()`, `fine_grained_tokenize()`
- **surname.py**: `isit()` - 判断是否为姓氏

---

## 三、方法详细解析（强制5要素 + 文字流程串讲）

### 3.1 search.py - Dealer.retrieval() ⭐⭐⭐

#### 方法文字流程串讲
这是RAG检索的顶层协调方法。首先校验问题非空,然后计算重排序限制(RERANK_LIMIT),构建检索请求参数。调用search()执行检索后,根据是否提供重排序模型选择重排序策略:有模型则调用rerank_by_model(),无模型则调用rerank()。最后对相似度分数排序,应用阈值过滤,分页返回结果。

#### 强制5要素

**1. 入参**:
- `question`: 用户问题字符串
- `embd_mdl`: 嵌入模型(用于向量化)
- `tenant_ids`: 租户ID列表
- `kb_ids`: 知识库ID列表
- `page`, `page_size`: 分页参数
- `similarity_threshold`: 相似度阈值(默认0.2)
- `vector_similarity_weight`: 向量权重(默认0.3)
- `rerank_mdl`: 重排序模型(可选)

**2. 核心逻辑**:
```python
# 1. 构建检索请求
req = {
    "kb_ids": kb_ids,
    "question": question,
    "topk": top,
    "similarity": similarity_threshold,
}

# 2. 执行检索
sres = await self.search(req, ...)

# 3. 重排序
if rerank_mdl:
    sim, tsim, vsim = self.rerank_by_model(...)
else:
    sim, tsim, vsim = self.rerank(...)

# 4. 排序过滤
sorted_idx = np.argsort(sim_np * -1)
valid_idx = [i for i in sorted_idx if sim_np[i] >= threshold]
```

**3. 输出形式**:
```python
{
    "total": 10,              # 符合条件的结果数
    "chunks": [               # 知识片段列表
        {
            "chunk_id": "xxx",
            "content_with_weight": "内容...",
            "similarity": 0.85,
            "vector_similarity": 0.9,
            "term_similarity": 0.7,
            ...
        }
    ],
    "doc_aggs": [...]        # 文档聚合统计
}
```

**4. 底层关键依赖**:
- `self.search()`: 执行检索
- `self.rerank()` / `self.rerank_by_model()`: 重排序
- `numpy`: 排序和数组操作

**5. 关键代码片段**:
```python
sim_np = np.array(sim, dtype=np.float64)
sorted_idx = np.argsort(sim_np * -1)

# 当vector_similarity_weight为0时,相似度阈值对纯词相似度无意义
post_threshold = 0.0 if vector_similarity_weight <= 0 else similarity_threshold

# 当明确提供doc_ids时(元数据或文档过滤),绕过阈值
if doc_ids:
    post_threshold = 0.0

valid_idx = [int(i) for i in sorted_idx if sim_np[i] >= post_threshold]
```

**特殊处理标注**:
- RERANK_LIMIT动态计算确保是page_size的倍数
- 当doc_ids明确指定时,绕过相似度阈值(用户明确要这些文档)
- 支持Infinity引擎的特殊处理(无需重排序)

---

### 3.2 query.py - FulltextQueryer.question() ⭐⭐⭐

#### 方法文字流程串讲
这是查询构建的核心方法。首先对输入文本进行预处理(添加中英文空格、清理特殊字符、繁简转换、全角转半角)。然后判断是否为中文:如果是英文,直接分词后计算权重,扩展同义词,构建查询表达式;如果是中文,则按句子切分后,对每个句子进行细粒度分词、权重计算、同义词扩展,最后用OR连接所有句子的查询表达式。

#### 强制5要素

**1. 入参**:
- `txt`: 用户问题字符串
- `tbl`: 表名(默认"qa")
- `min_match`: 最小匹配度(默认0.6)

**2. 核心逻辑**:
```python
# 1. 文本预处理
txt = self.add_space_between_eng_zh(txt)
txt = re.sub(r"[ :|\r\n\t,，。？?/`!！&^%%()\[\]{}<>*~'\"\\]+", " ", 
             rag_tokenizer.tradi2simp(rag_tokenizer.strQ2B(txt.lower()))).strip()

# 2. 判断中英文
if not self.is_chinese(txt):
    # 英文处理流程
    tks = rag_tokenizer.tokenize(txt).split()
    tks_w = self.tw.weights(tks, preprocess=False)
    # 构建查询表达式...
else:
    # 中文处理流程
    for tt in self.tw.split(txt):
        twts = self.tw.weights([tt])
        # 细粒度分词、同义词扩展...
```

**3. 输出形式**:
```python
# 返回元组
(
    MatchTextExpr(  # 查询表达式对象
        fields=["title_tks^10", "content_ltks^2", ...],
        query="(机器^0.85 OR 学习^0.92) OR (深度^0.88 OR 学习^0.92)",
        topn=100,
        extra_options={"minimum_should_match": 0.6}
    ),
    ["机器", "学习", "深度"]  # 关键词列表
)
```

**4. 底层关键依赖**:
- `rag_tokenizer.tokenize()`: 分词
- `rag_tokenizer.fine_grained_tokenize()`: 细粒度分词
- `term_weight.Dealer.weights()`: 词权重计算
- `synonym.Dealer.lookup()`: 同义词查询

**5. 关键代码片段**:
```python
def need_fine_grained_tokenize(tk):
    if len(tk) < 3:
        return False
    if re.match(r"[0-9a-z\.\+#_\*-]+$", tk):
        return False
    return True

# 中文查询处理
for tt in self.tw.split(txt)[:256]:
    twts = self.tw.weights([tt])
    syns = self.syn.lookup(tt)
    for tk, w in sorted(twts, key=lambda x: x[1] * -1):
        sm = rag_tokenizer.fine_grained_tokenize(tk).split() if need_fine_grained_tokenize(tk) else []
        # 构建查询表达式...
```

**特殊处理标注**:
- 限制最多处理256个token,防止查询过长
- 细粒度分词只对长度≥3且非纯数字字母的token执行
- 同义词扩展最多32个关键词
- 需要转义Infinity的特殊字符: `[\x20()^"'~*?:\\]`

---

## 四、同类逻辑对比表

### 4.1 检索策略对比

| 检索类型 | 触发条件 | 查询表达式 | 向量查询 | 融合方式 | 适用场景 |
|---------|---------|-----------|---------|---------|---------|
| **纯全文检索** | `emb_mdl is None` | MatchTextExpr | 无 | 无 | 无向量模型场景 |
| **混合检索** | `emb_mdl is not None` | MatchTextExpr + MatchDenseExpr | 有 | FusionExpr(weighted_sum) | 标准RAG场景 |
| **无问题检索** | `question is empty` | 无 | 无 | 无 | 文档列表浏览 |
| **降级重试** | `total == 0` | MatchTextExpr(min_match=0.1) | MatchDense(similarity=0.17) | FusionExpr | 检索结果为空时 |

---

### 4.2 相似度计算对比

| 方法 | 输入 | 计算方式 | 权重配置 | 输出维度 |
|-----|------|---------|---------|---------|
| **hybrid_similarity** | 向量+词列表 | 向量余弦相似度 + 词相似度 | tkweight=0.3, vtweight=0.7 | 混合分数、词分数、向量分数 |
| **token_similarity** | 词列表 | 词重叠率(考虑权重) | 无 | 相似度列表 |
| **similarity** | 词权重字典 | 词交集权重和 / 查询词权重和 | 无 | 单个相似度值 |

---

### 4.3 分词策略对比

| 方法 | 粒度 | 中文处理 | 英文处理 | 使用场景 |
|-----|------|---------|---------|---------|
| **tokenize** | 粗粒度 | "机器学习" → "机器 学习" | "machine learning" → "machine learning" | 查询构建、索引 |
| **fine_grained_tokenize** | 细粒度 | "人工智能" → "人工 智能" | 不适用 | 中文关键词扩展 |
| **naive_qie** | 最粗粒度 | 按字符切分 | 不适用 | 极简分词 |

---

### 4.4 权重计算因子对比

| 因子 | 数据来源 | 权重范围 | 影响因素 |
|-----|---------|---------|---------|
| **IDF(词频)** | rag_tokenizer.freq() | log10(10 + (N-f+0.5)/(f+0.5)) | 词在语料中的频率 |
| **IDF(文档频率)** | term.freq文件 | log10(10 + (N-df+0.5)/(df+0.5)) | 词在文档中的出现频率 |
| **NER类型** | ner.json | 0.01-3 | toxic(2), corp(3), loca(3)等 |
| **词性标注** | rag_tokenizer.tag() | 0.3-3 | 代词(0.3), 名词(2), 地名(3) |

---

### 4.5 同义词来源对比

| 来源 | 数据位置 | 更新方式 | 语言支持 | 优先级 |
|-----|---------|---------|---------|-------|
| **自定义词典** | rag/res/synonym.json | 手动编辑 | 中英文 | 最高 |
| **Redis动态词典** | Redis "kevin_synonyms" | 实时更新 | 中英文 | 高 |
| **WordNet** | nltk.corpus.wordnet | 自动 | 仅英文 | 低 |

---

## 五、疑惑解答

### Q1: 为什么混合检索权重是"0.05,0.95"(全文:向量)?
**答**: 这个权重配置表示向量检索占主导地位(95%),全文检索仅占5%。原因是:
1. 向量检索能捕捉语义相似度,效果通常优于字面匹配
2. 全文检索主要用于召回包含特定关键词的文档,作为补充
3. 实际效果可能因数据集而异,可通过参数调整

---

### Q2: 为什么中文查询需要细粒度分词?
**答**: 中文分词存在歧义问题,例如"人工智能"可以切分为:
- 粗粒度: ["人工智能"]
- 细粒度: ["人工", "智能"]

细粒度分词能提高召回率,确保查询"人工智能"时也能匹配到只包含"人工"或"智能"的文档。

---

### Q3: 为什么空结果时要降低min_match和提高similarity?
**答**: 
- **降低min_match**(0.3→0.1): 减少必须匹配的词数,放宽全文检索条件
- **提高similarity**(0.1→0.17): 提高向量相似度阈值,筛选更相关的结果

这是一种"先宽后严"的策略,先放宽全文条件召回更多候选,再用向量相似度过滤。

---

### Q4: 为什么Infinity引擎不需要分词?
**答**: Infinity是RAGFlow自研的搜索引擎,内置了分词能力。当使用Infinity时,分词工作由引擎内部完成,Python层无需预处理,直接传递原文即可。这样可以:
1. 减少Python层计算开销
2. 利用C++引擎的高性能分词
3. 保持索引和查询的一致性

---

### Q5: 词权重计算为什么要归一化?
**答**: 归一化(权重总和为1)的好处:
1. 不同查询的权重可比性
2. 便于设置阈值(如相似度0.2)
3. 避免长查询权重过大

---

## 六、规范修正

### 6.1 代码规范问题

#### 问题1: 循环导入风险
**位置**: rag_tokenizer.py
```python
# 当前实现
def tokenize(self, line: str) -> str:
    from common import settings  # 延迟导入避免循环依赖
```
**建议**: 虽然延迟导入解决了循环依赖,但更好的方式是通过依赖注入传递settings,或重构模块结构。

---

#### 问题2: 硬编码的魔法数字
**位置**: search.py
```python
fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
```
**建议**: 将权重配置提取为常量或配置项:
```python
DEFAULT_FUSION_WEIGHTS = "0.05,0.95"
```

---

#### 问题3: 异常处理不完整
**位置**: synonym.py
```python
try:
    wordnet.ensure_loaded()
except Exception:
    logging.warning("Fail to load wordnet.ensure_loaded()")
```
**建议**: 捕获具体异常类型,避免隐藏其他错误:
```python
except (OSError, IOError) as e:
    logging.warning(f"Fail to load wordnet: {e}")
```

---

### 6.2 性能优化建议

#### 优化1: 缓存词权重计算结果
**位置**: term_weight.py
**建议**: 对高频查询的词权重结果进行LRU缓存:
```python
from functools import lru_cache

@lru_cache(maxsize=10000)
def weights(self, tks_tuple, preprocess=True):
    tks = list(tks_tuple)
    # 原有逻辑...
```

---

#### 优化2: 批量向量查询
**位置**: search.py
**建议**: 支持批量查询向量化,减少模型调用次数:
```python
async def get_vectors(self, txts, emb_mdl, topk=10, similarity=0.1):
    qvs, _ = await thread_pool_exec(emb_mdl.encode_queries, txts)
    # 批量处理...
```

---

## 七、可复现实操步骤

### 7.1 环境准备

```bash
# 1. 克隆项目
git clone https://github.com/infiniflow/ragflow.git
cd ragflow

# 2. 安装依赖
uv sync --python 3.12 --all-extras
uv run download_deps.py

# 3. 启动基础服务
docker compose -f docker/docker-compose-base.yml up -d

# 4. 激活虚拟环境
source .venv/bin/activate
export PYTHONPATH=$(pwd)
```

---

### 7.2 测试分词功能

```python
# test_tokenizer.py
from rag.nlp import rag_tokenizer

# 测试粗粒度分词
text = "机器学习是人工智能的分支"
result = rag_tokenizer.tokenize(text)
print(f"粗粒度分词: {result}")
# 输出: "机器 学习 是 人工智能 的 分支"

# 测试细粒度分词
fine_result = rag_tokenizer.fine_grained_tokenize("人工智能")
print(f"细粒度分词: {fine_result}")
# 输出: "人工 智能"

# 测试词性标注
tag = rag_tokenizer.tag("机器")
print(f"词性: {tag}")
# 输出: "n" (名词)

# 测试词频
freq = rag_tokenizer.freq("机器")
print(f"词频: {freq}")
```

---

### 7.3 测试词权重计算

```python
# test_term_weight.py
from rag.nlp import term_weight

tw = term_weight.Dealer()

# 测试权重计算
tks = ["机器学习", "深度", "学习"]
weights = tw.weights(tks, preprocess=False)
print("词权重:")
for token, weight in weights:
    print(f"  {token}: {weight:.4f}")

# 测试预处理
text = "请问机器学习是什么?"
tokens = tw.pretoken(text)
print(f"\n预处理后: {tokens}")
# 输出: ['机器学习', '什么']

# 测试分词合并
tks = ["机", "器", "学", "习"]
merged = tw.token_merge(tks)
print(f"\n合并后: {merged}")
# 输出: ['机 器 学 习']
```

---

### 7.4 测试同义词查询

```python
# test_synonym.py
from rag.nlp import synonym

syn = synonym.Dealer()

# 测试英文同义词
synonyms = syn.lookup("happy")
print(f"happy的同义词: {synonyms}")
# 输出: ['felicitous', 'glad', 'cheerful', ...]

# 测试中文同义词(需在synonym.json中配置)
synonyms = syn.lookup("机器学习")
print(f"机器学习的同义词: {synonyms}")
# 输出: ['machine learning', 'ML'] (如果在词典中)
```

---

### 7.5 测试查询构建

```python
# test_query.py
from rag.nlp import query

qryr = query.FulltextQueryer()

# 测试英文查询
match_expr, keywords = qryr.question("What is machine learning?")
print(f"英文查询表达式: {match_expr.query}")
print(f"关键词: {keywords}")

# 测试中文查询
match_expr, keywords = qryr.question("什么是机器学习?")
print(f"\n中文查询表达式: {match_expr.query}")
print(f"关键词: {keywords}")
```

---

## 八、关键模块总览

### 8.1 模块依赖关系图

```
┌─────────────────────────────────────────────────────────┐
│                    API Layer (api/)                      │
│                   用户请求入口                            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              search.py - Dealer                          │
│           检索协调器(主入口: retrieval)                  │
│  ┌──────────────────────────────────────────────────┐  │
│  │  search() → rerank() → 返回结果                   │  │
│  └──────────────────────────────────────────────────┘  │
└─────┬───────────────────────────────────────────────────┘
      │
      ├─────────────────────┬──────────────────────────┐
      │                     │                          │
      ▼                     ▼                          ▼
┌──────────────┐   ┌────────────────┐   ┌────────────────────┐
│ query.py     │   │ term_weight.py │   │ synonym.py         │
│ FulltextQueryer│ │ Dealer         │   │ Dealer             │
│              │   │                │   │                    │
│ question()   │──▶│ weights()      │   │ lookup()           │
│ hybrid_sim() │   │ pretoken()     │   │                    │
└──────┬───────┘   └───────┬────────┘   └────────────────────┘
       │                   │
       │                   │
       └─────────┬─────────┘
                 │
                 ▼
       ┌──────────────────────┐
       │ rag_tokenizer.py     │
       │ RagTokenizer         │
       │                      │
       │ tokenize()           │
       │ fine_grained_tokenize│
       │ tag() / freq()       │
       └──────────┬───────────┘
                  │
                  ▼
       ┌──────────────────────┐
       │ infinity.rag_tokenizer│
       │ (C++ Implementation)  │
       └──────────────────────┘

       ┌──────────────────────┐
       │ surname.py           │
       │ 姓氏数据集(独立模块) │
       └──────────────────────┘
```

---

### 8.2 核心数据流

```
用户问题: "什么是机器学习?"
    ↓
[预处理] 清理特殊字符、繁简转换、全角转半角
    ↓
[分词] "什么 是 机器 学习"
    ↓
[权重计算] 
    - 什么: 0.05 (停用词降权)
    - 机器: 0.35 (名词、高频)
    - 学习: 0.30 (名词、高频)
    ↓
[同义词扩展]
    - 机器: ["machine"]
    - 学习: ["learn", "study"]
    ↓
[查询表达式构建]
    "(什么^0.05 OR 机器^0.35 OR 学习^0.30) OR (machine^0.08) OR (learn^0.07)"
    ↓
[向量检索] 向量化问题 → 余弦相似度匹配
    ↓
[混合检索] FusionExpr(weighted_sum, weights="0.05,0.95")
    ↓
[重排序] hybrid_similarity = 0.7×向量相似度 + 0.3×词相似度
    ↓
[返回结果] TopK文档片段
```

---

### 8.3 关键配置项

| 配置项 | 位置 | 默认值 | 说明 |
|-------|------|-------|------|
| **查询字段权重** | query.py | title_tks^10, content_ltks^2 | ES字段权重映射 |
| **混合检索权重** | search.py | "0.05,0.95" | 全文:向量权重比 |
| **相似度权重** | query.py | tkweight=0.3, vtweight=0.7 | 词:向量相似度权重 |
| **最小匹配度** | search.py | min_match=0.3 | 全文检索最小匹配比例 |
| **相似度阈值** | search.py | similarity_threshold=0.2 | 结果过滤阈值 |
| **重排序限制** | search.py | RERANK_LIMIT=64 | 重排序候选数量 |

---

### 8.4 文件职责总结

| 文件 | 核心类/函数 | 主要职责 | 代码行数 |
|-----|-----------|---------|---------|
| **query.py** | FulltextQueryer | 查询表达式构建、相似度计算 | 243行 |
| **search.py** | Dealer | 检索协调、重排序、结果聚合 | 716行 |
| **term_weight.py** | Dealer | 词权重计算、文本预处理 | 247行 |
| **synonym.py** | Dealer | 同义词查询、词典管理 | 108行 |
| **rag_tokenizer.py** | RagTokenizer | 分词器封装 | 57行 |
| **surname.py** | isit() | 姓氏判断 | 144行 |

---

### 8.5 技术栈总结

- **分词**: infinity.rag_tokenizer (C++实现)
- **词性标注**: rag_tokenizer.tag()
- **词频统计**: rag_tokenizer.freq()
- **同义词**: nltk.corpus.wordnet + 自定义词典
- **向量相似度**: sklearn.metrics.pairwise.cosine_similarity
- **数据存储**: DocStoreConnection抽象层
- **异步执行**: thread_pool_exec线程池

---

## 总结

`rag/nlp` 模块是RAGFlow项目的NLP核心,通过6个Python文件协同工作,实现了从用户问题到检索结果的完整链路。核心亮点包括:

1. **智能查询构建**: 自动识别中英文,应用不同的分词和扩展策略
2. **混合检索**: 融合向量检索和全文检索,提高召回率和准确率
3. **动态权重**: 基于IDF、NER、词性标注计算词权重,提升关键词识别能力
4. **同义词扩展**: 支持自定义词典和WordNet,增强查询语义理解
5. **灵活配置**: 支持多种检索策略和参数调优

该模块设计合理,职责清晰,是RAGFlow实现高质量检索的关键基础。
