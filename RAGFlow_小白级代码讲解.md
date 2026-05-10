# RAGFlow 核心代码小白级讲解

> **目标**：让完全不懂代码的人，也能看懂这 7 个职责是怎么实现的。
> 
> **讲解方式**：用生活中的比喻 + 代码逐行翻译 + 流程图

---

## 先搞清楚：RAGFlow 是做什么的？

想象你是一个**图书管理员**：

1. **有人送书来**（上传文档）→ 你要把书拆开、分类、做目录
2. **有人来找书**（用户提问）→ 你要快速找到最相关的几页
3. **把找到的内容交给专家**（发给 AI）→ 专家根据这些内容回答问题

RAGFlow 就是做这个的**自动化系统**。

---

## 职责一：PDF 乱码检测与 OCR 回退

### 对应文件：`deepdoc/parser/pdf_parser.py`

### 生活中的比喻

想象你收到一封信，信上的字有两种情况：
- **正常信**：字都认识，直接读
- **乱码信**：字变成了 `㐀`、`궅` 这种看不懂的符号，或者全是标点符号

你的工作是：先判断信是不是乱码，如果是，就**拍照发给识字专家**（OCR）重新识别。

### 代码是怎么实现的？

#### 第一步：定义"乱码"的标准

```python
class RAGFlowPdfParser:
    # 这些 Unicode 码点范围是"私人使用区"（Private Use Area）
    # 就像信纸上印了一些厂家专用的暗号，普通人看不懂
    PUA_CHARS = (
        (0xE000, 0xF8FF),    # 基本多文种平面的私人区
        (0xF0000, 0xFFFFD),  # 第一辅助平面的私人区
        (0x100000, 0x10FFFD) # 第二辅助平面的私人区
    )
    
    # CID 占位符：PDF 内部用来表示"这个字我没法显示"的标记
    _CID_PATTERN = re.compile(r"\(cid:\d+\)")
```

**小白翻译**：
- `PUA_CHARS` = 信纸上印的暗号范围
- `_CID_PATTERN` = 信上写着"(cid:123)"这种标记，意思是"这里应该有个字，但我不知道怎么显示"

#### 第二步：第一层检测 —— PUA/CID 检测

```python
def _is_garbled_text(self, text, threshold=0.5):
    """
    判断一段文字是不是乱码
    
    参数：
        text: 要检查的文字
        threshold: 阈值，默认 0.5（50%）
    
    返回：
        True = 是乱码，False = 不是乱码
    """
    # 如果文字里有 "(cid:123)" 这种标记，直接判定为乱码
    if RAGFlowPdfParser._CID_PATTERN.search(text):
        return True
    
    # 统计乱码字符的数量
    garbled_count = 0
    total = 0
    for ch in text:
        total += 1
        # 检查这个字符是不是在 PUA 范围内
        if self._is_pua_char(ch):
            garbled_count += 1
    
    # 如果乱码字符超过 50%，就认为是乱码
    if total == 0:
        return False
    return garbled_count / total >= threshold
```

**小白翻译**：
1. 先看信上有没有 `(cid:xxx)` 这种标记 → 有就直接判定乱码
2. 数一下信上有多少"暗号字符"
3. 如果暗号超过一半 → 这封信是乱码

#### 第三步：第二层检测 —— 字体编码检测

```python
def _is_garbled_by_font_encoding(self, page_chars):
    """
    检测字体子集化导致的乱码
    
    有些 PDF 为了减小文件大小，只嵌入用到的字（子集化字体）
    但提取时映射关系丢失，导致中文变成了标点符号
    """
    # 统计页面上的字符
    total = len(page_chars)
    if total < 20:  # 字符太少，不判断
        return False
    
    # 统计中文字符数量
    cjk_count = 0
    # 统计 ASCII 标点数量
    punct_count = 0
    
    for char_info in page_chars:
        char = char_info.get("text", "")
        # 判断是不是中文字符（\u4e00 到 \u9fff 是汉字范围）
        if "\u4e00" <= char <= "\u9fff":
            cjk_count += 1
        # 判断是不是标点符号
        elif char in ".,;:!?\"'()[]":
            punct_count += 1
    
    # 如果中文字符极少（<5%），但标点符号极多（>40%）
    # 说明字体映射出了问题
    cjk_ratio = cjk_count / total
    punct_ratio = punct_count / total
    
    return cjk_ratio < 0.05 and punct_ratio > 0.4
```

**小白翻译**：
1. 看信上有多少中文字
2. 看信上有多少标点符号
3. 如果**中文字很少**（不到 5%），但**标点很多**（超过 40%）→ 说明字体映射错了，是乱码

#### 第四步：完整的检测流程

```python
def __images__(self, fnm, from_page, to_page, callback=None):
    """
    提取 PDF 每一页的文字和图片
    
    这是核心方法，处理每一页 PDF
    """
    # 用 pdfplumber 打开 PDF
    pdf = pdfplumber.open(fnm)
    
    for i, page in enumerate(pdf.pages[from_page:to_page]):
        # 第一步：用 pdfplumber 提取文字
        chars = page.chars  # 获取页面上的所有字符信息
        
        # 第二步：判断是不是乱码
        text = "".join([c.get("text", "") for c in chars])
        
        is_garbled = False
        if text:
            # 第一层检测：PUA/CID
            is_garbled = self._is_garbled_text(text)
            
            # 第二层检测：字体编码
            if not is_garbled:
                is_garbled = self._is_garbled_by_font_encoding(chars)
        
        # 第三步：根据检测结果决定怎么处理
        if is_garbled:
            # 乱码了！把页面转成图片，走 OCR
            image = page.to_image(resolution=72)  # 页面转图片
            ocr_result = self.__ocr__(image)       # 调用 OCR 识别
            sections = ocr_result["text"]          # 拿到识别出的文字
        else:
            # 没乱码，直接用 pdfplumber 提取的文字
            sections = self._extract_text(chars)
        
        # 第四步：返回结果
        yield sections
```

**小白翻译**：
1. 打开 PDF，一页一页处理
2. 用 pdfplumber 提取文字
3. **两层检测**判断是不是乱码
4. 如果乱码 → 页面转图片 → OCR 识别
5. 如果没乱码 → 直接用提取的文字

#### 第五步：OCR 识别（简化版）

```python
def __ocr__(self, image):
    """
    对图片进行 OCR 识别
    
    参数：
        image: PDF 页面转成的图片
    
    返回：
        识别出的文字和位置信息
    """
    # 第一步：文本检测（DB 算法）
    # 找出图片中哪里有文字
    text_boxes = self.text_detector(image)
    
    # 第二步：文字识别（CTC 算法）
    # 对每个文字区域进行识别
    recognized_texts = []
    for box in text_boxes:
        # 裁剪出文字区域
        cropped = image.crop(box)
        # 识别文字
        text = self.text_recognizer(crop)
        recognized_texts.append(text)
    
    # 第三步：版式分析
    # 判断每个文字块是标题、正文、表格还是图片
    layouts = self.layout_recognizer(image, text_boxes)
    
    return {
        "text": recognized_texts,
        "layouts": layouts
    }
```

**小白翻译**：
1. **检测**：在照片上画框框，标出哪里有字
2. **识别**：把每个框里的字读出来
3. **分类**：判断这些字是标题、正文、还是表格

### 完整流程图

```
PDF 文件
    ↓
打开 PDF，逐页处理
    ↓
用 pdfplumber 提取文字
    ↓
┌─────────────────────────────────────┐
│  第一层检测：PUA/CID 字符占比 > 50%？  │
│  第二层检测：中文 < 5% 且标点 > 40%？  │
└─────────────────────────────────────┘
    ↓
    ├─ 是乱码 ──→ 页面转图片 ──→ OCR 识别 ──→ 输出文字
    ↓
    └─ 没乱码 ──→ 直接用 pdfplumber 的文字 ──→ 输出文字
```

### 面试官可能问什么？

| 问题 | 回答 |
|-----|------|
| "PUA 字符是什么？" | Unicode 的私人使用区，PDF 解析器用这些码点表示无法映射的字符 |
| "为什么用两层检测？" | 第一层检测 PUA/CID 占位符，第二层检测字体子集化导致的编码异常，两层互补 |
| "OCR 比 pdfplumber 慢多少？" | 大约 5~8 倍，所以误判率要控制在 2% 以下 |
| "DB 算法是什么？" | Differentiable Binarization，可微分二值化的文本检测算法 |

---

## 职责二：多后端文档解析适配层

### 对应文件：`rag/app/naive.py`

### 生活中的比喻

想象你是一个**餐厅经理**，餐厅有 5 个厨师：
- **DeepDOC 厨师**：擅长做家常菜（通用 PDF 解析）
- **MinerU 厨师**：擅长做精致料理（表格精确还原）
- **Docling 厨师**：擅长做快手菜（快速解析）
- **PaddleOCR 厨师**：擅长做海鲜（中文 OCR）
- **PlainText 厨师**：只做白粥（纯文本提取）

客人点菜时，你要根据客人的口味（配置），**自动分配给合适的厨师**。

问题是：每个厨师的**工作流程不一样**（有的先切菜，有的先焯水），你要怎么统一管理？

答案是：**统一出餐标准**——不管哪个厨师做，最后端上来的都是"一荤一素一汤"（统一格式的返回值）。

### 代码是怎么实现的？

#### 第一步：定义"厨师名单"

```python
# 这是一个字典，key 是厨师名字，value 是厨师的做菜方法
PARSERS = {
    "deepdoc": by_deepdoc,      # DeepDOC 厨师
    "mineru": by_mineru,        # MinerU 厨师
    "docling": by_docling,      # Docling 厨师
    "tcadp parser": by_tcadp,   # 腾讯云厨师
    "paddleocr": by_paddleocr,  # PaddleOCR 厨师
    "plaintext": by_plaintext,  # 白粥厨师（默认）
}
```

**小白翻译**：
- 这是一个**菜单**，客人点了哪个，就调用哪个厨师

#### 第二步：统一"出餐标准"

每个厨师（`by_*` 函数）必须返回**相同格式**的三样东西：

```python
def by_deepdoc(filename, binary=None, from_page=0, to_page=100000, 
               lang="Chinese", callback=None, pdf_cls=None, **kwargs):
    """
    DeepDOC 厨师做菜
    
    参数：
        filename: 文件路径（比如 "/tmp/合同.pdf"）
        binary: 文件二进制内容（如果直接传了文件内容，就不用读文件了）
        from_page: 从第几页开始（默认第 0 页）
        to_page: 到第几页结束（默认 10 万页，就是全部）
        lang: 语言（默认中文）
        callback: 进度回调（告诉客人菜做到哪了）
        pdf_cls: 自定义 PDF 解析器类（可选）
    
    返回：
        (sections, tables, pdf_parser)  # 统一的三元组
    """
    # 如果没有指定自定义解析器，就用默认的 Pdf 类
    pdf_parser = pdf_cls() if pdf_cls else Pdf()
    
    # 调用解析器解析文件
    # 如果传了 binary（文件内容），就用 binary；否则用 filename（文件路径）
    sections, tables = pdf_parser(
        filename if not binary else binary, 
        from_page=from_page, 
        to_page=to_page, 
        callback=callback
    )
    
    # 对表格进行视觉增强（用 AI 描述图片内容）
    tables = vision_figure_parser_pdf_wrapper(
        tbls=tables,
        sections=sections,
        callback=callback,
        **kwargs,
    )
    
    # 返回统一格式的三元组
    return sections, tables, pdf_parser
```

**小白翻译**：
1. 创建一个 PDF 解析器对象
2. 解析文件，得到文字段落和表格
3. 对表格进行视觉增强（用 AI 给图片加描述）
4. 返回 `(文字段落, 表格, 解析器对象)`

#### 第三步：MinerU 厨师的实现（带配置自动获取）

```python
def by_mineru(filename, binary=None, from_page=0, to_page=100000, 
              lang="Chinese", callback=None, pdf_cls=None,
              parse_method="raw", mineru_llm_name=None, 
              tenant_id=None, **kwargs):
    """
    MinerU 厨师做菜
    
    特点：MinerU 需要配置信息（比如服务器地址），
          配置可以从三个地方获取：传入参数 > 数据库 > 环境变量
    """
    # 第一步：获取 MinerU 的配置
    if tenant_id:
        # 如果没有指定模型名，就自动查找
        if not mineru_llm_name:
            try:
                # 从数据库查询该租户配置的 MinerU 模型
                candidates = TenantLLMService.query(
                    tenant_id=tenant_id, 
                    llm_factory="MinerU", 
                    model_type=LLMType.OCR
                )
                if candidates:
                    # 用数据库里配置的第一个模型
                    mineru_llm_name = candidates[0].llm_name
                else:
                    # 数据库没有，就从环境变量读取
                    env_name = TenantLLMService.ensure_mineru_from_env(tenant_id)
                    mineru_llm_name = env_name
            except Exception as e:
                logging.warning(f"获取 MinerU 配置失败: {e}")
        
        # 第二步：用模型名获取完整配置
        if mineru_llm_name:
            ocr_model_config = get_model_config_by_type_and_name(
                tenant_id, LLMType.OCR, mineru_llm_name
            )
            # 创建 LLM 模型对象
            ocr_model = LLMBundle(
                tenant_id=tenant_id, 
                model_config=ocr_model_config, 
                lang=lang
            )
            # 用 MinerU 解析 PDF
            pdf_parser = ocr_model.mdl
            sections, tables = pdf_parser.parse_pdf(
                filepath=filename,
                binary=binary,
                callback=callback,
                parse_method=parse_method,
                lang=lang,
                **kwargs,
            )
            return sections, tables, pdf_parser
    
    # 如果配置获取失败，返回 None
    if callback:
        callback(-1, "MinerU not found.")
    return None, None, None
```

**小白翻译**：
1. 先找 MinerU 的配置（传入的参数 → 数据库 → 环境变量）
2. 用配置创建 MinerU 模型对象
3. 调用 MinerU 解析 PDF
4. 返回统一格式的三元组

#### 第四步：主入口 —— 根据配置选择厨师

```python
def chunk(filename, binary=None, from_page=0, to_page=100000,
          lang="Chinese", callback=None, **kwargs):
    """
    主入口：根据配置选择解析器，解析文档并分块
    """
    # 从配置中获取要使用的解析器名称
    # 比如 layout_recognizer = "deepdoc"
    layout_recognizer = (kwargs.get("layout_recognizer") or "").strip()
    
    # 如果没有指定，或者指定了 "Plain Text"，就用默认的纯文本解析
    if not layout_recognizer or layout_recognizer == "Plain Text":
        parser = by_plaintext
    else:
        # 从 PARSERS 字典中查找对应的解析函数
        # 比如 layout_recognizer = "deepdoc" → parser = by_deepdoc
        parser = PARSERS.get(layout_recognizer.lower(), by_plaintext)
    
    # 调用选中的解析函数
    sections, tables, pdf_parser = parser(
        filename=filename,
        binary=binary,
        from_page=from_page,
        to_page=to_page,
        lang=lang,
        callback=callback,
        **kwargs
    )
    
    # ... 后续的分块处理 ...
```

**小白翻译**：
1. 看客人点了哪个厨师（从配置中读取）
2. 从菜单（PARSERS 字典）中找到对应的厨师
3. 让厨师做菜（调用解析函数）
4. 拿到统一格式的结果

### 完整流程图

```
用户上传文档
    ↓
读取配置：layout_recognizer = "deepdoc"
    ↓
PARSERS.get("deepdoc") → 找到 by_deepdoc 函数
    ↓
调用 by_deepdoc(filename, binary, ...)
    ├── 创建 Pdf 解析器对象
    ├── 解析 PDF → 得到 sections（文字）和 tables（表格）
    ├── vision_figure_parser_pdf_wrapper() 视觉增强
    └── 返回 (sections, tables, pdf_parser)
    ↓
上层 chunk() 函数拿到统一格式的结果，继续处理
```

### 面试官可能问什么？

| 问题 | 回答 |
|-----|------|
| "为什么用字典而不是 if-elif？" | 字典是策略模式，新增解析器只需注册到字典，不用改原有代码；if-elif 违反开闭原则 |
| "返回值为什么统一成三元组？" | 上层代码只需要关心 `(文字, 表格, 解析器)`，不用管底层用的是哪个解析器 |
| "ensure_mineru_from_env 做了什么？" | 从环境变量读取 MinerU 配置，自动在数据库中创建/复用模型记录，实现零配置接入 |

---

## 职责三：重叠分块策略

### 对应文件：`rag/nlp/__init__.py`

### 生活中的比喻

想象你有一本**很长的书**，要把书切成一段一段（chunk），方便读者查找。

问题是：如果切得太死板，**一个完整的句子可能被切成两半**，放在两个 chunk 里。读者搜索时，只找到半个句子，看不懂。

解决方案：**每段都留一点重叠**。就像切面包时，每片都和下一片有一点重叠，这样不会掉渣。

### 代码是怎么实现的？

#### 第一步：理解核心参数

```python
def naive_merge(sections, chunk_token_num=128, delimiter="\n。；！？", overlapped_percent=0):
    """
    把文字段落合并成固定大小的 chunk
    
    参数：
        sections: 文字段落列表，每个元素是 (文字内容, 位置信息)
        chunk_token_num: 每个 chunk 最多多少 token（默认 128）
        delimiter: 分隔符，默认按换行、句号、分号、感叹号、问号切分
        overlapped_percent: 重叠百分比（默认 0%，即不重叠）
    
    返回：
        chunk 列表
    """
```

**小白翻译**：
- `sections` = 书的各个段落
- `chunk_token_num` = 每 chunk 最多多少字（token 约等于字）
- `delimiter` = 在哪里可以切（句号、换行等）
- `overlapped_percent` = 相邻 chunk 重叠多少

#### 第二步：核心合并逻辑

```python
def naive_merge(sections, chunk_token_num=128, delimiter="\n。；！？", overlapped_percent=0):
    # 初始化：当前正在构建的 chunk
    cks = [""]           # chunk 列表，一开始有一个空字符串
    tk_nums = [0]        # 每个 chunk 的 token 数
    
    # 遍历每个段落
    for section_text, position_tag in sections:
        # 按分隔符把段落切成更小的片段
        # 比如 "你好。世界。" → ["你好", "。", "世界", "。"]
        for sec in re.split(delimiter, section_text):
            if not sec:
                continue  # 跳过空片段
            
            # 计算这个片段有多少 token
            tokens = num_tokens_from_string(sec)
            
            # 如果当前 chunk 加上这个片段会超过限制
            if tk_nums[-1] + tokens > chunk_token_num:
                # 当前 chunk 满了，开启新 chunk
                
                # ===== 重叠逻辑 =====
                if overlapped_percent > 0 and cks[-1]:
                    # 取上一个 chunk 的尾部作为重叠部分
                    # 比如 overlapped_percent=10，就取尾部 10%
                    overlap_len = int(len(cks[-1]) * overlapped_percent / 100)
                    overlap_text = cks[-1][-overlap_len:]
                    
                    # 新 chunk 以重叠部分开头
                    cks.append(overlap_text + sec)
                    tk_nums.append(num_tokens_from_string(overlap_text + sec))
                else:
                    # 不重叠，直接开启新 chunk
                    cks.append(sec)
                    tk_nums.append(tokens)
            else:
                # 当前 chunk 还能装下，直接追加
                cks[-1] += sec
                tk_nums[-1] += tokens
    
    # 返回所有 chunk（去掉第一个空的）
    return [(c, "") for c in cks[1:]]
```

**小白翻译**：
1. 准备一个空盒子（chunk）
2. 把书里的段落一段一段往里放
3. 如果盒子快满了（超过 128 token）：
   - 把盒子封好
   - 如果设置了重叠，从盒子里拿出最后一点东西，放到新盒子里
   - 开启一个新盒子
4. 如果盒子还能装，继续放

#### 第三步：重叠的图示

假设 `chunk_token_num=10`，`overlapped_percent=20`：

```
原文："今天天气很好。我想去公园玩。公园里有很多人。"

不分块（一个 chunk）：
[今天天气很好。我想去公园玩。公园里有很多人。]

分块但不重叠：
[今天天气很好。我想] [去公园玩。公园里有] [很多人。]
问题："我想去公园" 被切成了两半！

分块且重叠 20%：
[今天天气很好。我想] 
        [我想去公园玩。公园]  ← 保留了 "我想" 作为重叠
                [公园里有很多人。]  ← 保留了 "公园" 作为重叠

这样搜索 "我想去公园" 时，第二个 chunk 包含完整信息！
```

### 面试官可能问什么？

| 问题 | 回答 |
|-----|------|
| "Chunk size 为什么选 512？" | 实验对比了 256/512/1024，256 语义不完整，1024 检索粒度太粗，512 是平衡点 |
| "Overlap 设多少合适？" | 通常 10%~20%，太小补偿不够，太大会增加 20%+ 存储和计算开销 |
| "表格被切断了怎么办？" | `attach_media_context()` 在表格前后附加上下文文本，或整段保留不截断 |

---

## 职责四：混合检索与降级重试

### 对应文件：`rag/nlp/search.py`

### 生活中的比喻

想象你在**图书馆找书**，有两种找法：
- **按内容意思找**（向量检索）：你说"找关于狗的书"，管理员给你找"宠物、犬类、汪汪"相关的书
- **按书名关键词找**（全文检索）：你说"书名里有'狗'字的书"，管理员给你找书名含"狗"的书

**问题**：
- 按意思找，可能漏掉书名里有"犬"但没提到"狗"的书
- 按关键词找，可能找到书名有"狗"但内容讲"狗不理包子"的书

**解决方案**：两种方法都用，结果合并！

### 代码是怎么实现的？

#### 第一步：全文检索（BM25）

```python
# 在 Dealer.search() 方法中

# 1. 对用户的问题进行分词
# 比如 "狗的饲养方法" → ["狗", "饲养", "方法"]
matchText, keywords = self.qryr.question(qst, min_match=0.3)
```

**小白翻译**：
- 把用户的问题切成关键词
- `min_match=0.3` 表示至少 30% 的关键词要匹配上

#### 第二步：向量检索

```python
# 2. 把用户的问题转成向量
# 比如 "狗的饲养方法" → [0.1, 0.5, -0.2, ...]（1024 维的向量）
matchDense = await self.get_vector(qst, emb_mdl, topk, req.get("similarity", 0.1))
```

**小白翻译**：
- 用 AI 模型把问题变成一串数字（向量）
- 然后在数据库里找"数字最相近"的文档

#### 第三步：加权融合

```python
# 3. 把两种检索结果合并
# ES 模式：用 should 子句同时匹配文本和向量
if not settings.DOC_ENGINE_INFINITY:
    matchExprs = [matchText, matchDense]
else:
    # Infinity 模式：用 FusionExpr 加权融合
    # 权重 0.05:0.95 = BM25 占 5%，向量占 95%
    fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
    matchExprs = [matchText, matchDense, fusionExpr]
```

**小白翻译**：
- 把两种找书的结果合并
- BM25（关键词）占 5%，向量（语义）占 95%
- 为什么 BM25 权重低？因为它主要是"补偿"作用，防止向量检索漏掉关键词匹配

#### 第四步：降级重试

```python
# 4. 执行检索
res = await self.dataStore.search(src, highlightFields, filters, matchExprs, ...)
total = self.dataStore.get_total(res)

# 5. 如果第一次没结果，降低阈值重试
if total == 0:
    # 降低 BM25 的匹配阈值：0.3 → 0.1（更容易匹配上）
    matchText, _ = self.qryr.question(qst, min_match=0.1)
    # 降低向量的相似度阈值：0.1 → 0.17（更容易匹配上）
    matchDense.extra_options["similarity"] = 0.17
    # 重新检索
    res = await self.dataStore.search(...)
```

**小白翻译**：
1. 先用严格标准找
2. 如果没找到，放宽标准再找一次
3. 就像找东西：先在抽屉里找，找不到再把抽屉翻个底朝天

### 完整流程图

```
用户提问："狗的饲养方法"
    ↓
【全文检索】分词 → ["狗", "饲养", "方法"] → BM25 匹配
    ↓
【向量检索】问题向量化 → [0.1, 0.5, ...] → 余弦相似度匹配
    ↓
【加权融合】BM25(5%) + 向量(95%) = 最终排序
    ↓
有结果？
    ├─ 有 → 返回 Top-50
    └─ 无 → 降级重试（阈值放宽）→ 再查一次
```

### 面试官可能问什么？

| 问题 | 回答 |
|-----|------|
| "为什么不用纯向量检索？" | 向量检索擅长语义匹配，但对短词、缩写词容易误匹配；BM25 擅长关键词精确匹配 |
| "权重 0.05:0.95 怎么定的？" | 经验值，BM25 主要起补偿作用；可根据业务调整 |
| "降级重试会不会返回不相关结果？" | 会，但这是权衡——有结果总比没结果好，且重排阶段会进一步筛选 |

---

## 职责五：混合重排算法

### 对应文件：`rag/nlp/search.py`

### 生活中的比喻

想象你让两个助手**分别找书**：
- 助手 A（Token 相似度）：按"关键词匹配度"排序
- 助手 B（向量相似度）：按"语义相似度"排序

但两个助手的排序结果不一样：
- 助手 A 把《养狗指南》排第一（关键词匹配高）
- 助手 B 把《宠物护理手册》排第一（语义相似度高）

你怎么综合两个助手的意见？**加权平均**！

### 代码是怎么实现的？

#### 第一步：计算 Token 相似度

```python
def rerank(self, sres, query, tkweight=0.3, vtweight=0.7, ...):
    # 1. 对用户问题分词
    _, keywords = self.qryr.question(query)
    
    # 2. 对每个 chunk，提取它的关键词
    ins_tw = []
    for i in sres.ids:
        # 提取 chunk 的各种文本特征
        content_ltks = sres.field[i]["content_ltks"].split()  # 内容分词
        title_tks = sres.field[i].get("title_tks", "").split()  # 标题分词
        important_kwd = sres.field[i].get("important_kwd", [])  # 关键词
        question_tks = sres.field[i].get("question_tks", "").split()  # 问题分词
        
        # 组合特征：内容 + 标题×2 + 关键词×5 + 问题×6
        # 权重不同：问题分词最重要（×6），关键词次之（×5）
        tks = content_ltks + title_tks * 2 + important_kwd * 5 + question_tks * 6
        ins_tw.append(tks)
    
    # 3. 计算 Token 相似度
    tksim = self.qryr.token_similarity(keywords, ins_tw)
```

**小白翻译**：
1. 把用户问题切成关键词
2. 对每个 chunk，提取它的"关键词集合"
3. 计算问题和 chunk 的关键词匹配度

#### 第二步：计算向量相似度

```python
    # 4. 提取每个 chunk 的向量
    ins_embd = []
    for chunk_id in sres.ids:
        vector = sres.field[chunk_id].get(vector_column, zero_vector)
        ins_embd.append(vector)
    
    # 5. 计算向量相似度（余弦相似度）
    # 用户问题的向量 和 每个 chunk 的向量 算相似度
    vtsim = cosine_similarity(query_vector, ins_embd)
```

**小白翻译**：
1. 拿出每个 chunk 的"数字指纹"（向量）
2. 计算用户问题和 chunk 的"数字指纹"有多像

#### 第三步：计算 PageRank + 标签特征

```python
    # 6. 计算 PageRank + 标签特征
    rank_fea = self._rank_feature_scores(rank_feature, sres)
```

```python
def _rank_feature_scores(self, query_rfea, search_res):
    """
    计算 PageRank 和标签特征分数
    
    query_rfea: 查询的标签特征，比如 {"狗": 0.8, "宠物": 0.6}
    search_res: 检索结果
    """
    rank_fea = []
    
    # 获取每个 chunk 的 PageRank 分数
    # PageRank 类似网页排名：被引用越多，分数越高
    pageranks = []
    for chunk_id in search_res.ids:
        pageranks.append(search_res.field[chunk_id].get(PAGERANK_FLD, 0))
    
    # 计算标签匹配度
    for i in search_res.ids:
        # 获取 chunk 的标签，比如 {"狗": 0.9, "猫": 0.3}
        chunk_tags = eval(search_res.field[i].get(TAG_FLD, "{}"))
        
        # 计算查询标签和 chunk 标签的点积
        # 比如 查询{"狗":0.8} × chunk{"狗":0.9} = 0.72
        score = 0
        for tag, weight in query_rfea.items():
            if tag in chunk_tags:
                score += weight * chunk_tags[tag]
        
        rank_fea.append(score)
    
    # 最终分数 = 标签匹配度 × 10 + PageRank
    return np.array(rank_fea) * 10. + np.array(pageranks)
```

**小白翻译**：
1. **PageRank**：像网页排名一样，被其他文档引用越多的文档越权威
2. **标签匹配**：如果用户问"狗"，打了"狗"标签的 chunk 分数更高

#### 第四步：三维加权融合

```python
    # 7. 三维加权融合
    # sim = 0.3 × Token相似度 + 0.7 × 向量相似度 + PageRank标签特征
    sim = tkweight * tksim + vtweight * vtsim + rank_fea
    
    return sim, tksim, vtsim
```

**小白翻译**：
- Token 相似度占 30%：关键词匹配，抗干扰强
- 向量相似度占 70%：语义匹配，能捕捉同义词
- PageRank + 标签：权威性加分

### 完整流程图

```
检索返回 Top-50 chunks
    ↓
【Token 相似度】关键词匹配 → tksim
    ↓
【向量相似度】语义匹配 → vtsim
    ↓
【PageRank + 标签】权威性 + 标签匹配 → rank_fea
    ↓
【加权融合】sim = 0.3×tksim + 0.7×vtsim + rank_fea
    ↓
按 sim 排序 → 取 Top-10 送入 LLM
```

### 面试官可能问什么？

| 问题 | 回答 |
|-----|------|
| "重排和检索有什么区别？" | 检索是召回阶段（找全），重排是精排阶段（找准） |
| "Cross-Encoder 和 Bi-Encoder 有什么区别？" | Bi-Encoder 分别编码 query 和 doc，速度快但精度低；Cross-Encoder 联合编码，精度高但速度慢 |
| "PageRank 怎么算到文档上的？" | 基于文档间的引用关系，被引用越多分数越高 |

---

## 职责六：Prompt 引擎与引用标注

### 对应文件：`rag/prompts/generator.py`

### 生活中的比喻

想象你是一个**秘书**，要给老板（AI）准备一份**参考资料**：

1. 你从档案室（知识库）找到了 10 份相关文件
2. 你要把这些文件整理成老板能看懂的格式
3. 你还要**提醒老板**："回答时要标注每条信息来自哪份文件"

### 代码是怎么实现的？

#### 第一步：把检索结果格式化成 Prompt

```python
def kb_prompt(kbinfos, max_tokens, hash_id=False):
    """
    把检索到的 chunks 格式化成知识库 Prompt
    
    参数：
        kbinfos: 检索结果，包含 chunks 列表
        max_tokens: 最大 token 数（防止超出模型限制）
        hash_id: 是否用哈希 ID（默认 False）
    
    返回：
        格式化后的知识列表
    """
    # 提取所有 chunk 的内容
    knowledges = [ck["content_with_weight"] for ck in kbinfos["chunks"]]
    
    # 控制总长度，不要超过 max_tokens 的 97%
    used_token_count = 0
    for i, content in enumerate(knowledges):
        used_token_count += num_tokens_from_string(content)
        if max_tokens * 0.97 < used_token_count:
            # 超长了，截断
            knowledges = knowledges[:i]
            break
    
    # 格式化每个 chunk
    formatted = []
    for i, ck in enumerate(kbinfos["chunks"][:len(knowledges)]):
        # 给每个 chunk 分配一个 ID
        chunk_id = i if not hash_id else hash_str2int(ck["chunk_id"], 500)
        
        # 格式化成：
        # ID: 0
        # ├── Title: 文档标题
        # └── Content:
        #     具体内容...
        text = f"\nID: {chunk_id}"
        text += f"\n├── Title: {ck.get('docnm_kwd', '')}"
        text += f"\n└── Content:\n{ck['content_with_weight']}"
        formatted.append(text)
    
    return formatted
```

**小白翻译**：
1. 拿出检索到的 chunk 内容
2. 算一下总长度，别超过模型限制
3. 给每个 chunk 编个号（ID）
4. 格式化成树形结构，方便阅读

#### 第二步：生成引用标注的 Prompt

```python
# 加载引用标注的 Prompt 模板（从 markdown 文件读取）
CITATION_PROMPT_TEMPLATE = load_prompt("citation_prompt")

# 创建 Jinja2 模板引擎
PROMPT_JINJA_ENV = jinja2.Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)

def citation_prompt(user_defined_prompts: dict = {}) -> str:
    """
    生成引用标注的 Prompt
    
    参数：
        user_defined_prompts: 用户自定义的 Prompt（可选）
    
    返回：
        引用标注的 Prompt 文本
    """
    # 如果用户有自定义的引用规则，就用用户的；否则用默认的
    template_text = user_defined_prompts.get("citation_guidelines", CITATION_PROMPT_TEMPLATE)
    
    # 用 Jinja2 渲染模板
    template = PROMPT_JINJA_ENV.from_string(template_text)
    return template.render()
```

**小白翻译**：
1. 从文件加载默认的引用模板
2. 如果用户有自己的规则，就用用户的
3. 用 Jinja2 模板引擎渲染成最终文本

#### 第三步：上下文长度控制

```python
def memory_prompt(message_list, max_tokens):
    """
    控制 Prompt 的总长度，防止超出模型限制
    
    参数：
        message_list: 消息列表
        max_tokens: 最大 token 数
    
    返回：
        截断后的消息内容列表
    """
    used_token_count = 0
    content_list = []
    
    for message in message_list:
        # 计算这条消息的 token 数
        current_tokens = num_tokens_from_string(message["content"])
        
        # 如果加上这条消息会超过限制，就停止
        if used_token_count + current_tokens > max_tokens * 0.97:
            break
        
        content_list.append(message["content"])
        used_token_count += current_tokens
    
    return content_list
```

**小白翻译**：
1. 一条一条算消息的 token 数
2. 累加，如果超过限制的 97%，就停止
3. 返回能装下的所有消息

#### 第四步：关键词自动提取

```python
async def keyword_extraction(chat_mdl, content, topn=3):
    """
    用 LLM 自动提取关键词
    
    参数：
        chat_mdl: LLM 模型
        content: 要提取关键词的文本
        topn: 提取几个关键词（默认 3 个）
    
    返回：
        关键词字符串，用逗号分隔
    """
    # 加载关键词提取模板
    template = PROMPT_JINJA_ENV.from_string(KEYWORD_PROMPT_TEMPLATE)
    
    # 渲染模板：把 content 和 topn 填入模板
    rendered_prompt = template.render(content=content, topn=topn)
    
    # 组装消息
    msg = [
        {"role": "system", "content": rendered_prompt},
        {"role": "user", "content": "Output: "}
    ]
    
    # 控制长度
    _, msg = message_fit_in(msg, chat_mdl.max_length)
    
    # 调用 LLM 生成关键词
    kwd = await chat_mdl.async_chat(rendered_prompt, msg[1:], {"temperature": 0.2})
    
    return kwd
```

**小白翻译**：
1. 准备一个模板："请从以下文本中提取 {topn} 个关键词：{content}"
2. 把内容填进去
3. 发给 AI，让 AI 提取关键词
4. `temperature=0.2` 让 AI 输出更稳定

### 完整流程图

```
检索结果 Top-10 chunks
    ↓
kb_prompt() 格式化成树形结构
    ↓
citation_prompt() 添加引用要求
    ↓
memory_prompt() 截断到 max_tokens × 0.97
    ↓
组装成最终 Prompt:
    
    System: 你是助手，请基于知识库回答，用 [ID:x] 标注来源
    
    User:
    ## 知识库
    ID: 0
    ├── Title: 养狗指南
    └── Content: 狗是人类的...
    
    ID: 1
    ├── Title: 宠物护理
    └── Content: 宠物需要定期...
    
    问题：狗应该怎么养？
    ↓
发给 LLM 生成回答
```

### 面试官可能问什么？

| 问题 | 回答 |
|-----|------|
| "怎么防止 LLM 幻觉？" | 引用标注约束 + 检索上下文作为证据 + 要求模型只基于提供的内容回答 |
| "LLM 不遵守引用格式怎么办？" | Prompt 中给示例 + 后处理正则提取 + 不强制要求 |
| "上下文超过模型限制怎么办？" | `memory_prompt()` 硬截断到 97%，优先保留前面的 chunk |

---

## 职责七：异步任务 Pipeline

### 对应文件：`rag/svr/task_executor.py`

### 生活中的比喻

想象你是一个**工厂厂长**，工厂要处理很多订单（文档解析任务）：

**问题**：
- 订单太多，同时处理会**挤爆车间**（OOM）
- 有些订单是**重复下单**（相同文件重复上传）
- 需要**实时告诉客户**进度到哪里了

**解决方案**：
1. **限流**：车间同时最多处理 5 个订单
2. **去重**：相同订单直接复用之前的成果
3. **异步**：客户下单后立刻收到回执，不用等做完

### 代码是怎么实现的？

#### 第一步：并发控制（Semaphore）

```python
# 从环境变量读取最大并发数，默认 5
MAX_CONCURRENT_TASKS = int(os.environ.get('MAX_CONCURRENT_TASKS', "5"))
MAX_CONCURRENT_CHUNK_BUILDERS = int(os.environ.get('MAX_CONCURRENT_CHUNK_BUILDERS', "1"))
MAX_CONCURRENT_MINIO = int(os.environ.get('MAX_CONCURRENT_MINIO', '10'))

# 创建信号量（Semaphore）
# Semaphore 就像一个"通行证池"，同时最多发 N 个通行证
task_limiter = asyncio.Semaphore(MAX_CONCURRENT_TASKS)      # 任务级：最多 5 个
chunk_limiter = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_BUILDERS)  # 分块级：最多 1 个
embed_limiter = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_BUILDERS)  # 向量化级：最多 1 个
minio_limiter = asyncio.Semaphore(MAX_CONCURRENT_MINIO)     # IO 级：最多 10 个
```

**小白翻译**：
- `task_limiter` = 工厂同时最多处理 5 个订单
- `chunk_limiter` = 解析车间同时只能做 1 个（CPU 密集型，避免卡死）
- `embed_limiter` = 向量化车间同时只能做 1 个（调用 AI API，限流）
- `minio_limiter` = 仓库同时最多 10 个搬运工（文件读写）

#### 第二步：使用 Semaphore

```python
async def build_chunks(task, progress_callback):
    """
    构建 chunks（解析文档）
    """
    # 获取文件内容
    binary = await get_storage_binary(bucket, name)
    
    # 获取 chunker（解析器）
    chunker = FACTORY[task["parser_id"].lower()]
    
    # ===== 使用 Semaphore 控制并发 =====
    # async with 会自动申请通行证，做完后释放
    async with chunk_limiter:
        cks = await thread_pool_exec(
            chunker.chunk,
            task["name"],
            binary=binary,
            from_page=task["from_page"],
            to_page=task["to_page"],
            lang=task["language"],
            callback=progress_callback,
            ...
        )
    
    return cks
```

**小白翻译**：
1. 拿到文件
2. 找到对应的解析器
3. **申请通行证**（`async with chunk_limiter`）
4. 如果通行证没了，就排队等待
5. 做完后**自动归还通行证**

#### 第三步：任务复用（xxhash）

```python
# 在 task_service.py 中

def reuse_prev_task_chunks(task, prev_task):
    """
    判断是否可以复用之前任务的 chunks
    
    原理：如果两个任务的配置完全一样，结果就是一样的，不用重新算
    """
    # 创建哈希计算器
    hasher = xxhash.xxh64()
    
    # 把分块配置的所有字段加入哈希
    chunking_config = task["parser_config"]
    for field in sorted(chunking_config.keys()):
        hasher.update(str(chunking_config[field]).encode("utf-8"))
    
    # 把页面范围也加入哈希
    hasher.update(str(task.get("from_page", "")).encode("utf-8"))
    hasher.update(str(task.get("to_page", "")).encode("utf-8"))
    
    # 计算摘要
    task_digest = hasher.hexdigest()
    
    # 如果和之前任务的摘要一样，且之前任务已完成，就复用
    if prev_task.digest == task_digest and prev_task.progress == 1.0:
        return prev_task.chunk_ids  # 直接返回之前的 chunk IDs
    
    return None
```

**小白翻译**：
1. 把任务的配置（分块大小、分隔符等）和页面范围**混合哈希**
2. 如果哈希值和之前某个任务一样 → 说明是**相同任务**
3. 直接复用之前的结果，**跳过重新解析**

#### 第四步：向量化批处理

```python
async def embedding(docs, mdl, parser_config=None, callback=None):
    """
    对 chunks 进行向量化
    
    参数：
        docs: chunk 列表
        mdl: Embedding 模型
        parser_config: 解析配置
        callback: 进度回调
    """
    # 提取标题和内容
    tts, cnts = [], []
    for d in docs:
        tts.append(d.get("docnm_kwd", "Title"))  # 标题
        cnts.append(d["content_with_weight"])     # 内容
    
    # 对标题向量化（只取第一个，然后复制给所有 chunks）
    vts, _ = await thread_pool_exec(mdl.encode, tts[0:1])
    tts = np.tile(vts[0], (len(cnts), 1))  # 复制
    
    # 对内容分批向量化
    cnts_ = np.array([])
    for i in range(0, len(cnts), settings.EMBEDDING_BATCH_SIZE):
        # 使用 embed_limiter 控制并发
        async with embed_limiter:
            vts, _ = await thread_pool_exec(
                batch_encode, 
                cnts[i: i + settings.EMBEDDING_BATCH_SIZE]
            )
        # 拼接结果
        if len(cnts_) == 0:
            cnts_ = vts
        else:
            cnts_ = np.concatenate((cnts_, vts), axis=0)
    
    # 标题加权融合
    # 公式：最终向量 = 标题权重 × 标题向量 + (1 - 标题权重) × 内容向量
    title_w = float(parser_config.get("filename_embd_weight", 0.1))
    vects = title_w * tts + (1 - title_w) * cnts_
    
    # 把向量保存到每个 chunk
    for i, d in enumerate(docs):
        v = vects[i].tolist()
        d["q_%d_vec" % len(v)] = v  # 比如 q_1024_vec
    
    return token_count, vector_size
```

**小白翻译**：
1. 提取每个 chunk 的**标题**和**内容**
2. 标题向量化后**复制给所有 chunks**（同一文档的 chunk 共享标题向量）
3. 内容分批向量化（一批 16~64 个，控制 API 调用频率）
4. **加权融合**：标题占 10%，内容占 90%
5. 保存到 chunk 的 `q_1024_vec` 字段

#### 第五步：插入数据库

```python
async def insert_chunks(task_id, task_tenant_id, task_dataset_id, chunks, progress_callback):
    """
    把 chunks 插入向量数据库（ES 或 Infinity）
    """
    # 分批插入，每批 128 个
    for b in range(0, len(chunks), settings.DOC_BULK_SIZE):
        # 插入一批
        await thread_pool_exec(
            settings.docStoreConn.insert, 
            chunks[b:b + settings.DOC_BULK_SIZE],
            search.index_name(task_tenant_id), 
            task_dataset_id
        )
        
        # 检查任务是否被取消
        if has_canceled(task_id):
            progress_callback(-1, msg="Task has been canceled.")
            return False
        
        # 更新进度
        if b % 128 == 0:
            progress_callback(prog=0.8 + 0.1 * (b + 1) / len(chunks), msg="")
    
    return True
```

**小白翻译**：
1. 把 chunks 分成小批（每批 128 个）
2. 一批一批插入数据库
3. 每插一批检查一下：任务被取消了吗？
4. 更新进度条

### 完整流程图

```
用户上传文档
    ↓
创建任务 → 放入 Redis 队列
    ↓
Worker 消费任务
    ↓
do_handle_task()
    ├── 绑定 Embedding 模型
    ├── init_kb() 初始化知识库
    ├── build_chunks() 解析文档（受 chunk_limiter 限制）
    │       ├── 获取文件
    │       ├── 调用解析器
    │       ├── 生成关键词（可选）
    │       ├── 生成问题（可选）
    │       └── 返回 chunks
    ├── embedding() 向量化（受 embed_limiter 限制）
    │       ├── 标题向量化
    │       ├── 内容分批向量化
    │       └── 加权融合
    └── insert_chunks() 插入数据库（受 minio_limiter 限制）
                ↓
        任务完成，更新进度
```

### 面试官可能问什么？

| 问题 | 回答 |
|-----|------|
| "为什么用异步而不是多线程？" | Python GIL 限制，多线程不适合 CPU 密集型；asyncio 适合 IO 密集型 |
| "Semaphore 和 Lock 的区别？" | Semaphore 允许 N 个同时通过，Lock 只允许 1 个；Semaphore 适合控制并发度 |
| "xxhash 冲突怎么办？" | 冲突概率极低（64 位哈希）；即使冲突，最多是误复用，不会导致错误结果 |
| "为什么标题权重是 0.1？" | 实验得出，太高会稀释内容信息，太低失去标题的聚合作用 |

---

## 总结：7 个职责的关系图

```
┌─────────────────────────────────────────────────────────────┐
│                        RAGFlow 全流程                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ① 文档解析（deepdoc/parser/pdf_parser.py）                  │
│     ├── 乱码检测 → OCR 回退                                  │
│     └── 输出：sections（文字）+ tables（表格）                │
│                          ↓                                  │
│  ② 解析路由（rag/app/naive.py）                              │
│     ├── PARSERS 字典选择解析器                               │
│     └── 统一接口：(sections, tables, parser)                 │
│                          ↓                                  │
│  ③ 文本分块（rag/nlp/__init__.py）                           │
│     ├── naive_merge() 重叠分块                               │
│     └── 输出：chunks（固定大小文本块）                        │
│                          ↓                                  │
│  ④ 向量化（rag/svr/task_executor.py）                        │
│     ├── embedding() 标题+内容加权融合                        │
│     └── 输出：带向量的 chunks                                │
│                          ↓                                  │
│  ⑤ 存储（rag/svr/task_executor.py）                          │
│     ├── insert_chunks() 写入 ES/Infinity                    │
│     └── 输出：可检索的知识库                                 │
│                          ↓                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              用户提问（查询阶段）                      │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ↓                                  │
│  ⑥ 混合检索（rag/nlp/search.py）                             │
│     ├── BM25 + 向量检索 → FusionExpr 融合                   │
│     └── 降级重试机制                                        │
│                          ↓                                  │
│  ⑦ 重排（rag/nlp/search.py）                                 │
│     ├── Token + 向量 + PageRank 三维加权                    │
│     └── 输出：Top-10 候选 chunks                            │
│                          ↓                                  │
│  ⑧ Prompt 工程（rag/prompts/generator.py）                    │
│     ├── kb_prompt() 格式化知识库                            │
│     ├── citation_prompt() 引用标注                         │
│     └── memory_prompt() 长度控制                           │
│                          ↓                                  │
│  ⑨ LLM 生成回答（rag/llm/chat_model.py）                     │
│     ├── 流式生成                                           │
│     └── insert_citations() 插入引用链接                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 背诵口诀

| 职责 | 一句话 | 核心文件 |
|-----|--------|---------|
| ① 乱码检测 | "两层检测，PUA+字体，乱码走 OCR" | `pdf_parser.py` |
| ② 适配层 | "策略模式，PARSERS 字典，统一接口" | `naive.py` |
| ③ 重叠分块 | " overlapped_percent，尾部拼头部，防截断" | `rag/nlp/__init__.py` |
| ④ 混合检索 | "BM25+向量，FusionExpr，降级重试" | `search.py` |
| ⑤ 混合重排 | "Token+向量+PageRank，三维加权" | `search.py` |
| ⑥ Prompt 引擎 | "Jinja2 模板，引用标注，防幻觉" | `generator.py` |
| ⑦ 异步 Pipeline | "Semaphore 限流，xxhash 复用，批处理" | `task_executor.py` |
