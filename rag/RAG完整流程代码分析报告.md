# RAGFlow 完整RAG流程代码分析报告

## 概述

本文档详细分析RAGFlow项目中RAG（检索增强生成）的16个核心流程的实现代码，包括每个流程对应的核心文件、关键函数和重要代码片段。

---

## 流程总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           RAG 完整流程架构                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  │ 1.文档采集   │───▶│ 2.文本读取   │───▶│ 3.结构化提取 │───▶│ 4.智能分块   │
│  │    校验      │    │    清洗      │    │              │    │              │
│  └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
│                                                                      │
│                                                                      ▼
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  │ 16.知识库    │◀───│ 15.兜底与    │◀───│ 14.答案校验  │◀───│ 5.Embedding  │
│  │ 增量维护     │    │    埋点      │    │    溯源      │    │   向量化     │
│  └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
│         ▲                                                              │
│         │                                                              ▼
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  │ 13.LLM答案   │◀───│ 12.Prompt    │◀───│ 11.上下文    │◀───│ 6.向量库     │
│  │    生成      │    │    模板      │    │    拼接      │    │    入库      │
│  └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
│         ▲                                                              │
│         │                                                              ▼
│         │         ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│         └─────────│ 10.Rerank    │◀───│ 9.结果过滤   │◀───│ 7.Query      │
│                   │   重排序     │    │    去重      │    │   预处理     │
│                   └──────────────┘    └──────────────┘    └──────────────┘
│                                              ▲                         │
│                                              │                         ▼
│                                              └─────────────────────────┤
│                                                                        ▼
│                                                                  ┌──────────────┐
│                                                                  │ 8.混合检索   │
│                                                                  │              │
│                                                                  └──────────────┘
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 流程1：文档采集校验

### 核心功能
- 批量遍历文档
- 校验存在/权限/损坏
- 返回结构化文件信息

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/svr/task_executor.py` | 任务调度、文档校验、分块构建 |
| `rag/utils/file_utils.py` | 文件格式识别、嵌入文件提取 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `collect()` | 从Redis队列获取任务，校验任务存在性 |
| `build_chunks()` | 构建文档分块，校验文件大小、存储地址 |
| `extract_embed_file()` | 提取嵌入文件，支持ZIP/OLE容器 |
| `_guess_ext()` | 通过文件头识别文件类型 |

### 核心代码

```python
# task_executor.py - 任务采集与校验
async def collect():
    """从Redis队列获取任务并进行校验"""
    redis_msg = REDIS_CONN.queue_consumer(svr_queue_name, SVR_CONSUMER_GROUP_NAME, CONSUMER_NAME)
    if not redis_msg:
        return None, None
    
    msg = redis_msg.get_message()
    task = TaskService.get_task(msg["id"])
    
    # 校验任务是否存在或已取消
    if task:
        canceled = has_canceled(task["id"])
    if not task or canceled:
        FAILED_TASKS += 1
        redis_msg.ack()
        return None, None
    
    return redis_msg, task

# task_executor.py - 文档分块构建与校验
@timeout(60 * 80, 1)
async def build_chunks(task, progress_callback):
    # 校验文件大小
    if task["size"] > settings.DOC_MAXIMUM_SIZE:
        set_progress(task["id"], prog=-1, 
                    msg="File size exceeds( <= %dMb )" % (int(settings.DOC_MAXIMUM_SIZE / 1024 / 1024)))
        return []
    
    # 获取存储地址
    bucket, name = File2DocumentService.get_storage_address(doc_id=task["doc_id"])
    
    try:
        binary = await get_storage_binary(bucket, name)
    except Exception as e:
        # 校验文件是否存在
        if re.search("(No such file|not found)", str(e)):
            progress_callback(-1, "Can not find file from minio.")
        raise
    
    # 调用解析器进行分块
    chunker = FACTORY[task["parser_id"].lower()]
    cks = await thread_pool_exec(chunker.chunk, task["name"], binary=binary)
    return docs

# file_utils.py - 文件格式识别
def _guess_ext(b: bytes) -> str:
    """通过文件头识别文件类型"""
    h = b[:8]
    if _is_zip(h):
        try:
            with zipfile.ZipFile(io.BytesIO(b), "r") as z:
                names = [n.lower() for n in z.namelist()]
                if any(n.startswith("word/") for n in names):
                    return ".docx"
                if any(n.startswith("ppt/") for n in names):
                    return ".pptx"
                if any(n.startswith("xl/") for n in names):
                    return ".xlsx"
        except Exception:
            pass
        return ".zip"
    if _is_pdf(h):
        return ".pdf"
    return ".bin"
```

---

## 流程2：文本读取清洗

### 核心功能
- 兼容PDF/Word/TXT/MD格式
- 去页眉页脚/乱码/冗余换行
- 修复断句，编码自适应

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/app/naive.py` | 统一解析入口，支持多种格式 |
| `deepdoc/parser/pdf_parser.py` | PDF OCR、布局分析、乱码检测 |
| `rag/nlp/__init__.py` | 编码检测、文本合并、分词 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `chunk()` | 统一解析入口，支持PDF/Word/TXT/MD等格式 |
| `find_codec()` | 编码自适应检测 |
| `naive_merge()` | 文本合并分块 |
| `_is_garbled_text()` | 乱码检测 |

### 核心代码

```python
# naive.py - 统一解析入口
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs):
    """支持的文件格式: docx, pdf, excel, txt, markdown, html, epub, json"""
    parser_config = kwargs.get("parser_config", {
        "chunk_token_num": 512, 
        "delimiter": "\n!?。；！？", 
        "layout_recognize": "DeepDOC"
    })
    
    # PDF文件处理
    if re.search(r"\.pdf$", filename, re.IGNORECASE):
        parser = PARSERS.get(layout_recognizer.strip().lower(), by_plaintext)
        sections, tables, pdf_parser = parser(filename=filename, binary=binary, ...)
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        
    # DOCX文件处理
    elif re.search(r"\.docx$", filename, re.IGNORECASE):
        sections = Docx()(filename, binary)
        
    # TXT/代码文件处理
    elif re.search(r"\.(txt|py|js|java|c|cpp|h|php|go|ts|sh|cs|kt|sql)$", filename, re.IGNORECASE):
        sections = TxtParser()(filename, binary, ...)
    
    # 文本合并分块
    chunks = naive_merge(sections, int(parser_config.get("chunk_token_num", 128)),
                        parser_config.get("delimiter", "\n!?。；！？"), overlapped_percent)
    return res

# rag/nlp/__init__.py - 编码自适应检测
def find_codec(blob):
    """自动检测文件编码，支持多种编码格式"""
    detected = chardet.detect(blob[:1024])
    if detected['confidence'] > 0.5:
        if detected['encoding'] == "ascii":
            return "utf-8"
    
    # 遍历所有支持的编码进行尝试
    for c in all_codecs:  # 支持50+种编码
        try:
            blob[:1024].decode(c)
            return c
        except Exception:
            pass
    return "utf-8"

# pdf_parser.py - 乱码检测
@staticmethod
def _is_garbled_text(text, threshold=0.5):
    """检测文本是否包含过多乱码字符"""
    if RAGFlowPdfParser._CID_PATTERN.search(text):
        return True
    
    garbled_count = 0
    total = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        if RAGFlowPdfParser._is_garbled_char(ch):
            garbled_count += 1
    
    # 乱码比例超过阈值则判定为乱码文本
    return garbled_count / total >= threshold
```

---

## 流程3：结构化提取

### 核心功能
- 抽取表格、多级标题、图片
- 关联原文位置输出结构化数据

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `deepdoc/parser/pdf_parser.py` | PDF深度解析核心，提取表格、图片、标题 |
| `deepdoc/vision/table_structure_recognizer.py` | 表格结构识别，输出HTML/文本格式 |
| `deepdoc/vision/layout_recognizer.py` | 布局识别，区分标题、文本、表格、图片 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `RAGFlowPdfParser` | PDF解析主类，整合OCR、布局、表格识别 |
| `_extract_table_figure()` | 提取表格和图片，关联位置信息 |
| `TableStructureRecognizer` | 表格结构识别，输出HTML |
| `_line_tag()` | 生成位置标签，关联原文位置 |

### 核心代码

```python
# pdf_parser.py - 表格和图片提取
def _extract_table_figure(self, need_image, ZM, return_html, need_position):
    tables = {}
    figures = {}
    # 遍历所有box，识别表格和图片布局
    while i < len(self.boxes):
        if self.boxes[i]["layout_type"] == "table":
            tables[lout_no].append(self.boxes[i])
        if need_image and self.boxes[i]["layout_type"] == "figure":
            figures[lout_no].append(self.boxes[i])
    
    # 为表格/图片查找最近的标题
    for i in range(len(self.boxes)):
        if TableStructureRecognizer.is_caption(self.boxes[i]):
            tables[tk].insert(0, c)
    
    # 裁剪图片并生成位置信息
    for k, bxs in tables.items():
        img = cropout(bxs, "table", poss)  # 裁剪表格图片
        res.append((img, self.tbl_det.construct_table(bxs, html=return_html)))
        positions.append(poss)  # 记录位置：[页码, left, right, top, bottom]

# table_structure_recognizer.py - 表格结构识别
@staticmethod
def construct_table(boxes, is_english=False, html=True):
    # 识别行和列
    rows = [[boxes[0]]]
    for b in boxes[1:]:
        if b["top"] >= btm - 3:  # 新行
            rows.append([b])
        else:
            rows[-1].append(b)
    
    # 构建HTML表格
    html = "<table>"
    if cap:
        html += f"<caption>{cap}</caption>"
    for i in range(len(tbl)):
        row = "<tr>"
        for j, arr in enumerate(tbl[i]):
            if i in hdset:  # 表头行
                row += f"<th>{txt}</th>"
            else:
                row += f"<td>{txt}</td>"
        html += "\n" + row
    html += "\n</table>"
    return html

# pdf_parser.py - 位置标签生成
def _line_tag(self, bx, ZM):
    """生成位置标签，格式：@@页码\tx0\tx1\ttop\tbottom##"""
    pn = [bx["page_number"]]
    top = bx["top"] - self.page_cum_height[pn[0] - 1]
    bott = bx["bottom"] - self.page_cum_height[pn[0] - 1]
    
    return "@@{}\t{:.1f}\t{:.1f}\t{:.1f}\t{:.1f}##".format(
        "-".join([str(p) for p in pn]), bx["x0"], bx["x1"], top, bott
    )
```

---

## 流程4：智能分块

### 核心功能
- 语义/标题切分
- 块长512字符、重叠50-200字符
- 附加元数据，过滤空/短块

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/flow/splitter/splitter.py` | 分块组件，处理512字符块长 |
| `rag/nlp/__init__.py` | NLP处理，包含分块算法 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `Splitter` | 分块组件主类 |
| `SplitterParam` | 分块参数配置（块长512、重叠等） |
| `naive_merge()` | 基础分块算法 |
| `naive_merge_with_images()` | 带图片的分块算法 |
| `tokenize_chunks()` | 分块后附加元数据 |

### 核心代码

```python
# splitter.py - 分块参数配置
class SplitterParam(ProcessParamBase):
    def __init__(self):
        super().__init__()
        self.chunk_token_size = 512      # 块长512字符
        self.delimiters = ["\n"]          # 分隔符
        self.overlapped_percent = 0       # 重叠百分比（0-100）
        self.table_context_size = 0       # 表格上下文大小
        self.image_context_size = 0       # 图片上下文大小

# rag/nlp/__init__.py - 智能分块核心算法
def naive_merge(sections, chunk_token_num=128, delimiter="\n。；！？", overlapped_percent=0):
    """智能分块算法"""
    cks = [""]
    tk_nums = [0]

    def add_chunk(t, pos):
        """添加块，处理重叠"""
        tnum = num_tokens_from_string(t)
        if tnum < 8:
            pos = ""  # 过滤短块
        
        # 当块超过阈值时，创建新块并添加重叠
        if cks[-1] == "" or tk_nums[-1] > chunk_token_num * (100 - overlapped_percent) / 100.:
            if cks:
                # 计算重叠部分
                overlapped = RAGFlowPdfParser.remove_tag(cks[-1])
                t = overlapped[int(len(overlapped) * (100 - overlapped_percent) / 100.):] + t
            cks.append(t)
            tk_nums.append(tnum)
        else:
            cks[-1] += t
            tk_nums[-1] += tnum

    for sec, pos in sections:
        add_chunk("\n" + sec, pos)
    return cks

# rag/nlp/__init__.py - 分块后附加元数据
def tokenize_chunks(chunks, doc, eng, pdf_parser=None):
    """为分块附加元数据"""
    res = []
    for ii, ck in enumerate(chunks):
        if len(ck.strip()) == 0:
            continue  # 过滤空块
        
        d = copy.deepcopy(doc)
        if pdf_parser:
            d["image"], poss = pdf_parser.crop(ck, need_position=True)
            add_positions(d, poss)  # 添加位置元数据
        
        tokenize(d, ck, eng)
        res.append(d)
    return res
```

---

## 流程5：Embedding向量化

### 核心功能
- 中文用m3e-base/bge-small-zh
- 向量L2归一化
- 批量处理+异常兜底

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/llm/embedding_model.py` | 嵌入模型实现 |
| `rag/svr/task_executor.py` | 任务执行中的向量化流程 |
| `rag/flow/tokenizer/tokenizer.py` | Tokenizer组件 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `Base` | 抽象基类，定义encode和encode_queries接口 |
| `BuiltinEmbed` | 内置嵌入模型(支持TEI服务) |
| `SILICONFLOWEmbed` | 支持中文模型(BAAI/bge-large-zh-v1.5等) |
| `embedding()` | 主向量化流程 |

### 核心代码

```python
# embedding_model.py - 中文模型支持
class SILICONFLOWEmbed(Base):
    _FACTORY_NAME = "SILICONFLOW"
    
    def encode(self, texts: list):
        batch_size = 16
        for i in range(0, len(texts), batch_size):
            texts_batch = texts[i : i + batch_size]
            # 支持中文模型: BAAI/bge-large-zh-v1.5
            if self.model_name in ["BAAI/bge-large-zh-v1.5", "BAAI/bge-large-en-v1.5"]:
                texts_batch = [" " if not text.strip() else truncate(text, 256) for text in texts_batch]
            
            payload = {"model": self.model_name, "input": texts_batch, "encoding_format": "float"}
            response = requests.post(self.base_url, json=payload, headers=self.headers)
        return np.array(ress), token_count

# task_executor.py - 批量处理与异常兜底
async def embedding(docs, mdl, parser_config=None, callback=None):
    tts, cnts = [], []
    for d in docs:
        tts.append(d.get("docnm_kwd", "Title"))
        c = d["content_with_weight"]
        c = re.sub(r"</?(table|td|caption|tr|th)( [^<>]{0,12})?>", " ", c)
        cnts.append(c)

    tk_count = 0
    # 标题向量编码
    vts, c = await thread_pool_exec(mdl.encode, tts[0:1])
    tts = np.tile(vts[0], (len(cnts), 1))

    @timeout(60)
    def batch_encode(txts):
        return mdl.encode([truncate(c, mdl.max_length - 10) for c in txts])

    # 批量处理
    for i in range(0, len(cnts), settings.EMBEDDING_BATCH_SIZE):
        async with embed_limiter:  # 并发限制
            vts, c = await thread_pool_exec(batch_encode, cnts[i: i + settings.EMBEDDING_BATCH_SIZE])
        cnts_ = np.concatenate((cnts_, vts), axis=0)
        tk_count += c
    
    # 文件名权重融合
    filename_embd_weight = parser_config.get("filename_embd_weight", 0.1)
    vects = title_w * tts + (1 - title_w) * cnts

    for i, d in enumerate(docs):
        d["q_%d_vec" % len(v)] = v
    return tk_count, vector_size
```

---

## 流程6：向量库入库

### 核心功能
- 存储向量+原文+元数据
- 建索引，文本哈希去重
- 批量事务

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/utils/es_conn.py` | ES操作（insert, update, delete） |
| `rag/utils/infinity_conn.py` | Infinity向量数据库连接 |
| `rag/svr/task_executor.py` | 任务执行器 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `ESConnection.insert()` | 批量插入文档 |
| `InfinityConnection.insert()` | Infinity批量插入 |
| `insert_chunks()` | 批量插入chunks |

### 核心代码

```python
# task_executor.py - 文本哈希去重
d["id"] = xxhash.xxh64(
    (chunk["content_with_weight"] + str(d["doc_id"])).encode("utf-8", "surrogatepass")
).hexdigest()

# es_conn.py - Elasticsearch批量插入
def insert(self, documents: list[dict], index_name: str, knowledgebase_id: str = None):
    operations = []
    for d in documents:
        d_copy = copy.deepcopy(d)
        d_copy["kb_id"] = knowledgebase_id
        meta_id = d_copy.get("id", "")
        operations.append({"index": {"_index": index_name, "_id": meta_id}})
        operations.append(d_copy)

    for _ in range(ATTEMPT_TIME):  # 重试2次
        try:
            r = self.es.bulk(index=index_name, operations=operations, refresh=False, timeout="60s")
            if re.search(r"False", str(r["errors"]), re.IGNORECASE):
                return res
        except ConnectionTimeout:
            time.sleep(3)
            self._connect()
            continue
    return res

# task_executor.py - 批量事务处理
async def insert_chunks(task_id, task_tenant_id, task_dataset_id, chunks, progress_callback):
    # 批量插入chunks
    for b in range(0, len(chunks), settings.DOC_BULK_SIZE):
        doc_store_result = await thread_pool_exec(
            settings.docStoreConn.insert, 
            chunks[b:b + settings.DOC_BULK_SIZE],
            search.index_name(task_tenant_id), task_dataset_id)
        
        if has_canceled(task_id):
            progress_callback(-1, msg="Task has been canceled.")
            return False
        
        if doc_store_result:
            raise Exception(f"Insert chunk error: {doc_store_result}")
        
        # 更新任务进度
        chunk_ids = [chunk["id"] for chunk in chunks[:b + settings.DOC_BULK_SIZE]]
        TaskService.update_chunk_ids(task_id, " ".join(chunk_ids))
    return True
```

---

## 流程7：Query预处理

### 核心功能
- 纠错、口语转书面
- 扩写、关键词提取
- 优化检索语句

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/nlp/query.py` | 查询预处理核心类 |
| `rag/nlp/term_weight.py` | 词权重计算（TF-IDF） |
| `rag/nlp/synonym.py` | 同义词扩展 |
| `rag/prompts/generator.py` | 多语言扩写、关键词提取 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `FulltextQueryer` | 全文查询处理器主类 |
| `question()` | 查询预处理主方法 |
| `Dealer.weights()` | 计算词权重（TF-IDF） |
| `Dealer.lookup()` | 同义词查找 |
| `cross_languages()` | 多语言查询扩写 |

### 核心代码

```python
# query.py - 查询预处理主流程
class FulltextQueryer(QueryBase):
    def question(self, txt, tbl="qa", min_match: float = 0.6):
        """查询预处理主方法：纠错、分词、权重计算、同义词扩展"""
        # 1. 文本清洗
        txt = re.sub(r"[ :|\r\n\t,，。？?/`!！&^%%()\[\]{}<>*~'\"\\]+", " ",
                    rag_tokenizer.tradi2simp(rag_tokenizer.strQ2B(txt.lower()))).strip()
        
        # 2. 分词处理
        tks = rag_tokenizer.tokenize(txt).split()
        
        # 3. 计算词权重（TF-IDF）
        tks_w = self.tw.weights(tks, preprocess=False)
        
        # 4. 同义词扩展
        syns = []
        for tk, w in tks_w[:256]:
            syn = [rag_tokenizer.tokenize(s) for s in self.syn.lookup(tk)]
            keywords.extend(syn)
        
        # 5. 构建查询表达式（带权重）
        q = ["({}^{:.4f}".format(tk, w) + " {})".format(syn) for (tk, w), syn in zip(tks_w, syns)]
        
        return MatchTextExpr(self.query_fields, " ".join(q), 100), keywords

# term_weight.py - 词权重计算
def weights(self, tks, preprocess=True):
    """计算词权重：结合TF-IDF、NER、词性标注"""
    def ner(t):
        m = {"toxic": 2, "func": 1, "corp": 3, "loca": 3, "sch": 3, "stock": 3}
        return m.get(self.ne.get(t), 1)
    
    def idf(s, N):
        return math.log10(10 + ((N - s + 0.5) / (s + 0.5)))
    
    # 综合权重 = IDF * NER权重 * 词性权重
    wts = (0.3 * idf1 + 0.7 * idf2) * np.array([ner(t) * postag(t) for t in tks])
    return [(t, s / S) for t, s in tw]

# synonym.py - 同义词扩展
def lookup(self, tk, topn=8):
    """查找同义词：优先自定义词典，其次WordNet"""
    # 1. 优先从自定义词典查找
    res = self.dictionary.get(key, [])
    if res:
        return res[:topn]
    
    # 2. 从WordNet查找同义词
    if re.fullmatch(r"[a-z]+", tk):
        wn_set = {re.sub("_", " ", syn.name().split(".")[0]) for syn in wordnet.synsets(tk)}
        return list(wn_set)[:topn]
    return []
```

---

## 流程8：混合检索

### 核心功能
- 向量检索+BM25加权融合
- 返回top-k结果

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/nlp/search.py` | 检索核心逻辑 |
| `rag/utils/es_conn.py` | Elasticsearch检索实现 |
| `common/doc_store/doc_store_base.py` | 检索表达式基类 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `Dealer.search()` | 混合检索主方法 |
| `get_vector()` | 向量检索表达式生成 |
| `MatchDenseExpr` | 向量检索表达式 |
| `MatchTextExpr` | 文本检索表达式 |
| `FusionExpr` | 融合表达式 |

### 核心代码

```python
# search.py - 混合检索主流程
async def search(self, req, idx_names, kb_ids, emb_mdl=None):
    """混合检索：向量检索 + BM25文本检索 + 加权融合"""
    # 1. 文本检索（BM25）
    matchText, keywords = self.qryr.question(qst, min_match=0.3)
    
    if emb_mdl:
        # 2. 向量检索
        matchDense = await self.get_vector(qst, emb_mdl, topk, req.get("similarity", 0.1))
        
        # 3. 融合表达式：加权求和
        fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
        # 权重说明：0.05给文本，0.95给向量
        
        # 4. 组合三种表达式
        matchExprs = [matchText, matchDense, fusionExpr]
        
        res = await thread_pool_exec(self.dataStore.search, ...)
    
    return self.SearchResult(total=total, ids=ids, query_vector=q_vec, field=fields)

# search.py - 向量检索表达式生成
async def get_vector(self, txt, emb_mdl, topk=10, similarity=0.1):
    """生成向量检索表达式"""
    qv, _ = await thread_pool_exec(emb_mdl.encode_queries, txt)
    
    return MatchDenseExpr(
        vector_column_name=f"q_{len(qv)}_vec",
        embedding_data=qv,
        embedding_data_type='float',
        distance_type='cosine',
        topn=topk,
        extra_options={"similarity": similarity}
    )

# query.py - 混合相似度计算
def hybrid_similarity(self, avec, bvecs, atks, btkss, tkweight=0.3, vtweight=0.7):
    """计算混合相似度：向量相似度 + 词项相似度"""
    # 1. 计算向量余弦相似度
    sims = cosine_similarity([avec], bvecs)
    
    # 2. 计算词项相似度（基于关键词重叠）
    tksim = self.token_similarity(atks, btkss)
    
    # 3. 加权融合
    return np.array(sims[0]) * vtweight + np.array(tksim) * tkweight, tksim, sims[0]
```

---

## 流程9：结果过滤去重

### 核心功能
- 相似度阈值过滤
- 文本哈希去重
- 元数据/长度筛选

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/nlp/search.py` | 检索结果过滤 |
| `agent/tools/retrieval.py` | 检索工具（含去重） |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `retrieval()` | 检索结果过滤主方法 |
| `rerank()` | 重排序和相似度计算 |
| `retrieval_by_children()` | 子文档合并去重 |

### 核心代码

```python
# search.py - 检索结果过滤
async def retrieval(self, question, embd_mdl, tenant_ids, kb_ids, page, page_size,
                   similarity_threshold=0.2, vector_similarity_weight=0.3):
    """检索结果过滤：相似度阈值、重排序、分页"""
    # 1. 执行检索
    sres = await self.search(req, [index_name(tid) for tid in tenant_ids], kb_ids, embd_mdl)
    
    # 2. 重排序
    if rerank_mdl:
        sim, tsim, vsim = self.rerank_by_model(rerank_mdl, sres, question, ...)
    else:
        sim, tsim, vsim = self.rerank(sres, question, ...)
    
    # 3. 排序（按相似度降序）
    sorted_idx = np.argsort(sim_np * -1)
    
    # 4. 相似度阈值过滤
    valid_idx = [int(i) for i in sorted_idx if sim_np[i] >= post_threshold]
    
    # 5. 分页
    page_idx = valid_idx[begin:end]
    
    # 6. 构建返回结果
    for i in page_idx:
        d = {
            "chunk_id": id,
            "similarity": float(sim_np[i]),
            "vector_similarity": float(vsim[i]),
            "term_similarity": float(tsim[i]),
            ...
        }
        ranks["chunks"].append(d)
    return ranks

# search.py - 子文档合并去重
def retrieval_by_children(self, chunks: list[dict], tenant_ids: list[str]):
    """合并子文档：将多个子chunk合并为父chunk"""
    mom_chunks = defaultdict(list)
    
    # 1. 提取有父文档的子chunk
    for ck in chunks:
        mom_id = ck.get("mom_id")
        if mom_id:
            mom_chunks[ck["mom_id"]].append(ck)
    
    # 2. 合并同一父文档的所有子chunk
    for id, cks in mom_chunks.items():
        d = {
            "chunk_id": id,
            "similarity": np.mean([ck["similarity"] for ck in cks]),
            ...
        }
        chunks.append(d)
    
    return sorted(chunks, key=lambda x: x["similarity"] * -1)
```

---

## 流程10：Rerank重排序

### 核心功能
- bge-reranker-base重排候选块
- 提升相关性

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/llm/rerank_model.py` | 重排序模型实现 |
| `rag/nlp/search.py` | 检索和重排序核心逻辑 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `HuggingfaceRerank` | HuggingFace重排序模型 |
| `similarity()` | 重排序接口方法 |
| `rerank_by_model()` | 使用模型进行重排序 |

### 核心代码

```python
# rerank_model.py - BGE-Reranker重排序模型实现
class HuggingfaceRerank(Base):
    _FACTORY_NAME = "HuggingFace"

    @staticmethod
    def post(query: str, texts: list, url="127.0.0.1"):
        """批量调用重排序API"""
        scores = [0 for _ in range(len(texts))]
        batch_size = 8
        for i in range(0, len(texts), batch_size):
            res = requests.post(
                f"http://{url}/rerank", 
                json={"query": query, "texts": texts[i : i + batch_size], 
                      "raw_scores": False, "truncate": True}
            )
            for o in res.json():
                scores[o["index"] + i] = o["score"]
        return np.array(scores)

    def similarity(self, query: str, texts: list) -> tuple[np.ndarray, int]:
        """计算查询与文本的相关性分数"""
        return HuggingfaceRerank.post(query, texts, self.base_url), token_count

# search.py - 使用模型进行重排序
def rerank_by_model(self, rerank_mdl, sres, query, tkweight=0.3, vtweight=0.7):
    """使用重排序模型对检索结果进行重排序"""
    # 1. 提取查询关键词
    _, keywords = self.qryr.question(query)
    
    # 2. 构建每个候选块的文本表示
    ins_tw = []
    for i in sres.ids:
        tks = content_ltks + title_tks + important_kwd
        ins_tw.append(tks)
    
    # 3. 计算关键词相似度
    tksim = self.qryr.token_similarity(keywords, ins_tw)
    
    # 4. 调用重排序模型计算相关性分数
    vtsim, _ = rerank_mdl.similarity(query, [" ".join(tks) for tks in ins_tw])
    
    # 5. 加权融合
    return tkweight * np.array(tksim) + vtweight * vtsim, tksim, vtsim
```

---

## 流程11：上下文拼接

### 核心功能
- 按重排顺序拼接
- token长度裁剪
- 保留溯源元数据

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/prompts/generator.py` | 上下文格式化和拼接 |
| `common/token_utils.py` | token计数和截断 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `kb_prompt()` | 格式化检索结果为上下文 |
| `truncate()` | token长度截断 |
| `num_tokens_from_string()` | token计数 |
| `message_fit_in()` | 消息长度适配 |

### 核心代码

```python
# generator.py - 上下文格式化函数
def kb_prompt(kbinfos, max_tokens, hash_id=False):
    """将检索结果格式化为上下文提示词"""
    # 1. 提取所有候选块的内容
    knowledges = [ck["content_with_weight"] for ck in kbinfos["chunks"]]
    
    # 2. 按token长度裁剪候选块数量
    for i, c in enumerate(knowledges):
        used_token_count += num_tokens_from_string(c)
        if max_tokens * 0.97 < used_token_count:
            knowledges = knowledges[:i]
            break
    
    # 3. 获取文档元数据(溯源信息)
    docs = DocumentService.get_by_ids([ck["doc_id"] for ck in kbinfos["chunks"]])
    
    # 4. 格式化每个候选块
    knowledges = []
    for i, ck in enumerate(kbinfos["chunks"]):
        cnt = "\nID: {}".format(i)
        cnt += f"\n├── Title: {ck['docnm_kwd']}"
        cnt += f"\n├── URL: {ck.get('url', '')}"
        cnt += "\n└── Content:\n"
        cnt += ck["content_with_weight"]
        knowledges.append(cnt)
    
    return knowledges

# token_utils.py - Token计数和截断
encoder = tiktoken.get_encoding("cl100k_base")

def num_tokens_from_string(string: str) -> int:
    """计算文本的token数量"""
    return len(encoder.encode(string))

def truncate(string: str, max_len: int) -> str:
    """截断文本到指定token长度"""
    return encoder.decode(encoder.encode(string)[:max_len])
```

---

## 流程12：Prompt模板

### 核心功能
- 约束LLM仅用上下文作答
- 禁幻觉，无结果固定兜底
- 标注来源

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/prompts/generator.py` | Prompt模板生成 |
| `rag/prompts/*.md` | 各种提示词模板 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `kb_prompt()` | 格式化知识库上下文 |
| `message_fit_in()` | 消息长度适配 |

### 核心代码

```python
# generator.py - Prompt模板构建
def kb_prompt(kbinfos, max_tokens, hash_id=False):
    """构建知识库Prompt，约束LLM仅用上下文作答"""
    # 格式化知识块
    for i, ck in enumerate(kbinfos["chunks"]):
        cnt = f"\nID: {i}"
        cnt += f"\n├── Title: {ck['docnm_kwd']}"
        cnt += "\n└── Content:\n" + ck["content_with_weight"]
        knowledges.append(cnt)
    
    return knowledges

# dialog_service.py - 无结果固定兜底
if not knowledges and prompt_config.get("empty_response"):
    empty_res = prompt_config["empty_response"]
    yield {"answer": empty_res, "reference": kbinfos}
    return
```

---

## 流程13：LLM答案生成

### 核心功能
- 兼容通用LLM
- 超时重试
- 记录token/耗时，支持流式

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/llm/chat_model.py` | 聊天模型实现 |
| `agent/component/llm.py` | LLM组件 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `Base` | LLM基类 |
| `chat()` | 同步聊天 |
| `async_chat()` | 异步聊天 |
| `stream_chat()` | 流式聊天 |

### 核心代码

```python
# chat_model.py - LLM答案生成
class Base:
    def chat(self, sys_prompt: str, msgs: list[dict], gen_conf: dict):
        """同步聊天"""
        pass
    
    async def async_chat(self, sys_prompt: str, msgs: list[dict], gen_conf: dict):
        """异步聊天"""
        pass
    
    def stream_chat(self, sys_prompt: str, msgs: list[dict], gen_conf: dict):
        """流式聊天"""
        pass

# agent/component/llm.py - LLM组件
async def _invoke_async(self, **kwargs):
    # 准备提示词
    prompt, msg, _ = self._prepare_prompt_variables()
    
    # 检查下游是否有Message组件（流式输出）
    if has_message_downstream:
        self.set_output("content", partial(self._stream_output_async, prompt, msg))
        return
    
    # 非流式生成
    ans = await self._generate_async(msg)
    self.set_output("content", ans)
```

---

## 流程14：答案校验溯源

### 核心功能
- 核对一致性
- 生成引用溯源
- 标记幻觉风险

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/prompts/citation_prompt.md` | 引用提示词模板 |
| `rag/nlp/search.py` | 引用插入核心算法 |
| `api/db/services/dialog_service.py` | 对话服务中的引用处理 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `insert_citations()` | 插入引用标记 |
| `repair_bad_citation_formats()` | 修复错误的引用格式 |
| `decorate_answer()` | 装饰答案（添加引用） |

### 核心代码

```python
# search.py - 引用插入算法
def insert_citations(self, answer, chunks, chunk_v, embd_mdl, tkweight=0.1, vtweight=0.9):
    """核对一致性、生成引用溯源、标记幻觉风险"""
    # 按句子分割答案
    pieces = re.split(r"([^\|][；。？!！\n])", answer)
    
    # 对每个句子进行编码
    ans_v, _ = embd_mdl.encode(pieces_)
    
    # 计算混合相似度
    cites = {}
    thr = 0.63  # 幻觉风险阈值
    while thr > 0.3 and len(cites.keys()) == 0:
        for i, a in enumerate(pieces_):
            sim, tksim, vtsim = self.qryr.hybrid_similarity(ans_v[i], chunk_v, ...)
            mx = np.max(sim) * 0.99
            if mx < thr:  # 低于阈值则跳过，可能存在幻觉风险
                continue
            cites[idx[i]] = [str(ii) for ii in range(len(chunk_v)) if sim[ii] > mx][:4]
        thr *= 0.8
    
    # 插入引用标记
    for c in cites[i]:
        res += f" [ID:{c}]"
    
    return res, seted

# dialog_service.py - 答案装饰与引用处理
def decorate_answer(answer):
    # 如果答案中没有引用标记，自动插入
    if embd_mdl and not CITATION_MARKER_PATTERN.search(answer):
        answer, idx = retriever.insert_citations(answer, chunks, vectors, embd_mdl)
    
    # 修复错误引用格式
    answer, idx = repair_bad_citation_formats(answer, kbinfos, idx)
    
    return {"answer": answer, "reference": refs}
```

---

## 流程15：兜底与埋点

### 核心功能
- 无结果标准化回复
- 全流程日志埋点
- 记录核心指标

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `api/db/services/dialog_service.py` | 无结果标准化回复 |
| `rag/svr/task_executor.py` | 任务执行日志埋点 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `async_chat()` | 对话主流程 |
| `set_progress()` | 任务进度更新 |
| `report_status()` | 执行器状态上报 |

### 核心代码

```python
# dialog_service.py - 无结果标准化回复
if not knowledges and prompt_config.get("empty_response"):
    empty_res = prompt_config["empty_response"]
    yield {"answer": empty_res, "reference": kbinfos}
    return

# task_executor.py - 任务进度更新与日志埋点
def set_progress(task_id, from_page=0, to_page=-1, prog=None, msg="Processing..."):
    if prog is not None and prog < 0:
        msg = "[ERROR]" + msg
    cancel = has_canceled(task_id)
    if cancel:
        msg += " [Canceled]"
        prog = -1
    TaskService.update_progress(task_id, {"progress_msg": msg, "progress": prog})
    logging.info(f"set_progress({task_id}), progress: {prog}, progress_msg: {msg}")

# dialog_service.py - 记录核心指标
total_time_cost = (finish_chat_ts - chat_start_ts) * 1000
prompt += f"\n\n## Time elapsed:\n  - Total: {total_time_cost:.1f}ms\n"
prompt += f"## Token usage:\n  - Generated tokens: {tk_num}\n"
```

---

## 流程16：知识库增量维护

### 核心功能
- 增量更新、按ID删向量
- 版本记录、索引优化

### 核心文件

| 文件路径 | 作用 |
|---------|------|
| `rag/utils/es_conn.py` | ES操作（insert, update, delete） |
| `api/apps/chunk_app.py` | Chunk的增删改 |
| `api/db/services/document_service.py` | 文档服务 |

### 关键函数

| 函数/类 | 功能描述 |
|---------|----------|
| `ESConnection.insert()` | 批量插入文档 |
| `ESConnection.delete()` | 删除文档 |
| `insert_chunks()` | 插入chunks |
| `remove_document()` | 删除文档 |

### 核心代码

```python
# es_conn.py - 按ID删向量
def delete(self, condition: dict, index_name: str, knowledgebase_id: str) -> int:
    """按条件删除文档"""
    condition["kb_id"] = knowledgebase_id
    
    # 构建布尔查询
    bool_query = Q("bool")
    if "id" in condition:
        bool_query.filter.append(Q("ids", values=chunk_ids))
    
    # 执行删除操作
    res = self.es.delete_by_query(index=index_name, body=Search().query(qry).to_dict())
    return res["deleted"]

# document_service.py - 删除文档及其所有相关数据
@classmethod
def remove_document(cls, doc, tenant_id):
    """删除文档及其所有相关数据"""
    # 取消所有运行中的任务
    cancel_all_task_of(doc.id)
    
    # 删除任务
    TaskService.filter_delete([Task.doc_id == doc.id])
    
    # 删除chunk图片
    cls.delete_chunk_images(doc, tenant_id)
    
    # 从文档存储删除chunks
    settings.docStoreConn.delete({"doc_id": doc.id}, search.index_name(tenant_id), doc.kb_id)
    
    # 清理知识图谱引用
    settings.docStoreConn.update(
        {"kb_id": doc.kb_id, "source_id": doc.id},
        {"remove": {"source_id": doc.id}},
        search.index_name(tenant_id), doc.kb_id)
    
    return True
```

---

## 总结

### 核心模块总览

| 流程 | 核心文件 | 关键技术 |
|------|----------|----------|
| 1.文档采集校验 | task_executor.py, file_utils.py | Redis队列、文件格式识别 |
| 2.文本读取清洗 | naive.py, pdf_parser.py | OCR、编码检测、乱码处理 |
| 3.结构化提取 | pdf_parser.py, table_structure_recognizer.py | 布局分析、表格识别 |
| 4.智能分块 | splitter.py, nlp/__init__.py | 语义切分、重叠分块 |
| 5.Embedding向量化 | embedding_model.py | bge-large-zh、批量处理 |
| 6.向量库入库 | es_conn.py, infinity_conn.py | xxhash去重、批量事务 |
| 7.Query预处理 | query.py, term_weight.py | TF-IDF、同义词扩展 |
| 8.混合检索 | search.py, es_conn.py | 向量+BM25融合 |
| 9.结果过滤去重 | search.py, retrieval.py | 相似度阈值、子文档合并 |
| 10.Rerank重排序 | rerank_model.py | bge-reranker |
| 11.上下文拼接 | generator.py, token_utils.py | token裁剪、溯源元数据 |
| 12.Prompt模板 | prompts/*.md | 约束LLM、防幻觉 |
| 13.LLM答案生成 | chat_model.py | 流式输出、超时重试 |
| 14.答案校验溯源 | citation_prompt.md, search.py | 引用标记、幻觉检测 |
| 15.兜底与埋点 | dialog_service.py, task_executor.py | 日志、指标记录 |
| 16.知识库增量维护 | es_conn.py, chunk_app.py | 增量更新、索引优化 |

### 技术亮点

1. **多格式支持**：支持PDF/Word/TXT/MD等10+种文档格式
2. **智能分块**：语义切分、重叠分块、元数据附加
3. **混合检索**：向量检索+BM25加权融合，提升召回率
4. **Rerank重排序**：bge-reranker提升相关性
5. **引用溯源**：自动插入引用标记，支持幻觉检测
6. **增量维护**：支持增量更新、版本记录、索引优化
