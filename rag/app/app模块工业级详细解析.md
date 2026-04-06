# RAGFlow rag/app 模块工业级详细解析

## 一、模块架构总览

### 1.1 模块定位

`rag/app`模块是RAGFlow项目的文档解析核心模块，负责将各种格式的文档转换为结构化的文本块（chunks），供后续的向量化存储和检索使用。

### 1.2 文件结构

| 文件名 | 功能描述 | 支持格式 |
|--------|----------|----------|
| naive.py | 基础解析器，被其他模块依赖 | docx, pdf, xlsx, txt, md, html, epub, json, doc |
| audio.py | 音频转录解析器 | da, wav, mp3, aac, flac, ogg, aiff, au, midi, wma |
| book.py | 书籍解析器 | docx, pdf, txt, html, doc |
| email.py | 邮件解析器 | eml |
| laws.py | 法律文档解析器 | docx, pdf, txt, md, html, doc |
| manual.py | 手册文档解析器 | pdf, docx |
| one.py | 单文档解析器（一文件一块） | docx, pdf, xlsx, txt, md, html, doc |
| paper.py | 学术论文解析器 | pdf |
| picture.py | 图片/视频解析器 | jpg, png, mp4, mov, avi, flv, mpeg, mp4, webm, wmv |
| presentation.py | 演示文稿解析器 | ppt, pptx, pdf |
| qa.py | 问答对解析器 | xlsx, txt, csv, pdf, md, docx |
| resume.py | 简历解析器 | pdf, docx, txt |
| table.py | 表格解析器 | xlsx, txt, csv |
| tag.py | 标签解析器 | xlsx, txt, csv |

### 1.3 核心依赖关系

```
deepdoc.parser (基础解析器基类)
    ├── PdfParser → naive.Pdf, book.Pdf, laws.Pdf, manual.Pdf, one.Pdf, paper.Pdf, presentation.Pdf, qa.Pdf
    ├── DocxParser → naive.Docx, laws.Docx, manual.Docx, qa.Docx
    ├── ExcelParser → qa.Excel, table.Excel
    └── MarkdownParser → naive.Markdown

rag.nlp (NLP工具)
    ├── rag_tokenizer → 分词器
    ├── tokenize_chunks → 文本块token化
    ├── naive_merge → 文本合并
    └── hierarchical_merge → 层级合并

api.db.services.llm_service.LLMBundle (LLM调用封装)
    ├── SPEECH2TEXT → audio.py
    ├── IMAGE2TEXT → picture.py, naive.py
    └── CHAT → resume.py
```

---

## 二、naive.py 基础解析器模块

### 2.1 模块级常量与函数

#### PARSERS 字典

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

**设计意图**：策略模式分发器，根据`layout_recognizer`参数选择不同的PDF解析引擎。

**数据流向**：`chunk()`函数根据配置从PARSERS获取对应的解析函数，调用后返回`(sections, tables, pdf_parser)`三元组。

---

#### by_deepdoc 函数

**完整签名**：
```python
def by_deepdoc(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, pdf_cls=None, **kwargs)
```

**功能**：使用DeepDOC引擎解析PDF文档。

**实现步骤**：
1. 创建PDF解析器实例：`pdf_parser = pdf_cls() if pdf_cls else Pdf()`
2. 调用解析器获取sections和tables：`sections, tables = pdf_parser(filename if not binary else binary, from_page, to_page, callback)`
3. 调用`vision_figure_parser_pdf_wrapper`处理表格中的图片
4. 返回`(sections, tables, pdf_parser)`三元组

**依赖调用**：
- `Pdf`类（本模块定义）
- `vision_figure_parser_pdf_wrapper`（deepdoc.parser.figure_parser）

**返回值**：`(sections, tables, pdf_parser)`，sections为文本段落列表，tables为表格列表。

---

#### by_mineru 函数

**完整签名**：
```python
def by_mineru(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, pdf_cls=None, parse_method: str = "raw", mineru_llm_name: str | None = None, tenant_id: str | None = None, **kwargs)
```

**功能**：使用MinerU引擎解析PDF文档。

**实现步骤**：
1. 检查`tenant_id`是否存在
2. 如果未指定`mineru_llm_name`，从环境变量或数据库查询获取
3. 通过`LLMBundle`加载OCR模型配置
4. 调用模型的`parse_pdf`方法解析文档
5. 异常时返回`(None, None, None)`

**依赖调用**：
- `TenantLLMService.ensure_mineru_from_env`
- `TenantLLMService.query`
- `get_model_config_by_type_and_name`
- `LLMBundle`

**异常处理**：
- 捕获所有异常并记录日志
- 失败时调用`callback(-1, "MinerU not found.")`

---

#### by_docling 函数

**完整签名**：
```python
def by_docling(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, pdf_cls=None, **kwargs)
```

**功能**：使用Docling引擎解析PDF文档。

**实现步骤**：
1. 创建`DoclingParser`实例
2. 检查安装状态：`pdf_parser.check_installation()`
3. 调用`parse_pdf`方法，传入环境变量配置
4. 返回解析结果

**依赖调用**：
- `DoclingParser`（deepdoc.parser.docling_parser）

**环境变量**：
- `DOCLING_OUTPUT_DIR`：输出目录
- `DOCLING_DELETE_OUTPUT`：是否删除临时输出
- `DOCLING_SERVER_URL`：Docling服务URL

---

#### by_tcadp 函数

**完整签名**：
```python
def by_tcadp(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, pdf_cls=None, **kwargs)
```

**功能**：使用腾讯云文档解析服务解析PDF。

**实现步骤**：
1. 创建`TCADPParser`实例
2. 检查安装状态
3. 调用`parse_pdf`方法

**依赖调用**：
- `TCADPParser`（deepdoc.parser.tcadp_parser）

---

#### by_paddleocr 函数

**完整签名**：
```python
def by_paddleocr(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, pdf_cls=None, parse_method: str = "raw", paddleocr_llm_name: str | None = None, tenant_id: str | None = None, **kwargs)
```

**功能**：使用PaddleOCR引擎解析PDF文档。

**实现步骤**：
1. 检查`tenant_id`是否存在
2. 获取PaddleOCR模型配置
3. 通过`LLMBundle`加载模型
4. 调用`parse_pdf`方法

**依赖调用**：
- `TenantLLMService.ensure_paddleocr_from_env`
- `LLMBundle`

---

#### by_plaintext 函数

**完整签名**：
```python
def by_plaintext(filename, binary=None, from_page=0, to_page=100000, callback=None, **kwargs)
```

**功能**：使用纯文本模式解析PDF。

**实现步骤**：
1. 检查`layout_recognizer`配置
2. 如果是"Plain Text"，使用`PlainParser`
3. 否则使用`VisionParser`（需要视觉模型）

**依赖调用**：
- `PlainParser`（deepdoc.parser.pdf_parser）
- `VisionParser`（deepdoc.parser.pdf_parser）
- `LLMBundle`

---

#### _normalize_section_text_for_rtl_presentation_forms 函数

**完整签名**：
```python
def _normalize_section_text_for_rtl_presentation_forms(sections)
```

**功能**：规范化从右到左（RTL）语言的文本表示形式。

**实现步骤**：
1. 遍历sections列表
2. 对每个section，判断其类型（tuple、list或字符串）
3. 提取文本内容，调用`normalize_arabic_presentation_forms`规范化
4. 返回规范化后的sections

**依赖调用**：
- `normalize_arabic_presentation_forms`（common.text_utils）

---

### 2.2 Docx 类

**类注释与设计意图**：
Word文档解析器，继承自`DocxParser`基类，负责将.docx文件解析为结构化的文本块列表，支持提取文本、图片、表格，并维护文档的层级标题结构。

**继承关系**：
```
deepdoc.parser.DocxParser (基类)
    └── naive.Docx (本类)
```

#### 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| doc | Document | python-docx库的文档对象 | 存储解析后的文档结构，用于遍历段落和表格 |

#### 构造方法

```python
def __init__(self):
    pass
```

**作用**：空构造函数，无需初始化任何成员变量。

#### __clean 方法

**完整签名**：
```python
def __clean(self, line) -> str
```

**功能**：清理文本行中的特殊字符。

**实现步骤**：
1. 使用正则表达式将全角空格`\u3000`替换为半角空格
2. 调用`strip()`去除首尾空白
3. 返回清理后的文本

**数据流向**：输入原始文本行 → 输出清理后的文本行。

#### __get_nearest_title 方法

**完整签名**：
```python
def __get_nearest_title(self, table_index, filename) -> str
```

**功能**：获取表格前最近的层级标题结构。

**实现步骤**：
1. 从filename提取文档名称
2. 遍历文档所有块（段落和表格），维护块列表
3. 定位目标表格位置
4. 从表格位置向前搜索最近的标题段落
5. 递归查找所有父级标题
6. 按层级排序，生成层级路径字符串

**数据流向**：
- 输入：表格索引、文件名
- 输出：层级标题字符串，格式为"文档名 > 一级标题 > 二级标题"

**依赖调用**：
- `Paragraph`（docx.text.paragraph）

**校验逻辑**：
- 检查块类型（段落或表格）
- 验证标题样式名称是否匹配`Heading\s*(\d+)`模式
- 支持最多7级标题

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000) -> list[tuple]
```

**功能**：解析Word文档，返回结构化的内容列表。

**实现步骤**：
1. 加载文档：`self.doc = Document(filename) if not binary else Document(BytesIO(binary))`
2. 初始化变量：`pn`(页码)、`lines`(结果列表)、`last_image`(暂存图片)、`table_idx`(表格索引)
3. 定义内部函数`flush_last_image()`：将暂存的图片添加到结果列表
4. 遍历文档所有块：
   - 处理段落块：
     - 提取文本和样式
     - 处理Caption样式（图片说明）
     - 提取段落中的图片
     - 检测分页符更新页码
   - 处理表格块：
     - 获取表格前最近的标题
     - 将表格转换为HTML格式
     - 处理合并单元格（colspan）
5. 返回结果列表，每个元素为`(text, image, table)`元组

**数据流向**：
- 输入：文件路径或二进制内容、页码范围
- 输出：`[(text, image, table), ...]`列表

**依赖调用**：
- `Document`（docx）
- `Paragraph`（docx.text.paragraph）
- `DocxTable`（docx.table）
- `self.get_picture`（继承自DocxParser）

**异常处理**：
- 表格解析错误时记录警告日志并跳过

#### to_markdown 方法

**完整签名**：
```python
def to_markdown(self, filename=None, binary=None, inline_images: bool = True) -> str
```

**功能**：将Word文档转换为Markdown格式。

**实现步骤**：
1. 打开文档文件
2. 定义内部函数`_convert_image_to_base64`：将图片转换为base64编码
3. 使用mammoth库转换为HTML
4. 使用markdownify将HTML转换为Markdown
5. 返回Markdown文本

**依赖调用**：
- `mammoth.convert_to_html`
- `markdownify`

---

### 2.3 Pdf 类

**类注释与设计意图**：
PDF文档解析器，继承自`PdfParser`基类，使用OCR技术识别PDF中的文本和表格，支持布局分析和表格提取。

**继承关系**：
```
deepdoc.parser.PdfParser (基类)
    └── naive.Pdf (本类)
```

#### 成员变量

继承自基类，无新增成员变量。

#### 构造方法

```python
def __init__(self):
    super().__init__()
```

**作用**：调用父类构造函数初始化。

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, zoomin=3, callback=None, separate_tables_figures=False)
```

**功能**：解析PDF文档，返回文本段落和表格。

**实现步骤**：
1. 记录开始时间
2. 调用`__images__`渲染PDF页面为图片
3. 调用`_layouts_rec`进行布局分析
4. 调用`_table_transformer_job`进行表格分析
5. 调用`_text_merge`合并文本
6. 根据`separate_tables_figures`参数：
   - True：分别提取表格和图片
   - False：提取表格并合并文本
7. 返回`[(text, line_tag), ...]`格式的sections和tables

**数据流向**：
- 输入：文件路径或二进制内容、页码范围、缩放因子、回调函数
- 输出：`(sections, tables)`或`(sections, tbls, figures)`

**依赖调用**：
- `self.__images__`（继承自PdfParser）
- `self._layouts_rec`（继承自PdfParser）
- `self._table_transformer_job`（继承自PdfParser）
- `self._text_merge`（继承自PdfParser）
- `self._extract_table_figure`（继承自PdfParser）
- `self._concat_downward`（继承自PdfParser）
- `self._naive_vertical_merge`（继承自PdfParser）
- `self._line_tag`（继承自PdfParser）

---

### 2.4 Markdown 类

**类注释与设计意图**：
Markdown文档解析器，继承自`MarkdownParser`基类，负责解析Markdown文件，提取文本内容、表格和图片URL。

**继承关系**：
```
deepdoc.parser.MarkdownParser (基类)
    └── naive.Markdown (本类)
```

#### 成员变量

继承自基类，无新增成员变量。

#### md_to_html 方法

**完整签名**：
```python
def md_to_html(self, sections) -> BeautifulSoup
```

**功能**：将Markdown文本转换为HTML并解析为BeautifulSoup对象。

**实现步骤**：
1. 检查sections是否为空
2. 提取文本内容
3. 使用`markdown`库转换为HTML
4. 使用BeautifulSoup解析HTML
5. 返回soup对象

**依赖调用**：
- `markdown`（markdown库）
- `BeautifulSoup`（bs4）

#### get_hyperlink_urls 方法

**完整签名**：
```python
def get_hyperlink_urls(self, soup) -> list
```

**功能**：从HTML中提取所有超链接URL。

**实现步骤**：
1. 检查soup对象是否存在
2. 使用`soup.find_all("a")`查找所有链接标签
3. 提取`href`属性
4. 返回URL集合

#### extract_image_urls_with_lines 方法

**完整签名**：
```python
def extract_image_urls_with_lines(self, text) -> list[dict]
```

**功能**：从Markdown文本中提取图片URL及其所在行号。

**实现步骤**：
1. 定义Markdown图片正则：`r"!\[[^\]]*\]\(([^)\s]+)"`
2. 定义HTML图片正则：`r'src=["\\\']([^"\\\'>\\s]+)'`
3. 按行分割文本
4. 逐行匹配正则表达式
5. 使用BeautifulSoup解析HTML图片标签
6. 计算图片标签在原文中的行号
7. 返回`[{"url": url, "line": line_no}, ...]`列表

**数据流向**：
- 输入：Markdown文本
- 输出：图片URL和行号的字典列表

#### load_images_from_urls 方法

**完整签名**：
```python
def load_images_from_urls(self, urls, cache=None) -> tuple[list, dict]
```

**功能**：从URL列表加载图片。

**实现步骤**：
1. 初始化缓存字典
2. 遍历URL列表：
   - 检查缓存
   - 处理HTTP/HTTPS URL：使用requests下载
   - 处理本地路径：使用PIL打开
3. 缓存加载的图片
4. 返回图片列表和缓存字典

**依赖调用**：
- `requests.get`
- `Image.open`（PIL）

**异常处理**：
- 捕获网络请求异常
- 捕获图片打开异常

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, separate_tables=True, delimiter=None, return_section_images=False) -> tuple
```

**功能**：解析Markdown文件。

**实现步骤**：
1. 读取文件内容（从binary或文件路径）
2. 调用`extract_tables_and_remainder`分离表格和正文
3. 创建`MarkdownElementExtractor`提取元素
4. 提取图片URL和行号
5. 加载图片并合并
6. 处理表格（转换为HTML）
7. 返回`(sections, tbls)`或`(sections, tbls, section_images)`

**数据流向**：
- 输入：文件路径或二进制内容
- 输出：sections列表、tables列表、可选的section_images列表

**依赖调用**：
- `find_codec`（rag.nlp）
- `MarkdownElementExtractor`（deepdoc.parser）
- `concat_img`（rag.nlp）
- `reduce`（functools）

---

### 2.5 load_from_xml_v2 函数

**完整签名**：
```python
def load_from_xml_v2(baseURI, rels_item_xml)
```

**功能**：修复python-docx处理NULL引用的问题。

**实现步骤**：
1. 创建`_SerializedRelationships`实例
2. 解析XML内容
3. 过滤掉目标为"NULL"或"../NULL"的关系
4. 返回关系集合

**设计原因**：解决python-docx在处理某些Word文档时出现的"There is no item named 'word/NULL' in the archive"错误。

---

### 2.6 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：文档解析主入口函数，根据文件类型选择解析器并生成文本块。

**实现步骤**：

**阶段1：初始化**
1. 解析配置参数：`parser_config = kwargs.get("parser_config", {...})`
2. 处理子分隔符：解析`children_delimiter`配置
3. 初始化文档元数据：`doc = {"docnm_kwd": filename, "title_tks": ..., "title_sm_tks": ...}`

**阶段2：嵌入文件处理**
1. 检查`is_root`标志
2. 调用`extract_embed_file`提取嵌入文件
3. 递归调用`chunk`处理嵌入文件

**阶段3：根据文件类型分发处理**

**docx文件处理**：
1. 提取超链接（如果配置了`analyze_hyperlink`）
2. 替换`_SerializedRelationships.load_from_xml`修复NULL引用问题
3. 调用`Docx()`解析文档
4. 调用`naive_merge_docx`合并文本块
5. 调用`vision_figure_parser_docx_wrapper_naive`处理图片
6. 调用`doc_tokenize_chunks_with_images`生成token

**pdf文件处理**：
1. 规范化`layout_recognizer`配置
2. 从`PARSERS`字典获取解析器
3. 调用解析器获取sections和tables
4. 调用`append_context2table_image4pdf`添加上下文
5. 调用`tokenize_table`处理表格

**Excel/CSV文件处理**：
1. 检查是否使用TCADP解析器
2. 使用`ExcelParser`解析
3. 根据配置选择HTML或文本输出

**文本文件处理**：
1. 使用`TxtParser`解析
2. 按分隔符分割文本

**Markdown文件处理**：
1. 使用`Markdown`解析器
2. 提取图片URL并加载
3. 使用视觉模型增强图片描述

**HTML文件处理**：
1. 使用`HtmlParser`解析

**EPUB文件处理**：
1. 使用`EpubParser`解析

**JSON文件处理**：
1. 使用`JsonParser`解析

**doc文件处理**：
1. 使用tika解析器

**阶段4：文本合并与token化**
1. 根据是否有图片选择合并策略
2. 调用`naive_merge`或`naive_merge_with_images`
3. 调用`tokenize_chunks`或`tokenize_chunks_with_images`

**阶段5：超链接处理**
1. 提取PDF中的超链接
2. 递归处理链接内容

**数据流向**：
- 输入：文件名、二进制内容、配置参数
- 输出：`[{"content_with_weight": ..., "content_ltks": ..., ...}, ...]`字典列表

**依赖调用**：
- 所有解析器类和函数
- `rag_tokenizer`
- `tokenize_chunks`, `tokenize_table`
- `naive_merge`, `naive_merge_docx`
- `LLMBundle`
- `extract_embed_file`, `extract_links_from_pdf`, `extract_links_from_docx`

**异常处理**：
- 不支持的文件类型抛出`NotImplementedError`
- 嵌入文件处理失败记录错误日志

---

## 三、audio.py 音频解析器模块

### 3.1 chunk 函数

**完整签名**：
```python
def chunk(filename, binary, tenant_id, lang, callback=None, **kwargs) -> list[dict]
```

**功能**：将音频文件转录为文本。

**实现步骤**：
1. 初始化文档元数据
2. 检查文件扩展名是否支持
3. 创建临时文件保存音频
4. 获取租户的语音转文字模型配置
5. 创建`LLMBundle`实例
6. 调用`transcription`方法转录音频
7. 调用`tokenize`生成token
8. 清理临时文件
9. 返回文档列表

**数据流向**：
- 输入：音频文件二进制内容
- 输出：`[{"docnm_kwd": ..., "content_with_weight": ..., ...}]`

**依赖调用**：
- `get_tenant_default_model_by_type`
- `LLMBundle`
- `rag_tokenizer.tokenize`
- `tokenize`

**支持的音频格式**：
`.da`, `.wave`, `.wav`, `.mp3`, `.aac`, `.flac`, `.ogg`, `.aiff`, `.au`, `.midi`, `.wma`, `.realaudio`, `.vqf`, `.oggvorbis`, `.ape`

**异常处理**：
- 捕获所有异常，调用`callback(prog=-1, msg=str(e))`
- finally块确保临时文件被删除

---

## 四、book.py 书籍解析器模块

### 4.1 Pdf 类

**类注释与设计意图**：
书籍专用PDF解析器，继承自`PdfParser`，针对书籍文档的特点进行优化，支持目录表移除和层级合并。

**继承关系**：
```
deepdoc.parser.PdfParser (基类)
    └── book.Pdf (本类)
```

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, zoomin=3, callback=None)
```

**功能**：解析书籍PDF文档。

**实现步骤**：
1. 调用`__images__`渲染PDF页面
2. 调用`_layouts_rec`进行布局分析
3. 调用`_table_transformer_job`进行表格分析
4. 调用`_text_merge`合并文本
5. 提取表格
6. 调用`_naive_vertical_merge`垂直合并
7. 调用`_filter_forpages`过滤页面
8. 调用`_merge_with_same_bullet`合并相同层级
9. 返回sections和tables

**依赖调用**：
- 继承自PdfParser的所有方法
- `_merge_with_same_bullet`（继承自PdfParser）

---

### 4.2 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：解析书籍文档。

**实现步骤**：

**docx文件处理**：
1. 调用`naive.Docx`解析
2. 调用`remove_contents_table`移除目录表
3. 调用`vision_figure_parser_docx_wrapper`处理图片

**pdf文件处理**：
1. 从`PARSERS`获取解析器
2. 调用解析器获取sections和tables

**txt/html/doc文件处理**：
1. 解析文本内容
2. 调用`remove_contents_table`移除目录表

**通用处理**：
1. 调用`make_colon_as_title`处理冒号标题
2. 调用`bullets_category`分析层级符号
3. 如果有层级符号，调用`hierarchical_merge`层级合并
4. 否则调用`naive_merge`普通合并
5. 调用`tokenize_table`和`tokenize_chunks`生成结果

**依赖调用**：
- `naive.Docx`
- `PARSERS`字典
- `remove_contents_table`, `make_colon_as_title`, `bullets_category`
- `hierarchical_merge`, `naive_merge`
- `tokenize_table`, `tokenize_chunks`
- `attach_media_context`

---

## 五、email.py 邮件解析器模块

### 5.1 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：解析.eml格式的邮件文件。

**实现步骤**：
1. 使用`BytesParser`解析邮件
2. 提取邮件头部信息（From, To, Subject等）
3. 定义内部函数`_add_content`递归处理邮件体：
   - 处理`text/plain`部分
   - 处理`text/html`部分
   - 处理`multipart`部分
4. 定义内部函数`_decode_payload`处理编码：
   - 尝试指定编码解码
   - 失败时依次尝试utf-8, gb2312, gbk, gb18030, latin1
5. 合并文本和HTML内容
6. 调用`naive_merge`合并文本块
7. 调用`tokenize_chunks`生成主结果
8. 遍历附件，递归调用`naive_chunk`处理
9. 返回主结果和附件结果的合并列表

**数据流向**：
- 输入：.eml文件二进制内容
- 输出：`[{"content_with_weight": ..., ...}, ...]`包含邮件正文和附件内容

**依赖调用**：
- `BytesParser`（email.parser）
- `TxtParser.parser_txt`（deepdoc.parser）
- `HtmlParser.parser_txt`（deepdoc.parser）
- `naive_merge`, `tokenize_chunks`
- `naive_chunk`（rag.app.naive）

**异常处理**：
- 附件处理失败时静默跳过

---

## 六、laws.py 法律文档解析器模块

### 6.1 Docx 类

**类注释与设计意图**：
法律文档专用Word解析器，继承自`DocxParser`，支持提取文档的层级标题结构。

**继承关系**：
```
deepdoc.parser.DocxParser (基类)
    └── laws.Docx (本类)
```

#### __clean 方法

**完整签名**：
```python
def __clean(self, line) -> str
```

**功能**：清理文本行中的全角空格。

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000) -> list
```

**功能**：解析法律Word文档，提取层级结构。

**实现步骤**：
1. 加载文档
2. 调用`bullets_category`分析项目符号类型
3. 遍历段落：
   - 调用`docx_question_level`提取标题层级
   - 记录所有出现的层级
4. 计算二级标题层级`h2_level`
5. 创建`Node`根节点
6. 调用`root.build_tree`构建层级树
7. 调用`root.get_tree`获取扁平化结果

**数据流向**：
- 输入：Word文档
- 输出：按层级组织的文本块列表

**依赖调用**：
- `bullets_category`, `docx_question_level`
- `Node`（rag.nlp）

---

### 6.2 Pdf 类

**类注释与设计意图**：
法律文档专用PDF解析器，继承自`PdfParser`，设置`model_speciess`为`ParserType.LAWS.value`。

**继承关系**：
```
deepdoc.parser.PdfParser (基类)
    └── laws.Pdf (本类)
```

#### 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| model_speciess | str | 解析器类型标识 | 用于指定使用法律文档专用的解析模型 |

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, zoomin=3, callback=None)
```

**功能**：解析法律PDF文档。

**实现步骤**：
1. 渲染PDF页面为图片
2. 进行布局分析
3. 调用`_naive_vertical_merge`垂直合并
4. 返回sections（包含位置标签）

---

### 6.3 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：解析法律文档。

**实现步骤**：

**docx文件处理**：
1. 调用`Docx()`解析
2. 直接调用`tokenize_chunks`返回

**pdf文件处理**：
1. 从`PARSERS`获取解析器
2. 调用解析器获取sections

**txt/md/html/doc文件处理**：
1. 解析文本内容

**通用处理**：
1. 调用`remove_contents_table`移除目录表
2. 调用`make_colon_as_title`处理冒号标题
3. 调用`bullets_category`分析层级符号
4. 调用`tree_merge`进行树形合并
5. 调用`tokenize_chunks`生成结果

**依赖调用**：
- `Docx`, `Pdf`（本模块）
- `PARSERS`字典
- `remove_contents_table`, `make_colon_as_title`, `bullets_category`
- `tree_merge`
- `tokenize_chunks`

---

## 七、manual.py 手册解析器模块

### 7.1 Pdf 类

**类注释与设计意图**：
手册文档专用PDF解析器，继承自`PdfParser`，支持提取位置信息和层级结构。

**继承关系**：
```
deepdoc.parser.PdfParser (基类)
    └── manual.Pdf (本类)
```

#### 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| model_speciess | str | 解析器类型标识 | 设置为`ParserType.MANUAL.value` |

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, zoomin=3, callback=None)
```

**功能**：解析手册PDF文档。

**实现步骤**：
1. 渲染PDF页面
2. 进行布局分析和表格分析
3. 合并文本
4. 提取表格
5. 调用`_concat_downward`向下连接
6. 调用`_filter_forpages`过滤页面
7. 清理多余空白字符
8. 返回sections（包含文本、布局编号、位置信息）和tables

**依赖调用**：
- `self.get_position`（继承自PdfParser）

---

### 7.2 Docx 类

**类注释与设计意图**：
手册文档专用Word解析器，继承自`DocxParser`，支持提取问答对结构。

**继承关系**：
```
deepdoc.parser.DocxParser (基类)
    └── manual.Docx (本类)
```

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, callback=None) -> tuple
```

**功能**：解析手册Word文档，提取问答对。

**实现步骤**：
1. 加载文档
2. 初始化变量：`last_answer`, `last_image`, `question_stack`, `level_stack`
3. 遍历段落：
   - 调用`docx_question_level`提取标题层级
   - 非标题段落：追加到`last_answer`，提取图片
   - 标题段落：
     - 保存之前的问答对
     - 更新问题栈和层级栈
4. 处理表格：转换为HTML格式
5. 返回问答对列表和表格列表

**数据流向**：
- 输入：Word文档
- 输出：`([(question, answer, image), ...], tables)`

**依赖调用**：
- `docx_question_level`
- `concat_img`
- `self.get_picture`（继承自DocxParser）

---

### 7.3 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：解析手册文档。

**实现步骤**：

**pdf文件处理**：
1. 从`PARSERS`获取解析器，传入`parse_method="manual"`
2. 规范化section格式为`(txt, layoutno, poss)`
3. 分析大纲结构：
   - 如果大纲比例>3%，使用大纲层级
   - 否则使用`bullets_category`和`title_frequency`分析
4. 生成section ID
5. 按位置排序sections
6. 合并相邻sections
7. 调用`vision_figure_parser_pdf_wrapper`处理图片
8. 调用`tokenize_table`和`tokenize_chunks`

**docx文件处理**：
1. 调用`Docx()`解析
2. 调用`vision_figure_parser_docx_wrapper`处理图片
3. 调用`tokenize_table`处理表格
4. 遍历问答对，生成文档块

**依赖调用**：
- `Pdf`, `Docx`（本模块）
- `PARSERS`字典
- `bullets_category`, `title_frequency`
- `vision_figure_parser_pdf_wrapper`, `vision_figure_parser_docx_wrapper`
- `tokenize_table`, `tokenize_chunks`
- `attach_media_context`

---

## 八、one.py 单文档解析器模块

### 8.1 Pdf 类

**类注释与设计意图**：
单文档PDF解析器，继承自`PdfParser`，将整个文档作为一个文本块处理，保持原文顺序。

**继承关系**：
```
deepdoc.parser.PdfParser (基类)
    └── one.Pdf (本类)
```

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, zoomin=3, callback=None)
```

**功能**：解析PDF文档，保持原文顺序。

**实现步骤**：
1. 渲染PDF页面
2. 进行布局分析和表格分析
3. 合并文本
4. 提取表格
5. 调用`_concat_downward`向下连接
6. 按位置排序sections
7. 返回sections和tables

---

### 8.2 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：将整个文档解析为一个文本块。

**实现步骤**：

**docx文件处理**：
1. 调用`naive.Docx`解析
2. 分类处理文本、图片、表格
3. 调用`vision_figure_parser_docx_wrapper_naive`处理图片
4. 合并所有文本

**pdf文件处理**：
1. 从`PARSERS`获取解析器
2. 调用解析器获取sections和tables
3. 将表格内容追加到sections
4. 合并所有文本

**xlsx文件处理**：
1. 使用`ExcelParser.html`解析为HTML

**txt/md/html/doc文件处理**：
1. 解析文本内容

**通用处理**：
1. 调用`tokenize`生成单个文档块
2. 返回只包含一个元素的列表

**依赖调用**：
- `naive.Docx`
- `PARSERS`字典
- `ExcelParser`
- `vision_figure_parser_docx_wrapper_naive`
- `tokenize`

---

## 九、paper.py 学术论文解析器模块

### 9.1 Pdf 类

**类注释与设计意图**：
学术论文专用PDF解析器，继承自`PdfParser`，支持提取标题、作者、摘要和章节结构。

**继承关系**：
```
deepdoc.parser.PdfParser (基类)
    └── paper.Pdf (本类)
```

#### 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| model_speciess | str | 解析器类型标识 | 设置为`ParserType.PAPER.value` |

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, zoomin=3, callback=None) -> dict
```

**功能**：解析学术论文PDF。

**实现步骤**：
1. 渲染PDF页面
2. 进行布局分析和表格分析
3. 合并文本
4. 计算列宽，检测双栏布局
5. 如果是双栏，调用`sort_X_by_page`重排序
6. 清理空白字符
7. 提取标题和作者：
   - 遍历前32个box
   - 查找title类型的布局
   - 提取后续作者信息
8. 提取摘要：
   - 匹配"abstract"或"摘要"关键词
   - 提取摘要内容
9. 返回包含title, authors, abstract, sections, tables的字典

**数据流向**：
- 输入：PDF文件
- 输出：`{"title": ..., "authors": ..., "abstract": ..., "sections": [...], "tables": [...]}`

**依赖调用**：
- `self.sort_X_by_page`（继承自PdfParser）
- `self._line_tag`（继承自PdfParser）

---

### 9.2 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：解析学术论文。

**实现步骤**：
1. 从`PARSERS`获取解析器
2. 如果是DeepDOC，直接调用`Pdf()`解析
3. 否则调用通用解析器
4. 调用`vision_figure_parser_pdf_wrapper`处理图片
5. 处理摘要：
   - 创建独立chunk
   - 添加`important_kwd`字段
   - 裁剪摘要区域图片
6. 处理章节：
   - 调用`bullets_category`分析层级
   - 调用`title_frequency`确定标题层级
   - 按section ID合并相邻章节
7. 调用`tokenize_table`和`tokenize_chunks`

**依赖调用**：
- `Pdf`（本模块）
- `PARSERS`字典
- `bullets_category`, `title_frequency`
- `vision_figure_parser_pdf_wrapper`
- `tokenize_table`, `tokenize_chunks`
- `add_positions`
- `attach_media_context`

---

## 十、picture.py 图片解析器模块

### 10.1 模块级常量

```python
VIDEO_EXTS = [".mp4", ".mov", ".avi", ".flv", ".mpeg", ".mpg", ".webm", ".wmv", ".3gp", ".3gpp", ".mkv"]
ocr = OCR()
```

**设计意图**：`VIDEO_EXTS`定义支持的视频格式，`ocr`是OCR识别器的全局实例。

---

### 10.2 chunk 函数

**完整签名**：
```python
def chunk(filename, binary, tenant_id, lang, callback=None, **kwargs) -> list[dict]
```

**功能**：解析图片或视频文件。

**实现步骤**：

**视频文件处理**：
1. 设置`doc_type_kwd`为"video"
2. 获取视觉模型配置
3. 创建`LLMBundle`实例
4. 调用`async_chat`处理视频
5. 调用`tokenize`生成结果

**图片文件处理**：
1. 使用PIL打开图片
2. 设置`doc_type_kwd`为"image"
3. 调用OCR识别文字
4. 如果OCR结果较长（>32字符），直接使用OCR结果
5. 否则调用视觉模型描述图片
6. 调用`tokenize`生成结果
7. 调用`attach_media_context`添加媒体上下文

**数据流向**：
- 输入：图片/视频二进制内容
- 输出：`[{"docnm_kwd": ..., "image": ..., "content_with_weight": ..., ...}]`

**依赖调用**：
- `OCR`（deepdoc.vision）
- `LLMBundle`
- `get_tenant_default_model_by_type`
- `tokenize`
- `attach_media_context`

**异常处理**：
- 捕获所有异常，调用`callback(prog=-1, msg=str(e))`

---

### 10.3 vision_llm_chunk 函数

**完整签名**：
```python
def vision_llm_chunk(binary, vision_model, prompt=None, callback=None) -> str
```

**功能**：使用视觉语言模型处理图片生成Markdown文本。

**实现步骤**：
1. 检查图片尺寸，跳过过小的图片
2. 将图片保存为JPEG或PNG格式
3. 调用`vision_model.describe_with_prompt`描述图片
4. 调用`clean_markdown_block`清理Markdown格式
5. 返回描述文本

**依赖调用**：
- `clean_markdown_block`（common.string_utils）

---

## 十一、presentation.py 演示文稿解析器模块

### 11.1 Pdf 类

**类注释与设计意图**：
演示文稿PDF解析器，继承自`PdfParser`，每页作为一个独立chunk处理。

**继承关系**：
```
deepdoc.parser.PdfParser (基类)
    └── presentation.Pdf (本类)
```

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, zoomin=3, callback=None, **kwargs) -> tuple
```

**功能**：解析演示文稿PDF，每页生成一个结果。

**实现步骤**：
1. 渲染PDF页面
2. 进行布局分析和表格分析
3. 合并文本
4. 提取表格
5. 按页面组织内容：
   - 收集每页的文本
   - 收集每页的表格和图片
   - 按垂直位置排序
6. 返回`[(full_page_text, page_img), ...]`列表

**数据流向**：
- 输入：PDF文件
- 输出：`[(页面文本, 页面图片), ...]`

---

### 11.2 PlainPdf 类

**类注释与设计意图**：
纯文本PDF解析器，继承自`PlainParser`，直接提取PDF文本不做布局分析。

**继承关系**：
```
deepdoc.parser.PlainParser (基类)
    └── presentation.PlainPdf (本类)
```

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, callback=None, **kwargs) -> tuple
```

**功能**：使用pdf2_read提取纯文本。

**实现步骤**：
1. 使用`pdf2_read`打开PDF
2. 遍历指定页面范围
3. 调用`page.extract_text()`提取文本
4. 返回`[(txt, None), ...]`列表

---

### 11.3 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, parser_config=None, **kwargs) -> list[dict]
```

**功能**：解析演示文稿文件。

**实现步骤**：

**PPT/PPTX文件处理**：
1. 创建`RAGFlowPptParser`实例
2. 遍历每页，提取文本和缩略图
3. 设置`doc_type_kwd`为"image"
4. 设置位置信息
5. 如果python-pptx失败，尝试tika作为备选

**PDF文件处理**：
1. 从`PARSERS`获取解析器
2. 调用解析器获取sections
3. 遍历每页：
   - 提取文本和图片
   - 设置位置信息
   - 调用`tokenize`生成chunk

**依赖调用**：
- `Pdf`, `PlainPdf`（本模块）
- `RAGFlowPptParser`（deepdoc.parser.ppt_parser）
- `PARSERS`字典
- `tokenize`
- `ensure_pil_image`, `is_image_like`（rag.utils.lazy_image）

**异常处理**：
- PPT解析失败时尝试tika备选方案
- tika不可用时抛出`NotImplementedError`

---

## 十二、qa.py 问答对解析器模块

### 12.1 Excel 类

**类注释与设计意图**：
问答对Excel解析器，继承自`ExcelParser`，从Excel文件提取问答对。

**继承关系**：
```
deepdoc.parser.ExcelParser (基类)
    └── qa.Excel (本类)
```

#### __call__ 方法

**完整签名**：
```python
def __call__(self, fnm, binary=None, callback=None) -> list[tuple]
```

**功能**：从Excel提取问答对。

**实现步骤**：
1. 加载工作簿
2. 计算总行数
3. 遍历所有工作表：
   - 遍历每行
   - 提取前两列作为问题和答案
   - 记录失败行
4. 调用`is_english`判断语言
5. 返回`[(q, a), ...]`列表

**数据流向**：
- 输入：Excel文件
- 输出：`[(问题, 答案), ...]`

**依赖调用**：
- `is_english`, `random_choices`
- `rmPrefix`（本模块）

---

### 12.2 Pdf 类

**类注释与设计意图**：
问答对PDF解析器，继承自`PdfParser`，从PDF识别问答结构。

**继承关系**：
```
deepdoc.parser.PdfParser (基类)
    └── qa.Pdf (本类)
```

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, zoomin=3, callback=None) -> tuple
```

**功能**：从PDF识别问答结构。

**实现步骤**：
1. 渲染PDF页面
2. 进行布局分析和表格分析
3. 合并文本
4. 提取表格
5. 调用`qbullets_category`识别问答符号
6. 遍历boxes：
   - 调用`has_qbullet`检测问题符号
   - 累积答案内容
   - 遇到新问题时保存之前的问答对
   - 处理表格和图片
7. 返回`[(q, a, image, poss), ...]`和tables

**依赖调用**：
- `qbullets_category`, `has_qbullet`
- `get_float`（common.float_utils）
- `self.crop`（继承自PdfParser）

---

### 12.3 Docx 类

**类注释与设计意图**：
问答对Word解析器，继承自`DocxParser`，从Word文档提取层级问答结构。

**继承关系**：
```
deepdoc.parser.DocxParser (基类)
    └── qa.Docx (本类)
```

#### __call__ 方法

**完整签名**：
```python
def __call__(self, filename, binary=None, from_page=0, to_page=100000, callback=None) -> tuple
```

**功能**：从Word文档提取层级问答结构。

**实现步骤**：
1. 加载文档
2. 初始化变量：`last_answer`, `last_image`, `question_stack`, `level_stack`
3. 遍历段落：
   - 调用`docx_question_level`提取标题层级
   - 非标题段落：追加到答案，提取图片
   - 标题段落：更新问题栈
4. 处理表格
5. 返回问答对列表和表格列表

---

### 12.4 辅助函数

#### rmPrefix 函数

**完整签名**：
```python
def rmPrefix(txt) -> str
```

**功能**：移除问答前缀。

**实现步骤**：
使用正则表达式移除"问题"、"答案"、"Q"、"A"等前缀。

#### beAdocPdf 函数

**完整签名**：
```python
def beAdocPdf(d, q, a, eng, image, poss) -> dict
```

**功能**：构建PDF问答文档块。

#### beAdocDocx 函数

**完整签名**：
```python
def beAdocDocx(d, q, a, eng, image, row_num=-1) -> dict
```

**功能**：构建Word问答文档块。

#### beAdoc 函数

**完整签名**：
```python
def beAdoc(d, q, a, eng, row_num=-1) -> dict
```

**功能**：构建通用问答文档块。

#### mdQuestionLevel 函数

**完整签名**：
```python
def mdQuestionLevel(s) -> tuple
```

**功能**：从Markdown标题提取层级。

---

### 12.5 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：解析问答对文件。

**实现步骤**：

**xlsx文件处理**：
1. 调用`Excel()`解析
2. 调用`beAdoc`生成每个问答对

**txt文件处理**：
1. 按行分割
2. 检测分隔符（逗号或制表符）
3. 解析问答对
4. 处理跨行答案

**csv文件处理**：
1. 使用csv.reader解析
2. 处理问答对

**pdf文件处理**：
1. 调用`Pdf()`解析
2. 调用`beAdocPdf`生成结果

**md文件处理**：
1. 按行分割
2. 识别Markdown标题层级
3. 构建层级问答结构

**docx文件处理**：
1. 调用`Docx()`解析
2. 调用`beAdocDocx`生成结果

**依赖调用**：
- `Excel`, `Pdf`, `Docx`（本模块）
- `beAdoc`, `beAdocPdf`, `beAdocDocx`
- `tokenize_table`

---

## 十三、resume.py 简历解析器模块

### 13.1 模块概述

简历解析器是RAGFlow中最复杂的解析器，采用SmartResume Pipeline架构，包含四个阶段：
1. PDF文本融合：元数据+OCR双路径提取
2. 布局感知重构：YOLOv10布局检测+层级排序
3. 并行LLM结构化提取：基本信息/工作经历/教育背景/项目经验四路并行
4. 四阶段后处理：源文本验证、领域规范化、上下文去重、字段补全

### 13.2 模块级常量

```python
FORBIDDEN_SELECT_FIELDS = ["name_pinyin_kwd", "edu_first_fea_kwd", "degree_kwd", "sch_rank_kwd", "edu_fea_kwd"]
FIELD_MAP_ZH = {...}  # 中文字段映射
FIELD_MAP_EN = {...}  # 英文字段映射
_LONG_RANDOM_PATTERN = re.compile(r'[a-zA-Z0-9\-~_]{40,}')
_LLM_MAX_RETRIES = 2
```

---

### 13.3 核心函数详解

#### _get_layout_recognizer 函数

**完整签名**：
```python
def _get_layout_recognizer()
```

**功能**：获取YOLOv10布局检测器单例（延迟加载）。

**实现步骤**：
1. 检查全局变量`_layout_recognizer`
2. 如果为None，尝试加载`LayoutRecognizer`
3. 加载失败时标记为False避免重复尝试
4. 返回检测器实例或None

---

#### _normalize_whitespace 函数

**完整签名**：
```python
def _normalize_whitespace(text: str) -> str
```

**功能**：Unicode空白字符规范化。

**实现步骤**：
1. 应用NFKC规范化
2. 统一各种Unicode空白字符为普通空格
3. 合并连续空格
4. 返回规范化文本

---

#### _should_remove_random_str 函数

**完整签名**：
```python
def _should_remove_random_str(match: re.Match) -> bool
```

**功能**：判断长字符串是否为无意义随机字符串。

**实现步骤**：
1. 如果tiktoken可用，计算token数量
2. 如果token数超过字符数的50%，判定为随机字符串
3. 否则使用启发式方法：大小写/数字交替频率

---

#### _is_noise_char 函数

**完整签名**：
```python
def _is_noise_char(obj: dict) -> bool
```

**功能**：判断PDF字符对象是否为装饰层噪声字符。

**实现步骤**：
1. 检查字体名是否包含"+"（嵌入字体）
2. 检查是否有PDF结构标签
3. 不满足任一条件则判定为噪声

---

#### _extract_metadata_text 函数

**完整签名**：
```python
def _extract_metadata_text(binary: bytes) -> list[dict]
```

**功能**：从PDF元数据提取文本块（带坐标信息）。

**实现步骤**：
1. 使用pdfplumber打开PDF
2. 遍历每页：
   - 过滤装饰层噪声字符
   - 使用`extract_words`提取单词级内容
   - 按Y坐标聚合为行级文本块
   - 提取表格内容
3. 返回文本块列表

**数据流向**：
- 输入：PDF二进制内容
- 输出：`[{"text": ..., "x0": ..., "top": ..., "x1": ..., "bottom": ..., "page": ...}, ...]`

---

#### _extract_ocr_text 函数

**完整签名**：
```python
def _extract_ocr_text(binary: bytes, meta_blocks: list[dict] | None = None) -> list[dict]
```

**功能**：使用黑化策略提取OCR文本块。

**实现步骤**：
1. 使用pdfplumber打开PDF
2. 遍历每页：
   - 渲染页面为图片
   - 黑化已提取的元数据区域
   - 运行OCR识别
   - 提取边界框和文本
3. 返回OCR文本块列表

---

#### _fuse_text_blocks 函数

**完整签名**：
```python
def _fuse_text_blocks(meta_blocks: list[dict], ocr_blocks: list[dict]) -> list[dict]
```

**功能**：融合PDF元数据文本和OCR文本。

**实现步骤**：
1. 过滤元数据中的乱码块
2. 直接合并有效元数据块和OCR块

---

#### _layout_aware_reorder 函数

**完整签名**：
```python
def _layout_aware_reorder(blocks: list[dict]) -> list[dict]
```

**功能**：布局感知层级排序。

**实现步骤**：
1. 按页分组
2. 检测多栏布局
3. 多栏：左栏优先，每栏从上到下
4. 单栏：从上到下，同行从左到右

---

#### _build_indexed_text 函数

**完整签名**：
```python
def _build_indexed_text(blocks: list[dict]) -> tuple[str, list[str], list[dict]]
```

**功能**：构建带行号的索引文本。

**实现步骤**：
1. 合并相邻文本块为行
2. 过滤空行和乱码行
3. 修复字段标签分割问题
4. 添加行号索引
5. 返回`(indexed_text, lines, line_positions)`

---

#### _is_valid_line 函数

**完整签名**：
```python
def _is_valid_line(line: str) -> bool
```

**功能**：检查文本行是否为有效内容（非乱码）。

**实现步骤**：
1. 检测cid占位符
2. 计算有效字符比例
3. 检测单字符间隔异常
4. 检测连续无意义混合序列

---

#### _fix_split_labels 函数

**完整签名**：
```python
def _fix_split_labels(lines: list[str]) -> list[str]
```

**功能**：修复字段标签分割问题。

**实现步骤**：
1. 定义常见分割模式（如"姓"+"名"→"姓名"）
2. 检测行内分割模式
3. 合并分割的标签

---

#### extract_text 函数

**完整签名**：
```python
def extract_text(filename: str, binary: bytes) -> tuple[str, list[str], list[dict]]
```

**功能**：根据文件类型提取文本内容（Pipeline阶段1）。

**实现步骤**：

**PDF文件**：
1. 双路径提取：元数据+OCR
2. 文本融合
3. 布局感知排序
4. 构建行索引文本

**DOCX文件**：
1. 提取段落文本
2. 提取表格内容

**其他格式**：
1. 使用`get_text`提取文本

---

#### _clean_llm_json_response 函数

**完整签名**：
```python
def _clean_llm_json_response(response: str) -> str
```

**功能**：清理LLM JSON响应。

**实现步骤**：
1. 移除Markdown代码块标记
2. 移除思考标签
3. 定位第一个"{"和最后一个"}"
4. 返回JSON字符串

---

#### _parse_json_with_repair 函数

**完整签名**：
```python
def _parse_json_with_repair(text: str) -> dict
```

**功能**：解析JSON字符串，失败时尝试修复。

**实现步骤**：
1. 尝试标准json.loads
2. 替换Python风格布尔值和None
3. 尝试json_repair库

---

#### _call_llm 函数

**完整签名**：
```python
def _call_llm(prompt: str, tenant_id, lang: str) -> Optional[dict]
```

**功能**：调用LLM并解析JSON响应。

**实现步骤**：
1. 创建LLMBundle实例
2. 最多重试`_LLM_MAX_RETRIES`次
3. 重试时增加temperature和随机seed
4. 清理响应并解析JSON

---

#### _extract_description_from_range 函数

**完整签名**：
```python
def _extract_description_from_range(index_range: list, lines: list[str], company: str = "", position: str = "") -> str
```

**功能**：从原文按索引范围提取描述。

**实现步骤**：
1. 边界安全检查
2. 提取指定范围的行
3. 过滤包含公司名和职位的标题行
4. 返回描述文本

---

#### _extract_basic_info 函数

**完整签名**：
```python
def _extract_basic_info(indexed_text: str, tenant_id, lang: str) -> Optional[dict]
```

**功能**：提取基本信息（子任务1）。

**实现步骤**：
1. 截取前8000字符
2. 构建提示词
3. 调用LLM

---

#### _extract_work_experience 函数

**完整签名**：
```python
def _extract_work_experience(indexed_text: str, tenant_id, lang: str) -> Optional[dict]
```

**功能**：提取工作经历（子任务2）。

---

#### _extract_education 函数

**完整签名**：
```python
def _extract_education(indexed_text: str, tenant_id, lang: str) -> Optional[dict]
```

**功能**：提取教育背景（子任务3）。

---

#### _extract_project_experience 函数

**完整签名**：
```python
def _extract_project_experience(indexed_text: str, tenant_id, lang: str) -> Optional[dict]
```

**功能**：提取项目经验（子任务4）。

---

#### parse_with_llm 函数

**完整签名**：
```python
def parse_with_llm(indexed_text: str, lines: list[str], tenant_id, lang: str) -> Optional[dict]
```

**功能**：使用并行任务分解策略提取简历信息。

**实现步骤**：
1. 使用ThreadPoolExecutor并行执行四个子任务
2. 合并基本信息
3. 处理工作经历：
   - 提取公司名、职位
   - 计算工作年限
   - 按索引指针提取描述
4. 处理教育背景：
   - 提取学校、专业、学位
   - 推断最高学历
5. 处理项目经验
6. 返回合并后的结构化简历字典

---

#### parse_with_regex 函数

**完整签名**：
```python
def parse_with_regex(text: str, lang: str = "Chinese") -> dict
```

**功能**：使用正则表达式解析简历（备选策略）。

**实现步骤**：
1. 提取姓名（中英文不同策略）
2. 提取电话号码
3. 提取邮箱
4. 提取性别
5. 提取年龄
6. 提取出生日期
7. 提取学历
8. 提取学校
9. 提取专业
10. 提取公司名
11. 提取职位
12. 提取工作年限
13. 提取毕业年份

---

#### _postprocess_resume 函数

**完整签名**：
```python
def _postprocess_resume(resume: dict, lines: list[str], lang: str = "Chinese") -> dict
```

**功能**：四阶段后处理管道。

**实现步骤**：

**阶段1：源文本验证**
- 验证姓名是否在原文中出现
- 验证公司名是否在原文中出现
- 验证学校名是否在原文中出现

**阶段2：领域规范化**
- 标准化日期格式
- 清理电话号码非数字字符
- 标准化性别

**阶段3：上下文去重**
- 列表字段去重
- 工作描述按公司名+时间段去重
- 合并项目描述到工作描述

**阶段4：字段补全**
- 确保所有必需字段存在

---

#### parse_resume 函数

**完整签名**：
```python
def parse_resume(filename: str, binary: bytes, tenant_id, lang: str = "Chinese") -> tuple[dict, list[str], list[dict]]
```

**功能**：简历解析管道编排函数。

**实现步骤**：
1. 阶段1：文本提取
2. 阶段2：并行LLM结构化提取
3. 阶段3：正则备选解析（LLM失败时）
4. 阶段4：后处理管道

---

#### _build_chunk_document 函数

**完整签名**：
```python
def _build_chunk_document(filename: str, resume: dict, lang: str = "Chinese") -> list[dict]
```

**功能**：从结构化简历信息构建文档块列表。

**实现步骤**：
1. 提取身份字段（姓名、电话、邮箱等）
2. 构建简历摘要文本
3. 定义字段分组：
   - 基本信息
   - 教育背景
   - 技能证书
   - 工作概况
4. 按组合并字段生成chunk
5. 处理需要拆分的列表字段（工作描述、项目描述）
6. 为每个chunk添加坐标信息

---

#### _blackout_text_regions 函数

**完整签名**：
```python
def _blackout_text_regions(image: "np.ndarray", meta_blocks: list[dict], page_idx: int, pdf_to_img_scale: float) -> "np.ndarray"
```

**功能**：在页面图片上黑化元数据提取的文本区域。

**实现步骤**：
1. 复制图片
2. 遍历指定页的文本块
3. 计算缩放后的坐标
4. 使用cv2.rectangle绘制黑色矩形

---

#### _resort_page_with_layout 函数

**完整签名**：
```python
def _resort_page_with_layout(page_blocks: list[dict], layout_regions: list[dict]) -> list[dict]
```

**功能**：使用布局区域重排页面块。

---

#### _layout_detect_reorder 函数

**完整签名**：
```python
def _layout_detect_reorder(blocks: list[dict], binary: bytes) -> list[dict]
```

**功能**：使用YOLOv10布局检测重排序。

**实现步骤**：
1. 获取布局检测器
2. 渲染PDF页面为图片
3. 调用检测器获取布局区域
4. 按布局区域重排序文本块

---

#### _text_shingles 函数

**完整签名**：
```python
def _text_shingles(text: str, n: int = 5) -> set[tuple[int, ...]]
```

**功能**：使用tiktoken BPE分词+n-gram shingling生成文本指纹集。

---

#### _shingling_jaccard 函数

**完整签名**：
```python
def _shingling_jaccard(text1: str, text2: str, n: int = 5) -> float
```

**功能**：计算两个文本的Jaccard相似度。

---

### 13.4 chunk 函数

**完整签名**：
```python
def chunk(filename, binary, tenant_id, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：简历解析入口函数。

**实现步骤**：
1. 调用`parse_resume`解析简历
2. 调用`_build_chunk_document`构建文档块
3. 返回文档块列表

---

## 十四、table.py 表格解析器模块

### 14.1 Excel 类

**类注释与设计意图**：
表格Excel解析器，继承自`ExcelParser`，支持多级表头解析、合并单元格处理和图片提取。

**继承关系**：
```
deepdoc.parser.ExcelParser (基类)
    └── table.Excel (本类)
```

#### __call__ 方法

**完整签名**：
```python
def __call__(self, fnm, binary=None, from_page=0, to_page=10000000000, callback=None, **kwargs) -> tuple
```

**功能**：解析Excel文件。

**实现步骤**：
1. 加载工作簿
2. 计算总行数
3. 遍历工作表：
   - 提取图片
   - 调用`vision_figure_parser_figure_xlsx_wrapper`处理图片
   - 解析表头（简单或多级）
   - 提取数据行
   - 将单元格图片描述填入对应位置
4. 构建DataFrame
5. 返回DataFrame列表和表格列表

**数据流向**：
- 输入：Excel文件
- 输出：`([DataFrame, ...], tables)`

**依赖调用**：
- `vision_figure_parser_figure_xlsx_wrapper`
- `self._parse_headers`
- `self._extract_row_data`

---

#### _parse_headers 方法

**完整签名**：
```python
def _parse_headers(self, ws, rows) -> tuple
```

**功能**：解析表头。

**实现步骤**：
1. 检测是否有复杂表头结构
2. 复杂结构：调用`_parse_multi_level_headers`
3. 简单结构：调用`_parse_simple_headers`

---

#### _has_complex_header_structure 方法

**完整签名**：
```python
def _has_complex_header_structure(self, ws, rows) -> bool
```

**功能**：检测是否有复杂表头结构。

**实现步骤**：
检查前两行是否涉及合并单元格。

---

#### _parse_simple_headers 方法

**完整签名**：
```python
def _parse_simple_headers(self, rows) -> tuple
```

**功能**：解析简单表头。

---

#### _parse_multi_level_headers 方法

**完整签名**：
```python
def _parse_multi_level_headers(self, ws, rows) -> tuple
```

**功能**：解析多级表头。

**实现步骤**：
1. 检测表头行数
2. 调用`_build_hierarchical_headers`构建层级表头

---

#### _detect_header_rows 方法

**完整签名**：
```python
def _detect_header_rows(self, rows) -> int
```

**功能**：检测表头行数。

---

#### _build_hierarchical_headers 方法

**完整签名**：
```python
def _build_hierarchical_headers(self, ws, rows, header_rows) -> list
```

**功能**：构建层级表头。

**实现步骤**：
1. 遍历每列
2. 获取合并单元格值
3. 用"-"连接各级表头

---

#### _extract_row_data 方法

**完整签名**：
```python
def _extract_row_data(self, ws, row, absolute_row_idx, expected_cols) -> list
```

**功能**：提取行数据。

**实现步骤**：
1. 遍历每列
2. 获取单元格值
3. 处理合并单元格
4. 返回行数据列表

---

### 14.2 辅助函数

#### trans_datatime 函数

**完整签名**：
```python
def trans_datatime(s) -> str
```

**功能**：转换日期时间格式。

---

#### trans_bool 函数

**完整签名**：
```python
def trans_bool(s) -> str
```

**功能**：转换布尔值。

---

#### column_data_type 函数

**完整签名**：
```python
def column_data_type(arr) -> tuple
```

**功能**：检测列数据类型。

**实现步骤**：
1. 统计各类型数量
2. 选择数量最多的类型
3. 转换数据

---

### 14.3 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, from_page=0, to_page=10000000000, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：解析表格文件。

**实现步骤**：

**xlsx文件处理**：
1. 调用`Excel()`解析
2. 返回DataFrame列表和表格列表

**txt文件处理**：
1. 按制表符分割
2. 构建DataFrame

**csv文件处理**：
1. 使用csv.reader解析
2. 构建DataFrame

**通用处理**：
1. 删除id/index列
2. 检测重复列名
3. 生成拼音列名
4. 检测列数据类型
5. 更新知识库字段映射
6. 遍历每行生成chunk
7. 处理表格中的图片

**依赖调用**：
- `Excel`（本模块）
- `Pinyin`（xpinyin）
- `column_data_type`
- `KnowledgebaseService.update_parser_config`
- `tokenize`, `tokenize_table`

---

## 十五、tag.py 标签解析器模块

### 15.1 beAdoc 函数

**完整签名**：
```python
def beAdoc(d, q, a, eng, row_num=-1) -> dict
```

**功能**：构建标签文档块。

**实现步骤**：
1. 设置content_with_weight为问题内容
2. 分词生成content_ltks
3. 将答案按逗号分割为标签列表
4. 存储到tag_kwd字段

---

### 15.2 chunk 函数

**完整签名**：
```python
def chunk(filename, binary=None, lang="Chinese", callback=None, **kwargs) -> list[dict]
```

**功能**：解析标签文件。

**实现步骤**：

**xlsx文件处理**：
1. 复用`qa.Excel`解析器
2. 调用`beAdoc`生成结果

**txt文件处理**：
1. 检测分隔符
2. 解析内容和标签
3. 处理跨行内容

**csv文件处理**：
1. 使用csv.reader解析
2. 解析内容和标签

**依赖调用**：
- `Excel`（rag.app.qa）
- `beAdoc`

---

### 15.3 label_question 函数

**完整签名**：
```python
def label_question(question, kbs) -> list
```

**功能**：使用知识库标签标注问题。

**实现步骤**：
1. 获取标签知识库ID列表
2. 从缓存或数据库获取所有标签
3. 调用`settings.retriever.tag_query`进行标签匹配
4. 返回匹配的标签列表

**依赖调用**：
- `KnowledgebaseService.get_by_ids`
- `get_tags_from_cache`, `set_tags_to_cache`
- `settings.retriever.tag_query`

---

## 十六、模块间继承关系图

```
deepdoc.parser.PdfParser (基类)
├── naive.Pdf
├── book.Pdf
├── laws.Pdf
├── manual.Pdf
├── one.Pdf
├── paper.Pdf
├── presentation.Pdf
└── qa.Pdf

deepdoc.parser.DocxParser (基类)
├── naive.Docx
├── laws.Docx
├── manual.Docx
└── qa.Docx

deepdoc.parser.ExcelParser (基类)
├── qa.Excel
└── table.Excel

deepdoc.parser.MarkdownParser (基类)
└── naive.Markdown

deepdoc.parser.PlainParser (基类)
└── presentation.PlainPdf
```

---

## 十七、数据流转图

```
文件输入
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                      chunk() 入口函数                        │
│  1. 解析配置参数                                             │
│  2. 提取嵌入文件                                             │
│  3. 根据文件类型分发                                         │
└─────────────────────────────────────────────────────────────┘
    │
    ├──docx──► naive.Docx() ──► naive_merge_docx() ──► doc_tokenize_chunks_with_images()
    │
    ├──pdf───► PARSERS[name]() ──► vision_figure_parser_pdf_wrapper() ──► tokenize_table()
    │              │
    │              ├── by_deepdoc ──► Pdf()
    │              ├── by_mineru ──► LLMBundle.parse_pdf()
    │              ├── by_docling ──► DoclingParser()
    │              ├── by_tcadp ──► TCADPParser()
    │              ├── by_paddleocr ──► LLMBundle.parse_pdf()
    │              └── by_plaintext ──► PlainParser/VisionParser()
    │
    ├──xlsx──► table.Excel() ──► DataFrame ──► tokenize()
    │
    ├──txt───► TxtParser() ──► naive_merge() ──► tokenize_chunks()
    │
    ├──md────► naive.Markdown() ──► MarkdownElementExtractor ──► tokenize_chunks_with_images()
    │
    ├──html──► HtmlParser() ──► tokenize_chunks()
    │
    ├──eml───► BytesParser() ──► naive_chunk() (递归处理附件)
    │
    ├──audio─► LLMBundle.transcription() ──► tokenize()
    │
    ├──image─► OCR() + LLMBundle.describe() ──► tokenize()
    │
    └──video──► LLMBundle.async_chat() ──► tokenize()
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    输出: chunks列表                          │
│  [{"content_with_weight": ...,                              │
│    "content_ltks": ...,                                     │
│    "docnm_kwd": ...,                                        │
│    "title_tks": ...,                                        │
│    ...}]                                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 十八、关键设计模式总结

### 18.1 策略模式

`PARSERS`字典实现了策略模式，允许在运行时根据配置选择不同的PDF解析引擎。

### 18.2 模板方法模式

`PdfParser`、`DocxParser`、`ExcelParser`等基类定义了骨架方法，子类通过重写`__call__`方法实现具体解析逻辑。

### 18.3 工厂模式

各模块的`chunk`函数作为工厂方法，根据文件类型创建对应的解析器实例并执行解析。

### 18.4 责任链模式

`resume.py`的四阶段管道（文本提取→LLM提取→正则备选→后处理）实现了责任链模式。

### 18.5 并行处理模式

`resume.py`使用`ThreadPoolExecutor`并行执行四个LLM子任务，提高处理效率。

---

## 十九、性能优化要点

1. **延迟加载**：`_get_layout_recognizer`延迟加载YOLOv10模型
2. **缓存机制**：图片加载使用缓存避免重复下载
3. **并行处理**：简历解析使用四路并行LLM调用
4. **黑化策略**：OCR前黑化已提取区域避免重复识别
5. **索引指针**：LLM返回行号范围而非生成全文，减少幻觉

---

## 二十、扩展指南

### 20.1 添加新文件格式支持

1. 在对应模块的`chunk`函数中添加文件扩展名匹配
2. 实现解析逻辑
3. 调用`tokenize`或`tokenize_chunks`生成结果

### 20.2 添加新PDF解析引擎

1. 实现`by_xxx`函数，签名与现有函数一致
2. 在`PARSERS`字典中注册
3. 在`normalize_layout_recognizer`中添加配置映射

### 20.3 自定义解析器

1. 继承对应的基类（`PdfParser`、`DocxParser`等）
2. 重写`__call__`方法
3. 在`chunk`函数中使用自定义解析器
