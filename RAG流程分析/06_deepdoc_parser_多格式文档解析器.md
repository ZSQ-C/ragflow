# 06 — deepdoc/parser：多格式文档解析器完整解读

> **目录位置**：`e:\AI\GitHub\RagFlow\deepdoc\parser\`
> **文件清单**：`pdf_parser.py`（2057行）、`docx_parser.py`、`excel_parser.py`、`html_parser.py`、`markdown_parser.py`、`epub_parser.py`、`json_parser.py`、`txt_parser.py`、`ppt_parser.py`、`figure_parser.py`、`docling_parser.py`、`mineru_parser.py`、`paddleocr_parser.py`、`tcadp_parser.py`、`utils.py`、`resume/`
> **核心定位**：RAGFlow 中 11 种文档格式的解析器集合——每种格式一个独立解析器，通过 `__init__.py` 统一导出别名供上层调用
> **调用链**：`rag/app/naive.py#chunk()` 按扩展名路由 → `deepdoc/parser/__init__.py` 统一导入 → 各解析器的 `__call__()` 方法 → `rag/nlp/__init__.py` 进行分块合并

---

## 一、核心总览（带逻辑关系）

### 1.1 核心定位

`deepdoc/parser` 是 RAGFlow 整个文档处理管线的**第一道工序**——负责将 11 种不同格式的文档（PDF、DOCX、Excel、HTML、Markdown、EPUB、JSON、TXT、PPT、图片、简历解析器）统一解析为可供检索的结构化文本片段。每一种文件格式都对应一个独立的解析器类，它们遵循相同的设计约定：通过 `__call__()` 方法作为统一入口，输出 `(sections, tables)` 或直接 `sections` 列表——其中 `sections` 是 `[(text, position_tag), ...]` 格式的文本块列表，每个块携带其在原始文档中的位置信息坐标。

这种"每种格式一个类、统一定义 `__call__` 入口"的设计，使得上层调用者（`rag/app/naive.py` 的 `chunk()` 函数）可以通过简单的扩展名判断 + PARSERS 字典路由，无需关心底层解析器内部的差异。

**适用场景**：企业知识库（含 WORD 文档、PDF 手册、Excel 报表）、合同/发票等格式化文档的批量自动解析、简历批量分析、网页内容抽取、电子书知识提取。

**解决的业务问题**：在传统 RAG 系统中，用户需要先手动提取文档中的文本再导入——这是一个巨大的效率瓶颈。`deepdoc/parser` 实现了全自动化：用户上传任意支持的格式 → 解析器自动提取文字、表格、图片 → 分块合并 → 向量化 → 可检索。

### 1.2 整体流程串讲

当用户上传一个文件后，`rag/app/naive.py` 中的 `chunk()` 函数首先按文件扩展名做路由判断。对于 `.pdf` 文件，路由到 `PdfParser`（即 `RAGFlowPdfParser`）——这是整个解板中最复杂的类，长达 2057 行。它先通过 `pdfplumber` 逐页渲染 PDF 为图片并提取字符级位置信息，然后用三重乱码检测（PUA 字符 + CID 模式 + 字体编码）决定是否需要触发 ONNX OCR 降级。OCR 完成后，`LayoutRecognizer` 对每页的文字框做布局分类（11 类标签），`TableStructureRecognizer` 对表格区域做结构识别。最终通过 `sort_X_by_page` 排序、`_text_merge` 横向合并、`_naive_vertical_merge` 纵向合并、KMeans 分栏检测、`__filterout_scraps` 碎片过滤、`_line_tag` 位置标签等多种后处理，输出带有页面坐标和布局类型的 sections 和 tables。

对于 `.docx` 文件，`RAGFlowDocxParser` 通过 `python-docx` 库打开文件后，遍历 XML body element tree，按文档顺序处理段落和表格。每个段落通过 `get_picture()` 提取嵌入图片（XPath 查找 `<a:blip>` 元素后用 rId 获取二进制数据），表格通过 `__extract_table_content` 转为 pandas DataFrame 再输出为 HTML。分页通过检测段落的 `lastRenderedPageBreak` XML 标签实现。

对于 Excel、HTML、Markdown、JSON 等其他格式，各自有不同的底层库依赖和解析策略，但都遵循"解析→分块→返回 text 列表"的统一输出接口。

**底层依赖链条**：`pdfplumber`（PDF→字符位置）→ `deepdoc.vision`（OCR+Layout+Table）→ `python-docx`（DOCX）→ `pandas/openpyxl`（Excel）→ `BeautifulSoup`（HTML）→ `markdown/itertools`（Markdown）→ `chardet`（编码检测）

---

## 二、模块拆分（固定顺序 + 关系说明）

### 模块1：统一导出层 —— `__init__.py`

**作用**：整个 parser 模块的"门面"。把每个解析器类的长名称（`RAGFlowPdfParser`）以别名（`PdfParser`）统一导出，上层只需 `from deepdoc.parser import PdfParser` 即可，内部类命名不影响调用方。

**与其他模块的配合关系**：是上层 `rag/app/naive.py` 和 `PARSERS` 字典的唯一依赖入口。

### 模块2：PDF 解析器 —— `pdf_parser.py`（`RAGFlowPdfParser` + `PlainParser` + `VisionParser`）

**作用**："最核心的解析器"。初始化时加载整套视觉管道（OCR + LayoutRecognizer + TableStructureRecognizer + XGBoost 纵向合并模型），通过 `__call__()` 按完整流水线执行 PDF→字符提取→乱码检测→OCR→布局识别→表格识别→文本合并→碎片过滤→输出。它是所有解析器中代码量最大、逻辑最复杂的，几乎占据了解析层 50% 以上的代码量。

**与其他模块的配合关系**：调用 `deepdoc.vision` 中的 `OCR`、`LayoutRecognizer`、`TableStructureRecognizer`；配合 KMeans 分栏检测、XGBoost 模型做纵向拼接判断；输出给 `rag/app/naive.py` 中的 `chunk()` 函数做后续分块。

### 模块3：DOCX 解析器 —— `docx_parser.py`（`RAGFlowDocxParser`）

**作用**：基于 `python-docx` 解析 Microsoft Word 文档。核心能力包括：图片提取（XPath XML 路径查询+LazyImage 延迟加载）、表格提取（pandas DataFrame→HTML）、分页检测（`lastRenderedPageBreak` XML 标签）、Caption 处理（识别 Caption 样式并将其与图片关联）。

**与其他模块的配合关系**：在上层 `naive.py` 中被 `Docx` 子类继承后增强，增加了章节标题查找、表格标题关联、Vision LLM 图片增强等能力；输出给 `naive_merge_docx` 做 DOCX 专用分块。

### 模块4：Excel 解析器 —— `excel_parser.py`（`RAGFlowExcelParser`）

**作用**：解决 Excel 文件的"多引擎兼容加载"问题——四级降级尝试（openpyxl→pandas→calamine→CSV fallback），并提供默认 `"字段: 值; "` 格式输出和可选的 HTML `<table>` 输出。

### 模块5：HTML 解析器 —— `html_parser.py`（`RAGFlowHtmlParser`）

**作用**：用 BeautifulSoup + html5lib 解析 HTML/网页，递归遍历 DOM 树提取文本，表格用 UUID 占位符隔离，保留标题层级（h1→h6 加 markdown `#` 前缀）。

### 模块6-11：其他格式解析器（Markdown、EPUB、JSON、TXT、PPT、Figure）

**作用**：各自处理一种文件格式的特殊逻辑，但都遵循 `__call__` → `sections` 的统一输出接口。

---

## 三、方法详细解析（强制 5 要素 + 文字流程串讲）

### 3.1 `RAGFlowPdfParser.__init__()` —— PDF 解析器初始化

#### 方法文字流程串讲（`pdf_parser.py` L56-L109）

这是所有解析器中初始化最重的方法——它要在构造函数中一次性加载整套视觉 AI 管道和机器学习模型。

首先创建 `self.ocr = OCR()`——这会在首次运行时通过 `snapshot_download` 从 HuggingFace 下载约 500MB 的 ONNX 模型文件（det.onnx、rec.onnx、ocr.res 字符字典等）到本地 `rag/res/deepdoc/` 目录。如果环境变量 `settings.PARALLEL_DEVICES > 1` 说明配置了多 GPU 并行，此时创建 `asyncio.Semaphore(1)` 的并行限制器列表（每 GPU 一个信号量），实现多 GPU 并发控制的 OCR 加速。

接下来通过 `LAYOUT_RECOGNIZER_TYPE` 环境变量判断布局识别器的后端类型——`"onnx"` 走本地 ONNX Runtime（创建 `LayoutRecognizer`），`"ascend"` 走华为 Ascend NPU 加速（创建 `AscendLayoutRecognizer`）。如果子类设置了 `model_speciess` 属性，说明要用特定领域的布局模型（如中文学术论文专用模型），布局域名变为 `"layout." + self.model_speciess`。同时创建 `self.tbl_det = TableStructureRecognizer()`。

最后加载 XGBoost 纵向合并模型 `updown_cnt_mdl`——这是一个训练好的二分类模型，输入两个相邻文本块的 25 个特征（Y 距离、是否同行、布局类型是否相同、标点位置、高度差、字符宽度等），输出它们是否应该纵向合并为一个段落。如果 torch cuda 可用，XGBoost 模型切换到 GPU 推理。模型文件 `updown_concat_xgb.model` 优先从本地加载，不存在则通过 `snapshot_download` 从 HuggingFace 下载。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `**kwargs`（可含 `model_speciess` 指定布局模型领域） |
| **核心逻辑** | 创建 OCR → 创建 LayoutRecognizer（ONNX/Ascend）→ 创建 TableStructureRecognizer → 加载 XGBoost 纵向合并模型 → 多 GPU 并行控制 |
| **输出形式** | 初始化完成的 PdfParser 实例 |
| **底层关键依赖** | `OCR()`、`LayoutRecognizer`、`TableStructureRecognizer`、`xgb.Booster`、`snapshot_download` |
| **关键代码片段** | `self.ocr = OCR(); self.layouter = LayoutRecognizer(domain); self.updown_cnt_mdl.load_model("updown_concat_xgb.model")` |

#### 特殊处理标注
- **多 GPU 并发控制**：`asyncio.Semaphore(1)` 按 GPU 设备分配，每个 GPU 单例访问避免显存冲突
- **HuggingFace 自动下载**：模型下载失败不崩溃——OCR 内部有 try-except，本地无文件时自动 `snapshot_download`
- **Ascend 兼容**：华为 NPU 通过 `AscendLayoutRecognizer` 独立实现，不影响 ONNX 路径

---

### 3.2 `RAGFlowPdfParser.__call__()` —— PDF 解析主流程

#### 方法文字流程串讲（`pdf_parser.py` 约 L400-L750）

这是整个 deepdoc 中最长的单个方法（约 350 行），串联了 PDF→字符提取→乱码检测→OCR→布局→表格识别→文本合并→碎片过滤的完整管道。

**阶段1-字符提取与乱码检测**：通过 `pdfplumber` 逐页打开 PDF，对每页（从 `from_page` 到 `to_page`）调用 `extract_words` 或 `extract_text` 提取字符级位置信息，同时获取 `page.chars`。对每个页面，依次执行三重乱码检测——如果有 CID 模式 `(cid:\d+)` 匹配到直接判定乱码；否则统计 `_is_garbled_char` 的 PUA 字符比例，超过自适应阈值则触发 OCR 降级；同时检查 `_is_garbled_by_font_encoding` 判断是否因字体编码错乱导致页面全为 ASCII 标点。乱码页面用 `self.ocr.__call__(page_image)` 重新识别。

**阶段2-布局识别**：调用 `self._layouts_rec()` → 对每页的 OCR 文本框调用 `self.layouter(image_list, ocr_res)` 做布局分类，标记每个框为 Text/Title/Table/Figure 等 11 类之一。布局完成后执行垃圾过滤——页眉/页脚/参考文献文本做 Counter 频次统计，重复出现的高频文本直接丢弃。

**阶段3-表格识别**：调用 `self._table_transformer_job()` → 从布局结果中筛选 Figure 类型的框，用 `group_bodies` 合并相邻 Figure，裁剪对应图片区域。对表格区域调用 `self.tbl_det(images)` 做 TableStructureRecognizer 推理，产出行列结构。同时支持表格旋转检测——评估四个方向（0°/90°/180°/270°）的 OCR 置信度选最佳角度（`_ocr_rotated_tables`）。

**阶段4-文本整合**：依次执行 `_text_merge`（横向合并同行文本框）、`_naive_vertical_merge`（纵向合并同列连续文本块）、`_assign_column`（KMeans 聚类 + 轮廓系数自动检测分栏数）、`_extract_table_figure`（分离表格/图片与其 Caption）、`_filter_forpages`（检测并过滤目录页）、`__filterout_scraps`（DFS 行分组过滤过窄/useless 碎片）、`_line_tag`（给每个框打上 `@@{page}\t{x0}\t{x1}\t{top}\t{bottom}##` 位置标签）。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `filename` 或 `binary`、`from_page=0`、`to_page=100000`、`callback=None` |
| **核心逻辑** | 字符提取→三重乱码检测→OCR降级→布局分类→垃圾过滤→表格识别→文本合并（横向+纵向）→分栏检测→碎片过滤→位置标签→返回 |
| **输出形式** | `sections`（list[str] 或 list[(str,dict)]，含位置信息）、`tables`（list[dict]，含 table_body/table_caption/table_img 等） |
| **底层关键依赖** | `pdfplumber`、`OCR`、`LayoutRecognizer`、`TableStructureRecognizer`、`KMeans`、`xgboost` |
| **关键代码片段** | `callbacks → pdfplumber → 乱码检测 → OCR → layout → table → merge → output` |

#### 特殊处理标注
- **回调进度报告**：`callback(progress, msg)` 在每个阶段完成后调用，支持前端进度条显示
- **自适应乱码阈值**：`lower=max(15, total*0.2), upper=min(35, total*0.3)`——短文档和长文档用不同标准
- **分栏自动检测**：`KMeans` 聚类 `X0` 坐标 → `silhouette_score` 评估聚类质量 → 自动确定列数

---

### 3.3 `RAGFlowDocxParser.__call__()` —— DOCX 解析器

#### 方法文字流程串讲（`docx_parser.py` L31-L70 + 后续约 300 行）

用 `python-docx` 打开 `.docx` 文件后，通过 `document.element.body` 获取完整的 XML body element tree，然后**按文档顺序遍历子元素**（不按段落/表格类型分组，而是按它们在 XML 中的实际出现顺序）。这就保证了"先出现的段落先读到"的文档顺序。

对每个子元素判断：如果 tag 以 `p` 结尾 → 是段落（paragraph），提取文本和可能包含的图片（调用 `get_picture` 获取 `LazyImage` 对象）、记录 style name（如 `Heading 1`、`Caption`）、检测 `lastRenderedPageBreak` 标签跟踪页码；如果 tag 以 `tbl` 结尾 → 是表格，调用 `__extract_table_content` 用 pandas DataFrame 转为 `"表头: 值; ..." `格式或 HTML 格式。

**图片提取**（`get_picture` L32-L69）：通过 XPath 路径 `".//pic:pic"` 查找段落中的图片对象 → 找到 `<a:blip>` 元素的 `r:embed` 属性 → 用 embed 值从 `document.part.related_parts` 获取图片数据 → 多层 try-except 兜底（`image.blob` 失败 → `related_part.blob` 降级获取）→ 最终返回 `LazyImage(image_blobs)` 延迟加载对象（节省内存，直到 `LazyImage.load()` 被调用才真正解码图片字节）。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `filename` 或 `binary`（bytes/str/BytesIO） |
| **核心逻辑** | python-docx 打开→XML body 遍历→段落（text+图片+样式+页码）+表格（pandas→HTML）→按文档顺序输出 |
| **输出形式** | `sections`（list[(text, image, table)] 三元组）+ 位置信息 |
| **底层关键依赖** | `python-docx`（`Document` 类）、`pandas`（`DataFrame`）、`LazyImage`（延迟加载） |
| **关键代码片段** | `for child in doc.element.body: if child.tag.endswith("p"): ... elif child.tag.endswith("tbl"): ...` |

#### 特殊处理标注
- **图片异常降级**：4 种异常（`UnrecognizedImageError`/`UnexpectedEndOfFileError`/`InvalidImageStreamError`/`UnicodeDecodeError`）分别捕获 + blob fallback
- **LazyImage 延迟加载**：不立即解码图片字节为 PIL Image（大文件内存开销），等分块阶段需要时才加载
- **按文档顺序遍历**：不按类型分组（如先处理所有段落再处理所有表格），保持原始阅读顺序

---

### 3.4 `RAGFlowExcelParser._load_excel_to_workbook()` —— Excel 多引擎加载

#### 方法文字流程串讲（`excel_parser.py` L30-L66）

这是一个精心设计的**四级降级加载策略**：

**第一级-openpyxl**：读取文件头 4 字节判断是否是 Excel 格式（`PK\x03\x04` 是 `.xlsx`/`.xlsm`、`\xd0\xcf\x11\xe0` 是 `.xls`）。如果是 → `load_workbook(file, data_only=True)` 直接加载。如果不是 → 尝试 `pd.read_csv` 按 CSV 加载（处理 `.csv` 文件）。

**第二级-pandas 默认引擎**：如果 openpyxl 加载失败（可能是版本不兼容或文件损坏），尝试 `pd.read_excel(file, sheet_name=None)` 用 pandas 默认引擎（也是 openpyxl 但容错性更好）。

**第三级-calamine 引擎**：如果 pandas 默认引擎也失败，尝试 `pd.read_excel(file, engine="calamine")`——calamine 是 Rust 编写的 fast Excel reader，容错性最强但功能较少。

**第四级-抛出异常**：如果三级全部失败，抛出包含原始 openpyxl 错误和 pandas 错误的异常信息。

**CSV 支持**：文件头不是 Excel 格式时直接走 `pd.read_csv`，实现了 CSV 到 Excel 的透明转换。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `file_like_object`（bytes 或 BytesIO） |
| **核心逻辑** | 文件头判断→openpyxl加载→pandas降级→calamine降级→CSV fallback→抛出异常 |
| **输出形式** | `openpyxl.Workbook` 对象 |
| **底层关键依赖** | `openpyxl.load_workbook`、`pandas.read_excel`（engine=calamine）、`pandas.read_csv` |
| **关键代码片段** | `file_head.startswith(b"PK\x03\x04")` → Excel；否则 → CSV |

#### 特殊处理标注
- **文件头 4 字节检测**：不用扩展名判断文件类型（用户可能传 `.csv` 改名为 `.xlsx`），而是读实际数据判断
- **data_only=True**：只读公式的计算结果，不读公式本身（避免打开时报公式错误）
- **异常串联**：最终异常信息包含原始 openpyxl 错误和 pandas 错误，方便排查

---

### 3.5 `RAGFlowHtmlParser.parser_txt()` —— HTML 递归解析与分块

#### 方法文字流程串讲（`html_parser.py` L49-L76）

首先用 `BeautifulSoup(txt, "html5lib")` 解析 HTML 文本（`html5lib` 比标准 `html.parser` 更宽容，能处理不规范 HTML）。然后执行**四步清洗**：
1. 删除所有 `<style>` 和 `<script>` 标签（不参与分块）
2. 删除 `<div>` 内部的 `<script>` 标签
3. 删除所有标签的 inline style 属性（保留结构但不保留样式）
4. 删除所有 HTML 注释

清洗后调用 `read_text_recursively(soup.body, temp_sections)` 递归遍历 DOM 树。这个递归函数的关键逻辑是：遇到高级块级标签（`h1`-`h6` → 加 markdown `#` 前缀；`table` → 用 UUID 占位符替换，表格 HTML 单独保存；`img` → 保留 src 属性），普通文本收集后累积。遍历完成后调用 `merge_block_text` 按 block_id 分组合并相邻同区块的文本，然后 `chunk_block` 按 `chunk_token_num` 做 Token 限制的分块。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `txt`（str，HTML 文本）、`chunk_token_num`（int，默认 512） |
| **核心逻辑** | BeautifulSoup解析→4步清洗→递归DOM遍历（表格UUID隔离+标题Markdown前缀）→block合并→Token分块 |
| **输出形式** | `sections`（list[str]，每块 ≤ chunk_token_num 个 Token） |
| **底层关键依赖** | `BeautifulSoup`（html5lib）、`rag_tokenizer`（Token 计数） |
| **关键代码片段** | `soup = BeautifulSoup(txt, "html5lib"); read_text_recursively(soup.body, ...)` |

#### 特殊处理标注
- **表格 UUID 隔离**：表格用 UUID 占位符替代原文位置，表格 HTML 后续单独分块——防止表格的 HTML 标签干扰正文的语义理解
- **h1-h6 Markdown 前缀**：保留标题层级信息，后续分块时标题会被优先保留
- **html5lib 解析器**：比标准解析器更宽容，能处理非标准 HTML

---

### 3.6 `RAGFlowTxtParser.parser_txt()` —— 纯文本解析与分块

#### 方法文字流程串讲（`txt_parser.py`）

先通过 `rag.nlp.find_codec` 自动检测文件编码（支持 UTF-8/GBK/BIG5 等 80+ 种编码），解码为字符串后按用户配置的 `delimiter` 分隔符（默认 `\n!?;。；！？`）切分文本。然后按 `chunk_token_num`（默认 512 Token）限制，将切分后的文本段合并——如果当前块 + 下一段的 Token 总数 ≤ 512，就合并；否则将当前块作为一个完整 chunk，下一段开始新 chunk。分隔符支持用反引号包裹的自定义模式（如 `` `\n##` `` 表示按 Markdown 二级标题切分）。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `txt`（str/bytes）、`chunk_token_num=512`、`delimiter="\n!?;。；！？"` |
| **核心逻辑** | 编码检测→分隔符切分→Token限制合并→返回chunk列表 |
| **输出形式** | `sections`（list[str]） |
| **底层关键依赖** | `find_codec()`（chardet 编码检测）、`rag_tokenizer`（Token 计数） |
| **关键代码片段** | `encoding = find_codec(binary); txt = binary.decode(encoding); pieces = re.split(delimiter, txt)` |

---

### 3.7 辅助函数 —— `RAGFlowPdfParser._updown_concat_features()` —— XGBoost 特征构造

#### 方法文字流程串讲（`pdf_parser.py` L136-L179）

这个函数为 XGBoost 纵向合并模型构造 25 个特征向量。输入是相邻的两个文本框（`up` 上框和 `down` 下框），输出是一个浮点数列表。特征包含：

- **空间关系**：是否同一行（`R` 标签相同）、Y 距离/高度比、页码差
- **布局关系**：布局类型是否相同、是否跨页
- **文本内容**：上框后 6 字符的 token 列表、下框前 6 字符的 token 列表、合并后的 token 列表
- **标点位置**：上框是否以冒号结束、下框是否以逗号/句号开始、上框括号是否未闭合
- **字体格式**：上下框的平均字符宽度、高度差、水平距离
- **特殊匹配**：上框是否有项目符号、段落编号
- **语言特征**：下框首个字符是否大写、上框末字符是否小写、是否是数字

总共 25 个特征，XGBoost 模型正是基于这些特征学习"两个相邻文本框是否应该合并"的二分类问题。

#### 强制 5 要素

| 要素 | 内容 |
|------|------|
| **入参** | `up`（dict，上方文本框，含 text/x0/x1/top/bottom/layout_type 等）、`down`（同理） |
| **核心逻辑** | 提取 25 个特征（空间14个+内容5个+标点3个+格式3个）→返回浮点数列表 |
| **输出形式** | `list[float]`，长度 25 |
| **底层关键依赖** | `rag_tokenizer.tokenize()`、`rag_tokenizer.tag()`（词性标注） |
| **关键代码片段** | `fea = [up.get("R")==down.get("R"), y_dis/h, ..., len(tks_up)==1 and tag=="nr"]` |

---

## 四、同类逻辑对比表

### 4.1 所有解析器对比

| 解析器 | 文件 | 底层依赖 API | 输入格式 | 输出格式 | 独特能力 |
|--------|------|------------|---------|---------|---------|
| **PdfParser** | `pdf_parser.py` | pdfplumber + OCR + ONNX | PDF 文件/bytes | `(sections, tables)` 含位置信息 | 三重乱码检测+自动降级OCR+布局分类+表格结构 |
| **PlainParser** | `pdf_parser.py` | pypdf | PDF 文件/bytes | `[(line, ""), ...]` | 纯文本提取（最快） |
| **VisionParser** | `pdf_parser.py` | Vision LLM | PDF 文件/bytes | 自然语言描述 | Vision LLM 直接看图说话（无OCR） |
| **DocxParser** | `docx_parser.py` | python-docx + pandas | DOCX 文件/bytes | `(text, image, table)` 三元组 | XML按文档顺序遍历+LazyImage延迟加载 |
| **ExcelParser** | `excel_parser.py` | openpyxl + pandas + calamine | XLSX/XLS/CSV/bytes | `"字段: 值; "` 或 HTML | 四级降级加载+CSV透明转换 |
| **HtmlParser** | `html_parser.py` | BeautifulSoup + html5lib | HTML/bytes | `sections` | DOM递归遍历+表格UUID隔离+标题Markdown前缀 |
| **MarkdownParser** | `markdown_parser.py` | markdown + itertools | MD/bytes | `(text, tables)` | 表格提取+结构元素（标题/代码块/列表/引用）提取 |
| **EpubParser** | `epub_parser.py` | zipfile + HtmlParser | EPUB 文件 | `sections` | OPF spine顺序读取+HTML子解析 |
| **JsonParser** | `json_parser.py` | json | JSON/JSONL/bytes | `sections` | JSONL自动检测+递归DFS分块 |
| **TxtParser** | `txt_parser.py` | chardet + find_codec | TXT/代码文件/bytes | `sections` | 80+种编码自动检测+分隔符+Token分块 |
| **PptParser** | `ppt_parser.py` | python-pptx | PPTX 文件/bytes | `sections` | slide shapes按位置排序+表格提取 |

### 4.2 PDF 子引擎对比

| 引擎 | 文件 | 核心技术 | 适用场景 | 成本 |
|------|------|---------|---------|------|
| **PdfParser（默认）** | `pdf_parser.py` | ONNX OCR + Layout + Table | 通用场景 | 免费本地 |
| **MinerU** | `mineru_parser.py` | MinerU API | 高精度正式文档 | 商业付费 |
| **Docling** | `docling_parser.py` | IBM Docling 开源 | 学术论文/数学公式 | 免费本地 |
| **PaddleOCR** | `paddleocr_parser.py` | 百度 PaddleOCR | 中文低质量扫描件 | 免费本地 |
| **TCADP** | `tcadp_parser.py` | 腾讯云 API | 已用腾讯云客户 | 腾讯云付费 |

---

## 五、疑惑解答

**Q1：为什么 PDF 解析器要加载 XGBoost 模型？不能直接用规则判断两个文本框是否应该合并吗？**

规则判断容易在边界场景失效。比如"标题:正文"和"列表:列表项"的间距相似但不应合并，而"被分页截断的同一段落"虽然跨页但应该合并。规则穷举所有情况非常困难，而 XGBoost 可以从 25 个特征中学习出非线性决策边界。模型是预先用标注数据训练的——对大量"应合并"和"不应合并"的文本块对人工标注后训练得到。

**Q2：Excel 为什么需要四级降级加载？一种方法不够吗？**

不同来源的 Excel 文件质量差异巨大：正规 Office 导出的文件 openpyxl 能完美打开；旧版本 Excel 或第三方软件导出的文件 openpyxl 可能报错但 pandas 能容错；超大文件（10万+行）openpyxl 可能内存溢出但 calamine（Rust 引擎）能处理；用户传的 `.csv` 文件用 Excel 解析必然失败。四级降级覆盖了最常见的 4 种边界场景。

**Q3：DOCX 的 `LazyImage` 为什么需要延迟加载？**

DOCX 文件中的图片可能非常多（科技论文、产品手册），而且每张图片可能很大（5MB+）。如果解析阶段就把所有图片解码为 PIL Image 对象加载到内存，内存峰值会很高。`LazyImage` 只存储图片的原始字节（blob），直到分块阶段需要实际裁剪/拼接图片时才调用 `.load()` 解码——这种"只存储不解析"的策略大幅降低了内存占用。

---

## 六、规范修正

- "OCR 降级"指无法通过 pdfplumber 直接提取文字时，用 ONNX OCR 模型重新识别
- "LazyImage"统一使用"延迟加载图片"
- "分块"与"合并"是两个相反操作——解析器先合并相邻文本框（横向+纵向），上层 `naive_merge()` 再按 Token 限制分块
- "布局标签"指 `LayoutRecognizer` 输出的 11 种分类结果

---

## 七、可复现实操步骤

| 步骤 | 操作内容 | 依赖 API / 模块 | 最简代码 | 注意事项 |
|------|----------|----------------|---------|---------|
| 1 | 安装依赖 | pip | `pip install pdfplumber opencv-python python-docx pandas openpyxl beautifulsoup4 chardet pypdf` | 大约 15 个第三方库 |
| 2 | 下载 ONNX 模型 | HuggingFace | OCR/LayoutRecognizer 首次运行自动从 HuggingFace 下载 | 约 500MB，支持 HF 镜像加速 |
| 3 | 解析 PDF | PdfParser | `parser = PdfParser(); sections, tables = parser("doc.pdf", from_page=0, to_page=10)` | 大 PDF 用 from_page/to_page 控制范围 |
| 4 | 解析 DOCX | DocxParser | `parser = DocxParser(); sections = parser("doc.docx")` | 图片通过 `LazyImage` 延迟加载 |
| 5 | 解析 Excel | ExcelParser | `parser = ExcelParser(); sections = parser("data.xlsx")` | CSV 自动透明转换 |
| 6 | 解析 HTML | HtmlParser | `parser = HtmlParser(); sections = parser("page.html")` | 表格自动分离为独立 chunk |

---

## 八、关键模块总览

| 模块名称 | 文件 | 负责功能 | 在流程中的核心作用 |
|----------|------|----------|-------------------|
| `PdfParser` | `pdf_parser.py` | PDF 完整解析 | 最复杂解析器，串联 OCR→Layout→Table→文本合并 |
| `PlainParser` | `pdf_parser.py` | PDF 纯文本提取 | 最快解析器，跳过视觉管道 |
| `VisionParser` | `pdf_parser.py` | Vision LLM 解析 | 用视觉 LLM "看图说话"替代 OCR |
| `DocxParser` | `docx_parser.py` | DOCX 解析 | 按 XML 文档顺序遍历、图片延迟加载 |
| `ExcelParser` | `excel_parser.py` | Excel 解析 | 四级降级加载多引擎兼容 |
| `HtmlParser` | `html_parser.py` | HTML 解析 | DOM 递归遍历、表格 UUID 隔离 |
| `MarkdownParser` | `markdown_parser.py` | Markdown 解析 | 表格提取 + 结构元素提取 |
| `EpubParser` | `epub_parser.py` | EPUB 电子书解析 | OPF spine 顺序 + HTML 子解析 |
| `JsonParser` | `json_parser.py` | JSON/JSONL 解析 | JSONL 自动检测 + DFS 递归分块 |
| `TxtParser` | `txt_parser.py` | 纯文本解析 | 80+ 编码检测 + 分隔符分块 |
| `PptParser` | `ppt_parser.py` | PowerPoint 解析 | 幻灯片 shapes 排序 + 表格提取 |
| `resume/` | `resume/` | 简历专用解析 | 实体识别（公司/学校）+两步分步解析 |
| `figure_parser.py` | `figure_parser.py` | Vision 图片增强 | Vision LLM 为图片生成描述文字 |
