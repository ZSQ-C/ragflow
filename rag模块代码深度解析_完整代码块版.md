# 代码模块解析文档：RAGFlow rag 核心 RAG 引擎

## 1. 模块核心功能总览

`rag` 是 RAGFlow 项目的**核心 RAG（Retrieval-Augmented Generation）引擎**，承担从文档解析、索引构建、检索召回、重排序到生成回答的完整链路职责。与 `deepdoc` 专注于文档解析不同，`rag` 模块聚焦于**知识检索与生成增强**，是连接文档内容和用户问答的桥梁。

**核心能力拆解：**

| 能力维度           | 具体实现                                                                  | 对应代码模块                                  |
| -------------- | --------------------------------------------------------------------- | --------------------------------------- |
| 文档分块（Chunking） | 支持多格式（PDF/DOCX/Excel/TXT/Markdown/HTML/EPUB/JSON）统一分块，可配置分块大小、分隔符、重叠率 | `app/naive.py`                          |
| 全文检索           | 基于 Elasticsearch/Infinity 实现混合检索（全文 + 向量）                             | `nlp/search.py`                         |
| 查询理解与改写        | 中文/英文分词、同义词扩展、权重计算                                                    | `nlp/query.py`                          |
| 重排序（Rerank）    | 内置混合相似度重排序 + 外部 Rerank 模型                                             | `nlp/search.py` + `llm/rerank_model.py` |
| 大模型对话          | 统一封装 OpenAI/Anthropic/Azure/本地模型等 20+ 种 LLM 后端                        | `llm/chat_model.py`                     |
| 嵌入模型           | 支持 OpenAI/BGE/通义千问/LocalAI 等多种 Embedding 服务                           | `llm/embedding_model.py`                |
| Rerank 模型      | 支持 Jina/Xinference/NVIDIA/LocalAI 等 Rerank 服务                         | `llm/rerank_model.py`                   |
| 引用溯源           | 将生成回答与检索到的文本块关联，标注引用 ID                                               | `nlp/search.py`                         |
| 标签系统           | 基于 TF-IDF 的自动标签提取与查询标签匹配                                              | `nlp/search.py`                         |
| 工具调用           | 支持 Function Calling 和 ReAct 模式的工具调用                                   | `llm/chat_model.py`                     |

***

## 2. 关键类 / 核心方法 / 全局变量清单

### 2.1 关键类清单

| 类名                 | 所属文件                     | 核心职责                                         |
| ------------------ | ------------------------ | -------------------------------------------- |
| `Dealer`           | `nlp/search.py`          | 检索召回核心类，封装全文检索、向量检索、混合检索、重排序、标签检索            |
| `FulltextQueryer`  | `nlp/query.py`           | 查询理解类，将自然语言问题转换为检索表达式，支持中英文分词和同义词扩展          |
| `Base` (Chat)      | `llm/chat_model.py`      | 大模型对话基类，封装 OpenAI 标准接口，支持流式/非流式、工具调用、重试容错    |
| `Base` (Embed)     | `llm/embedding_model.py` | 嵌入模型基类，定义 `encode`/`encode_queries` 接口       |
| `Base` (Rerank)    | `llm/rerank_model.py`    | 重排序模型基类，定义 `similarity` 接口                   |
| `OpenAIEmbed`      | `llm/embedding_model.py` | OpenAI Embedding API 封装                      |
| `BuiltinEmbed`     | `llm/embedding_model.py` | 本地内置嵌入模型封装（BGE 等），支持 TEI 服务                  |
| `JinaRerank`       | `llm/rerank_model.py`    | Jina Rerank API 封装                           |
| `XInferenceRerank` | `llm/rerank_model.py`    | Xinference Rerank 服务封装                       |
| `Docx`             | `app/naive.py`           | DOCX 文档解析器，继承 `DocxParser`，增加标题层级提取和表格处理     |
| `Pdf`              | `app/naive.py`           | PDF 文档解析器，继承 `PdfParser`，编排 OCR→版面→表格→合并完整流程 |
| `Markdown`         | `app/naive.py`           | Markdown 解析器，支持图片提取和视觉模型增强                   |

### 2.2 核心方法清单

| 方法名                     | 所属类/模块            | 核心作用                                         |
| ----------------------- | ----------------- | -------------------------------------------- |
| `chunk`                 | `app/naive.py`    | **文档分块主入口**，根据文件扩展名路由到不同解析器，统一输出 chunk 列表    |
| `search`                | `Dealer`          | **检索主入口**，执行全文+向量混合检索，返回结果列表                 |
| `retrieval`             | `Dealer`          | **召回主入口**，封装 search + rerank + 分页 + 阈值过滤完整流程 |
| `rerank`                | `Dealer`          | 内置混合相似度重排序（向量相似度 + 词项相似度 + 标签特征）             |
| `rerank_by_model`       | `Dealer`          | 外部 Rerank 模型重排序，结合词项相似度和标签特征                 |
| `insert_citations`      | `Dealer`          | 将生成回答与检索 chunk 关联，标注引用 ID，支持混合相似度匹配          |
| `question`              | `FulltextQueryer` | 将自然语言问题转换为 `MatchTextExpr` 检索表达式             |
| `hybrid_similarity`     | `FulltextQueryer` | 计算查询与文档的混合相似度（向量 + 词项加权）                     |
| `async_chat_streamly`   | `Base` (Chat)     | 异步流式对话，支持重试、截断提示、推理内容输出                      |
| `async_chat_with_tools` | `Base` (Chat)     | 异步工具调用对话，支持 Function Calling 和 ReAct 模式      |
| `encode`                | `Base` (Embed)    | 批量文本嵌入，返回向量数组                                |
| `similarity`            | `Base` (Rerank)   | 计算查询与文档列表的相关性分数，返回排名分数                       |

### 2.3 核心全局变量与环境变量

| 变量/配置                      | 位置             | 用途                                        |
| -------------------------- | -------------- | ----------------------------------------- |
| `PARSERS`                  | `app/naive.py` | 解析器路由字典，键为解析器名称，值为对应解析函数                  |
| `LLM_TIMEOUT_SECONDS`      | 环境变量           | LLM API 超时时间，默认 600 秒                     |
| `LLM_MAX_RETRIES`          | 环境变量           | LLM 调用最大重试次数，默认 5 次                       |
| `LLM_BASE_DELAY`           | 环境变量           | LLM 重试基础延迟，默认 2.0 秒                       |
| `DOC_ENGINE_INFINITY`      | `settings`     | 是否使用 Infinity 作为文档存储引擎（否则用 Elasticsearch） |
| `chunk_token_num`          | Parser Config  | 分块 token 数上限，默认 128-512                   |
| `delimiter`                | Parser Config  | 分块分隔符，默认 `\n!?。；！？`                       |
| `overlapped_percent`       | Parser Config  | 分块重叠率，默认 0%                               |
| `vector_similarity_weight` | 检索参数           | 向量相似度权重，默认 0.3-0.7                        |
| `similarity_threshold`     | 检索参数           | 相似度阈值，默认 0.2                              |

***

## 3. 代码分模块逻辑解析（含完整代码块 + 分步讲解）

### 3.1 文档分块路由与多格式支持

#### 3.1.1 完整功能实现代码

```python
def chunk(filename, binary=None, from_page=0, to_page=100000, lang="Chinese", callback=None, **kwargs):
    """
    Supported file formats are docx, pdf, excel, txt.
    This method apply the naive ways to chunk files.
    Successive text will be sliced into pieces using 'delimiter'.
    Next, these successive pieces are merge into chunks whose token number is no more than 'Max token number'.
    """
    urls = set()
    url_res = []

    is_english = lang.lower() == "english"
    parser_config = kwargs.get("parser_config", {"chunk_token_num": 512, "delimiter": "\n!?。；！？", "layout_recognize": "DeepDOC", "analyze_hyperlink": True})

    child_deli = (parser_config.get("children_delimiter") or "").encode("utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
    cust_child_deli = re.findall(r"`([^`]+)`", child_deli)
    child_deli = "|".join(re.sub(r"`([^`]+)`", "", child_deli))
    if cust_child_deli:
        cust_child_deli = sorted(set(cust_child_deli), key=lambda x: -len(x))
        cust_child_deli = "|".join(re.escape(t) for t in cust_child_deli if t)
        child_deli += cust_child_deli

    is_markdown = False
    table_context_size = max(0, int(parser_config.get("table_context_size", 0) or 0))
    image_context_size = max(0, int(parser_config.get("image_context_size", 0) or 0))

    doc = {"docnm_kwd": filename, "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))}
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])
    res = []
    pdf_parser = None
    section_images = None

    is_root = kwargs.get("is_root", True)
    embed_res = []
    if is_root:
        embeds = []
        if binary is not None:
            embeds = extract_embed_file(binary)
        else:
            raise Exception("Embedding extraction from file path is not supported.")

        for embed_filename, embed_bytes in embeds:
            try:
                sub_res = chunk(embed_filename, binary=embed_bytes, lang=lang, callback=callback, is_root=False, **kwargs) or []
                embed_res.extend(sub_res)
            except Exception as e:
                error_msg = f"Failed to chunk embed {embed_filename}: {e}"
                logging.error(error_msg)
                if callback:
                    callback(0.05, error_msg)
                continue

    if re.search(r"\.docx$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        if parser_config.get("analyze_hyperlink", False) and is_root:
            urls = extract_links_from_docx(binary)
            for index, url in enumerate(urls):
                html_bytes, metadata = extract_html(url)
                if not html_bytes:
                    continue
                try:
                    sub_url_res = chunk(url, html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
                except Exception as e:
                    logging.info(f"Failed to chunk url in registered file type {url}: {e}")
                    sub_url_res = chunk(f"{index}.html", html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
                url_res.extend(sub_url_res)

        _SerializedRelationships.load_from_xml = load_from_xml_v2
        sections = Docx()(filename, binary)
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        chunks, images = naive_merge_docx(sections, int(parser_config.get("chunk_token_num", 128)), parser_config.get("delimiter", "\n!?。；！？"), table_context_size, image_context_size)
        vision_figure_parser_docx_wrapper_naive(chunks=chunks, idx_lst=images, callback=callback, **kwargs)
        callback(0.8, "Finish parsing.")
        st = timer()
        res.extend(doc_tokenize_chunks_with_images(chunks, doc, is_english, child_delimiters_pattern=child_deli))
        logging.info("naive_merge({}): {}".format(filename, timer() - st))
        res.extend(embed_res)
        res.extend(url_res)
        return res

    elif re.search(r"\.pdf$", filename, re.IGNORECASE):
        layout_recognizer, parser_model_name = normalize_layout_recognizer(parser_config.get("layout_recognize", "DeepDOC"))
        if parser_config.get("analyze_hyperlink", False) and is_root:
            urls = extract_links_from_pdf(binary)
        if isinstance(layout_recognizer, bool):
            layout_recognizer = "DeepDOC" if layout_recognizer else "PlainText"
        name = layout_recognizer.strip().lower()
        parser = PARSERS.get(name, by_plaintext)
        callback(0.1, "Start to parse.")
        sections, tables, pdf_parser = parser(
            filename=filename, binary=binary, from_page=from_page, to_page=to_page, lang=lang,
            callback=callback, layout_recognizer=layout_recognizer,
            mineru_llm_name=parser_model_name, paddleocr_llm_name=parser_model_name, **kwargs,
        )
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        if not sections and not tables:
            return []
        if table_context_size or image_context_size:
            tables = append_context2table_image4pdf(sections, tables, image_context_size)
        if name in ["tcadp", "docling", "mineru", "paddleocr"]:
            if int(parser_config.get("chunk_token_num", 0)) <= 0:
                parser_config["chunk_token_num"] = 0
        res = tokenize_table(tables, doc, is_english)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.(csv|xlsx?)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        layout_recognizer = parser_config.get("layout_recognize", "DeepDOC")
        if layout_recognizer == "TCADP Parser":
            table_result_type = parser_config.get("table_result_type", "1")
            markdown_image_response_type = parser_config.get("markdown_image_response_type", "1")
            tcadp_parser = TCADPParser(table_result_type=table_result_type, markdown_image_response_type=markdown_image_response_type)
            if not tcadp_parser.check_installation():
                callback(-1, "TCADP parser not available. Please check Tencent Cloud API configuration.")
                return res
            file_type = "XLSX" if re.search(r"\.xlsx?$", filename, re.IGNORECASE) else "CSV"
            sections, tables = tcadp_parser.parse_pdf(filepath=filename, binary=binary, callback=callback, output_dir=os.environ.get("TCADP_OUTPUT_DIR", ""), file_type=file_type)
            sections = _normalize_section_text_for_rtl_presentation_forms(sections)
            parser_config["chunk_token_num"] = 0
            res = tokenize_table(tables, doc, is_english)
            callback(0.8, "Finish parsing.")
        else:
            excel_parser = ExcelParser()
            if parser_config.get("html4excel"):
                sections = [(_, "") for _ in excel_parser.html(binary, 12) if _]
                parser_config["chunk_token_num"] = 0
            else:
                sections = [(_, "") for _ in excel_parser(binary) if _]
            sections = _normalize_section_text_for_rtl_presentation_forms(sections)

    elif re.search(r"\.(txt|py|js|java|c|cpp|h|php|go|ts|sh|cs|kt|sql)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        sections = TxtParser()(filename, binary, parser_config.get("chunk_token_num", 128), parser_config.get("delimiter", "\n!?;。；！？"))
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.(md|markdown|mdx)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        markdown_parser = Markdown(int(parser_config.get("chunk_token_num", 128)))
        sections, tables, section_images = markdown_parser(filename, binary, separate_tables=False, delimiter=parser_config.get("delimiter", "\n!?;。；！？"), return_section_images=True)
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        is_markdown = True
        try:
            vision_model_config = get_tenant_default_model_by_type(kwargs["tenant_id"], LLMType.IMAGE2TEXT)
            vision_model = LLMBundle(kwargs["tenant_id"], vision_model_config)
            callback(0.2, "Visual model detected. Attempting to enhance figure extraction...")
        except Exception as e:
            logging.warning(f"Failed to detect figure extraction: {e}")
            vision_model = None
        if vision_model:
            for idx, (section_text, _) in enumerate(sections):
                images = []
                if section_images and len(section_images) > idx and section_images[idx] is not None:
                    images.append(section_images[idx])
                if images and len(images) > 0:
                    combined_image = reduce(concat_img, images) if len(images) > 1 else images[0]
                    if section_images:
                        section_images[idx] = combined_image
                    else:
                        section_images = [None] * len(sections)
                        section_images[idx] = combined_image
                    markdown_vision_parser = VisionFigureParser(vision_model=vision_model, figures_data=[((combined_image, ["markdown image"]), [(0, 0, 0, 0, 0)])], **kwargs)
                    boosted_figures = markdown_vision_parser(callback=callback)
                    sections[idx] = (section_text + "\n\n" + "\n\n".join([fig[0][1] for fig in boosted_figures]), sections[idx][1])
        else:
            logging.warning("No visual model detected. Skipping figure parsing enhancement.")
        if parser_config.get("hyperlink_urls", False) and is_root:
            for idx, (section_text, _) in enumerate(sections):
                soup = markdown_parser.md_to_html(section_text)
                hyperlink_urls = markdown_parser.get_hyperlink_urls(soup)
                urls.update(hyperlink_urls)
        res = tokenize_table(tables, doc, is_english)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.(htm|html)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        chunk_token_num = int(parser_config.get("chunk_token_num", 128))
        sections = HtmlParser()(filename, binary, chunk_token_num)
        sections = [(_, "") for _ in sections if _]
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.epub$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        chunk_token_num = int(parser_config.get("chunk_token_num", 128))
        sections = EpubParser()(filename, binary, chunk_token_num)
        sections = [(_, "") for _ in sections if _]
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.(json|jsonl|ldjson)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        chunk_token_num = int(parser_config.get("chunk_token_num", 128))
        sections = JsonParser(chunk_token_num)(binary)
        sections = [(_, "") for _ in sections if _]
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.doc$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        try:
            from tika import parser as tika_parser
        except Exception as e:
            callback(0.8, f"tika not available: {e}. Unsupported .doc parsing.")
            logging.warning(f"tika not available: {e}. Unsupported .doc parsing for {filename}.")
            return []
        binary = BytesIO(binary)
        doc_parsed = tika_parser.from_buffer(binary)
        if doc_parsed.get("content", None) is not None:
            sections = doc_parsed["content"].split("\n")
            sections = [(_, "") for _ in sections if _]
            sections = _normalize_section_text_for_rtl_presentation_forms(sections)
            callback(0.8, "Finish parsing.")
        else:
            error_msg = f"tika.parser got empty content from {filename}."
            callback(0.8, error_msg)
            logging.warning(error_msg)
            return []
    else:
        raise NotImplementedError("file type not supported yet(pdf, xlsx, doc, docx, txt supported)")

    st = timer()
    overlapped_percent = normalize_overlapped_percent(parser_config.get("overlapped_percent", 0))
    if is_markdown:
        merged_chunks = []
        merged_images = []
        chunk_limit = max(0, int(parser_config.get("chunk_token_num", 128)))
        current_text = ""
        current_tokens = 0
        current_image = None
        for idx, sec in enumerate(sections):
            text = sec[0] if isinstance(sec, tuple) else sec
            sec_tokens = num_tokens_from_string(text)
            sec_image = section_images[idx] if section_images and idx < len(section_images) else None
            if current_text and current_tokens + sec_tokens > chunk_limit:
                merged_chunks.append(current_text)
                merged_images.append(current_image)
                overlap_part = ""
                if overlapped_percent > 0:
                    overlap_len = int(len(current_text) * overlapped_percent / 100)
                    if overlap_len > 0:
                        overlap_part = current_text[-overlap_len:]
                current_text = overlap_part
                current_tokens = num_tokens_from_string(current_text)
                current_image = current_image if overlap_part else None
            if current_text:
                current_text += "\n" + text
            else:
                current_text = text
            current_tokens += sec_tokens
            if sec_image:
                current_image = concat_img(current_image, sec_image) if current_image else sec_image
        if current_text:
            merged_chunks.append(current_text)
            merged_images.append(current_image)
        chunks = merged_chunks
        has_images = merged_images and any(img is not None for img in merged_images)
        if has_images:
            res.extend(tokenize_chunks_with_images(chunks, doc, is_english, merged_images, child_delimiters_pattern=child_deli))
        else:
            res.extend(tokenize_chunks(chunks, doc, is_english, pdf_parser, child_delimiters_pattern=child_deli))
    else:
        if section_images:
            if all(image is None for image in section_images):
                section_images = None
        if section_images:
            chunks, images = naive_merge_with_images(sections, section_images, int(parser_config.get("chunk_token_num", 128)), parser_config.get("delimiter", "\n!?。；！？"), overlapped_percent)
            res.extend(tokenize_chunks_with_images(chunks, doc, is_english, images, child_delimiters_pattern=child_deli))
        else:
            chunks = naive_merge(sections, int(parser_config.get("chunk_token_num", 128)), parser_config.get("delimiter", "\n!?。；！？"), overlapped_percent)
            res.extend(tokenize_chunks(chunks, doc, is_english, pdf_parser, child_delimiters_pattern=child_deli))

    if urls and parser_config.get("analyze_hyperlink", False) and is_root:
        for index, url in enumerate(urls):
            html_bytes, metadata = extract_html(url)
            if not html_bytes:
                continue
            try:
                sub_url_res = chunk(url, html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
            except Exception as e:
                logging.info(f"Failed to chunk url in registered file type {url}: {e}")
                sub_url_res = chunk(f"{index}.html", html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
            url_res.extend(sub_url_res)

    logging.info("naive_merge({}): {}".format(filename, timer() - st))
    if embed_res:
        res.extend(embed_res)
    if url_res:
        res.extend(url_res)
    return res
```

#### 3.1.2 分步实现讲解

**步骤 1：初始化与配置解析**

- 从 `kwargs` 中提取 `parser_config`，包含 `chunk_token_num`（分块大小）、`delimiter`（分隔符）、`layout_recognize`（PDF 解析器选择）、`analyze_hyperlink`（是否追踪超链接）
- 解析 `children_delimiter` 子分隔符配置，支持自定义分隔符（用反引号包裹）
- 提取 `table_context_size` 和 `image_context_size`，用于表格和图片上下文关联
- 初始化 `doc` 字典，包含文件名和标题 token，供后续索引使用

**步骤 2：嵌入文件递归处理（仅根调用）**

- `is_root` 参数控制是否处理嵌入文件（如 DOCX 中嵌入的 OLE 对象）
- 调用 `extract_embed_file(binary)` 提取嵌入文件列表
- 对每个嵌入文件递归调用 `chunk`，传入 `is_root=False` 避免无限递归
- 异常时记录日志并继续处理其他嵌入文件，保证容错

**步骤 3：文件类型路由与解析**

- 通过正则匹配文件扩展名，路由到不同解析器：
  - `.docx`：调用 `Docx` 解析器，提取段落、图片、表格，支持标题层级结构
  - `.pdf`：根据 `layout_recognize` 配置从 `PARSERS` 字典选择解析器（deepdoc/mineru/docling/tcadp/paddleocr/plaintext）
  - `.csv/.xlsx`：调用 `ExcelParser` 或 `TCADPParser`
  - `.txt/.py/.js/.java` 等代码文件：调用 `TxtParser`
  - `.md/.markdown`：调用 `Markdown` 解析器，支持图片提取和视觉模型增强
  - `.htm/.html`：调用 `HtmlParser`
  - `.epub`：调用 `EpubParser`
  - `.json/.jsonl`：调用 `JsonParser`
  - `.doc`：调用 `tika` 解析器（需要额外安装）

**步骤 4：超链接追踪（可选）**

- 对于 DOCX 和 PDF，如果配置了 `analyze_hyperlink`，提取文档中的 URL
- 下载网页内容后递归分块（`is_root=False`）
- 失败时降级为纯 HTML 分块（`chunk(f"{index}.html", html_bytes, ...)`），保证容错

**步骤 5：分块与重叠处理**

- 根据 `parser_config` 中的 `chunk_token_num` 和 `delimiter` 进行分块
- Markdown 文件使用自定义合并逻辑，支持图片关联
- 其他文件使用 `naive_merge` 或 `naive_merge_with_images`
- `overlapped_percent` 控制相邻 chunk 的重叠率，默认 0%。重叠部分从前一个 chunk 的尾部截取，保证语义连续性

**步骤 6：Tokenize 与输出**

- `tokenize_chunks` 或 `tokenize_chunks_with_images` 生成 chunk 元数据（token、位置、标签等）
- 合并嵌入文件结果和超链接追踪结果
- 返回结构化 chunk 列表，供后续索引

***

### 3.2 DOCX 解析器：标题层级提取与表格处理

#### 3.2.1 完整功能实现代码

```python
class Docx(DocxParser):
    def __init__(self):
        pass

    def __clean(self, line):
        line = re.sub(r"\u3000", " ", line).strip()
        return line

    def __get_nearest_title(self, table_index, filename):
        """Get the hierarchical title structure before the table"""
        import re
        from docx.text.paragraph import Paragraph

        titles = []
        blocks = []

        doc_name = re.sub(r"\.[a-zA-Z]+$", "", filename)
        if not doc_name:
            doc_name = "Untitled Document"

        try:
            for i, block in enumerate(self.doc._element.body):
                if block.tag.endswith("p"):
                    p = Paragraph(block, self.doc)
                    blocks.append(("p", i, p))
                elif block.tag.endswith("tbl"):
                    blocks.append(("t", i, None))
        except Exception as e:
            logging.error(f"Error collecting blocks: {e}")
            return ""

        target_table_pos = -1
        table_count = 0
        for i, (block_type, pos, _) in enumerate(blocks):
            if block_type == "t":
                if table_count == table_index:
                    target_table_pos = pos
                    break
                table_count += 1

        if target_table_pos == -1:
            return ""

        nearest_title = None
        for i in range(len(blocks) - 1, -1, -1):
            block_type, pos, block = blocks[i]
            if pos >= target_table_pos:
                continue
            if block_type != "p":
                continue
            if block.style and block.style.name and re.search(r"Heading\s*(\d+)", block.style.name, re.I):
                try:
                    level_match = re.search(r"(\d+)", block.style.name)
                    if level_match:
                        level = int(level_match.group(1))
                        if level <= 7:
                            title_text = block.text.strip()
                            if title_text:
                                nearest_title = (level, title_text)
                                break
                except Exception as e:
                    logging.error(f"Error parsing heading level: {e}")

        if nearest_title:
            titles.append(nearest_title)
            current_level = nearest_title[0]
            while current_level > 1:
                found = False
                for i in range(len(blocks) - 1, -1, -1):
                    block_type, pos, block = blocks[i]
                    if pos >= target_table_pos:
                        continue
                    if block_type != "p":
                        continue
                    if block.style and re.search(r"Heading\s*(\d+)", block.style.name, re.I):
                        try:
                            level_match = re.search(r"(\d+)", block.style.name)
                            if level_match:
                                level = int(level_match.group(1))
                                if level < current_level:
                                    title_text = block.text.strip()
                                    if title_text:
                                        titles.append((level, title_text))
                                        current_level = level
                                        found = True
                                        break
                        except Exception as e:
                            logging.error(f"Error parsing parent heading: {e}")
                if not found:
                    break
            titles.sort(key=lambda x: x[0])
            hierarchy = [doc_name] + [t[1] for t in titles]
            return " > ".join(hierarchy)
        return ""

    def __call__(self, filename, binary=None, from_page=0, to_page=100000):
        self.doc = Document(filename) if not binary else Document(BytesIO(binary))
        pn = 0
        lines = []
        last_image = None
        table_idx = 0

        def flush_last_image():
            nonlocal last_image, lines
            if last_image is not None:
                lines.append({"text": "", "image": last_image, "table": None, "style": "Image"})
                last_image = None

        for block in self.doc._element.body:
            if pn > to_page:
                break
            if block.tag.endswith("p"):
                p = Paragraph(block, self.doc)
                if from_page <= pn < to_page:
                    text = p.text.strip()
                    style_name = p.style.name if p.style else ""
                    if text:
                        if style_name == "Caption":
                            former_image = None
                            if lines and lines[-1].get("image") and lines[-1].get("style") != "Caption":
                                former_image = lines[-1].get("image")
                                lines.pop()
                            elif last_image is not None:
                                former_image = last_image
                                last_image = None
                            lines.append({"text": self.__clean(text), "image": former_image if former_image else None, "table": None})
                        else:
                            flush_last_image()
                            lines.append({"text": self.__clean(text), "image": None, "table": None})
                            current_image = self.get_picture(self.doc, p)
                            if current_image is not None:
                                lines.append({"text": "", "image": current_image, "table": None})
                    else:
                        current_image = self.get_picture(self.doc, p)
                        if current_image is not None:
                            last_image = current_image
                for run in p.runs:
                    xml = run._element.xml
                    if "lastRenderedPageBreak" in xml:
                        pn += 1
                        continue
                    if "w:br" in xml and 'type="page"' in xml:
                        pn += 1
            elif block.tag.endswith("tbl"):
                if pn < from_page or pn > to_page:
                    table_idx += 1
                    continue
                flush_last_image()
                tb = DocxTable(block, self.doc)
                title = self.__get_nearest_title(table_idx, filename)
                html = "<table>"
                if title:
                    html += f"<caption>Table Location: {title}</caption>"
                for r in tb.rows:
                    html += "<tr>"
                    col_idx = 0
                    try:
                        while col_idx < len(r.cells):
                            span = 1
                            c = r.cells[col_idx]
                            for j in range(col_idx + 1, len(r.cells)):
                                if c.text == r.cells[j].text:
                                    span += 1
                                    col_idx = j
                                else:
                                    break
                            col_idx += 1
                            html += f"<td>{c.text}</td>" if span == 1 else f"<td colspan='{span}'>{c.text}</td>"
                    except Exception as e:
                        logging.warning(f"Error parsing table, ignore: {e}")
                    html += "</tr>"
                html += "</table>"
                lines.append({"text": "", "image": None, "table": html})
                table_idx += 1
        flush_last_image()
        new_line = [(line.get("text"), line.get("image"), line.get("table")) for line in lines]
        return new_line
```

#### 3.2.2 分步实现讲解

**步骤 1：标题层级提取（`__get_nearest_title`）**

- 遍历 DOCX 的 XML 结构，按文档顺序收集所有段落（`p`）和表格（`tbl`）块
- 找到目标表格的位置（`target_table_pos`）
- 从表格位置向前逆向遍历，找到最近的 Heading 段落（支持 1-7 级）
- 继续向上搜索父级标题（级别更低的标题），构建完整的标题层级路径
- 返回格式：`文档名 > 一级标题 > 二级标题 > ...`

**步骤 2：段落与图片关联（`__call__`）**

- 遍历文档的每个块（段落或表格）
- 对于段落：
  - 如果文本非空且样式为 "Caption"，将 Caption 与前面的图片关联（弹出前面的图片行，合并为一条）
  - 如果文本非空且不是 Caption，先 flush 之前的图片，然后添加文本行，再检查是否有新图片
  - 如果文本为空但有图片，缓存图片到 `last_image`
- 对于表格：
  - 先 flush 之前的图片
  - 调用 `__get_nearest_title` 获取标题层级路径作为 caption
  - 将表格转换为 HTML 格式，支持 colspan（合并单元格检测）

**步骤 3：页码追踪**

- 通过检查 `lastRenderedPageBreak` 和 `w:br type="page"` 追踪页码
- 支持 `from_page` 和 `to_page` 参数控制解析范围

***

### 3.3 检索召回核心：Dealer 类

#### 3.3.1 完整功能实现代码

```python
class Dealer:
    def __init__(self, dataStore: DocStoreConnection):
        self.qryr = query.FulltextQueryer()
        self.dataStore = dataStore

    @dataclass
    class SearchResult:
        total: int
        ids: list[str]
        query_vector: list[float] | None = None
        field: dict | None = None
        highlight: dict | None = None
        aggregation: list | dict | None = None
        keywords: list[str] | None = None
        group_docs: list[list] | None = None

    async def get_vector(self, txt, emb_mdl, topk=10, similarity=0.1):
        qv, _ = await thread_pool_exec(emb_mdl.encode_queries, txt)
        shape = np.array(qv).shape
        if len(shape) > 1:
            raise Exception(f"Dealer.get_vector returned array's shape {shape} doesn't match expectation(exact one dimension).")
        embedding_data = [get_float(v) for v in qv]
        vector_column_name = f"q_{len(embedding_data)}_vec"
        return MatchDenseExpr(vector_column_name, embedding_data, 'float', 'cosine', topk, {"similarity": similarity})

    def get_filters(self, req):
        condition = dict()
        for key, field in {"kb_ids": "kb_id", "doc_ids": "doc_id"}.items():
            if key in req and req[key] is not None:
                condition[field] = req[key]
        for key in ["knowledge_graph_kwd", "available_int", "entity_kwd", "from_entity_kwd", "to_entity_kwd", "removed_kwd"]:
            if key in req and req[key] is not None:
                condition[key] = req[key]
        return condition

    async def search(self, req, idx_names: str | list[str], kb_ids: list[str], emb_mdl=None, highlight: bool | list | None = None, rank_feature: dict | None = None):
        if highlight is None:
            highlight = False
        filters = self.get_filters(req)
        orderBy = OrderByExpr()
        pg = int(req.get("page", 1)) - 1
        topk = int(req.get("topk", 1024))
        ps = int(req.get("size", topk))
        offset, limit = pg * ps, ps
        src = req.get("fields", ["docnm_kwd", "content_ltks", "kb_id", "img_id", "title_tks", "important_kwd", "position_int", "doc_id", "page_num_int", "top_int", "create_timestamp_flt", "knowledge_graph_kwd", "question_kwd", "question_tks", "doc_type_kwd", "available_int", "content_with_weight", "mom_id", PAGERANK_FLD, TAG_FLD])
        kwds = set([])
        qst = req.get("question", "")
        q_vec = []
        if not qst:
            if req.get("sort"):
                orderBy.asc("page_num_int")
                orderBy.asc("top_int")
                orderBy.desc("create_timestamp_flt")
            res = self.dataStore.search(src, [], filters, [], orderBy, offset, limit, idx_names, kb_ids)
            total = self.dataStore.get_total(res)
            logging.debug("Dealer.search TOTAL: {}".format(total))
        else:
            highlightFields = ["content_ltks", "title_tks"]
            if not highlight:
                highlightFields = []
            elif isinstance(highlight, list):
                highlightFields = highlight
            matchText, keywords = self.qryr.question(qst, min_match=0.3)
            if emb_mdl is None:
                matchExprs = [matchText]
                res = await thread_pool_exec(self.dataStore.search, src, highlightFields, filters, matchExprs, orderBy, offset, limit, idx_names, kb_ids, rank_feature=rank_feature)
                total = self.dataStore.get_total(res)
                logging.debug("Dealer.search TOTAL: {}".format(total))
            else:
                matchDense = await self.get_vector(qst, emb_mdl, topk, req.get("similarity", 0.1))
                q_vec = matchDense.embedding_data
                if not settings.DOC_ENGINE_INFINITY:
                    src.append(f"q_{len(q_vec)}_vec")
                fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
                matchExprs = [matchText, matchDense, fusionExpr]
                res = await thread_pool_exec(self.dataStore.search, src, highlightFields, filters, matchExprs, orderBy, offset, limit, idx_names, kb_ids, rank_feature=rank_feature)
                total = self.dataStore.get_total(res)
                logging.debug("Dealer.search TOTAL: {}".format(total))
                if total == 0:
                    if filters.get("doc_id"):
                        res = await thread_pool_exec(self.dataStore.search, src, [], filters, [], orderBy, offset, limit, idx_names, kb_ids)
                        total = self.dataStore.get_total(res)
                    else:
                        matchText, _ = self.qryr.question(qst, min_match=0.1)
                        matchDense.extra_options["similarity"] = 0.17
                        res = await thread_pool_exec(self.dataStore.search, src, highlightFields, filters, [matchText, matchDense, fusionExpr], orderBy, offset, limit, idx_names, kb_ids, rank_feature=rank_feature)
                        total = self.dataStore.get_total(res)
                    logging.debug("Dealer.search 2 TOTAL: {}".format(total))
            for k in keywords:
                kwds.add(k)
                for kk in rag_tokenizer.fine_grained_tokenize(k).split():
                    if len(kk) < 2:
                        continue
                    if kk in kwds:
                        continue
                    kwds.add(kk)
        logging.debug(f"TOTAL: {total}")
        ids = self.dataStore.get_doc_ids(res)
        keywords = list(kwds)
        highlight = self.dataStore.get_highlight(res, keywords, "content_with_weight")
        aggs = self.dataStore.get_aggregation(res, "docnm_kwd")
        return self.SearchResult(total=total, ids=ids, query_vector=q_vec, aggregation=aggs, highlight=highlight, field=self.dataStore.get_fields(res, src + ["_score"]), keywords=keywords)
```

#### 3.3.2 分步实现讲解

**步骤 1：初始化与过滤条件构建**

- `__init__` 初始化 `FulltextQueryer`（查询理解）和 `DocStoreConnection`（文档存储连接）
- `get_filters` 从请求中提取过滤条件：`kb_ids` → `kb_id`、`doc_ids` → `doc_id`，以及知识图谱、可用性、实体等字段

**步骤 2：分页与字段配置**

- 计算分页参数：`offset = (page - 1) * size`，`limit = size`
- 默认返回字段包括：文档名、内容 token、标题 token、重要关键词、位置、页码、创建时间、知识图谱、问题 token、文档类型、内容原文、PageRank、标签等

**步骤 3：无查询词模式**

- 如果 `question` 为空，按排序字段返回结果（默认按页码、位置升序，创建时间降序）
- 这种模式用于浏览文档，不做相关性排序

**步骤 4：全文检索模式（无嵌入模型）**

- 调用 `FulltextQueryer.question` 将自然语言问题转换为 `MatchTextExpr`
- `min_match=0.3` 控制最少匹配词项比例
- 执行全文检索，返回结果

**步骤 5：混合检索模式（有嵌入模型）**

- 调用 `get_vector` 将查询文本编码为向量，构建 `MatchDenseExpr`
- 向量维度动态确定（`q_{len}_vec`），相似度度量使用 cosine
- 构建 `FusionExpr` 加权融合全文和向量分数（权重 0.05:0.95，向量占主导）
- 执行混合检索

**步骤 6：空结果兜底**

- 如果混合检索无结果，执行两级兜底：
  - 如果指定了 `doc_id`，直接按 `doc_id` 过滤返回（绕过相关性要求）
  - 否则降低 `min_match` 到 0.1，降低向量相似度阈值到 0.17，重新检索

**步骤 7：关键词扩展与结果组装**

- 对检索到的关键词进行细粒度分词扩展，加入同义词
- 组装 `SearchResult`：总数、ID 列表、查询向量、高亮、聚合统计、关键词

***

### 3.4 引用溯源：insert\_citations 方法

#### 3.4.1 完整功能实现代码

````python
    def insert_citations(self, answer, chunks, chunk_v, embd_mdl, tkweight=0.1, vtweight=0.9):
        assert len(chunks) == len(chunk_v)
        if not chunks:
            return answer, set([])
        pieces = re.split(r"(```)", answer)
        if len(pieces) >= 3:
            i = 0
            pieces_ = []
            while i < len(pieces):
                if pieces[i] == "```":
                    st = i
                    i += 1
                    while i < len(pieces) and pieces[i] != "```":
                        i += 1
                    if i < len(pieces):
                        i += 1
                    pieces_.append("".join(pieces[st: i]) + "\n")
                else:
                    pieces_.extend(re.split(r"([^\|][；。？!！،؛؟۔\n]|[a-z\u0600-\u06FF][.?;!،؛؟][ \n])", pieces[i]))
                i += 1
            pieces = pieces_
        else:
            pieces = re.split(r"([^\|][；。？!！،؛؟۔\n]|[a-z\u0600-\u06FF][.?;!،؛؟][ \n])", answer)
        for i in range(1, len(pieces)):
            if re.match(r"([^\|][；。？!！،؛؟۔\n]|[a-z\u0600-\u06FF][.?;!،؛؟][ \n])", pieces[i]):
                pieces[i - 1] += pieces[i][0]
                pieces[i] = pieces[i][1:]
        idx = []
        pieces_ = []
        for i, t in enumerate(pieces):
            if len(t) < 5:
                continue
            idx.append(i)
            pieces_.append(t)
        logging.debug("{} => {}".format(answer, pieces_))
        if not pieces_:
            return answer, set([])
        ans_v, _ = embd_mdl.encode(pieces_)
        for i in range(len(chunk_v)):
            if len(ans_v[0]) != len(chunk_v[i]):
                chunk_v[i] = [0.0] * len(ans_v[0])
                logging.warning("The dimension of query and chunk do not match: {} vs. {}".format(len(ans_v[0]), len(chunk_v[i])))
        assert len(ans_v[0]) == len(chunk_v[0]), "The dimension of query and chunk do not match: {} vs. {}".format(len(ans_v[0]), len(chunk_v[0]))
        chunks_tks = [rag_tokenizer.tokenize(self.qryr.rmWWW(ck)).split() for ck in chunks]
        cites = {}
        thr = 0.63
        while thr > 0.3 and len(cites.keys()) == 0 and pieces_ and chunks_tks:
            for i, a in enumerate(pieces_):
                sim, tksim, vtsim = self.qryr.hybrid_similarity(ans_v[i], chunk_v, rag_tokenizer.tokenize(self.qryr.rmWWW(pieces_[i])).split(), chunks_tks, tkweight, vtweight)
                mx = np.max(sim) * 0.99
                logging.debug("{} SIM: {}".format(pieces_[i], mx))
                if mx < thr:
                    continue
                cites[idx[i]] = list(set([str(ii) for ii in range(len(chunk_v)) if sim[ii] > mx]))[:4]
            thr *= 0.8
        res = ""
        seted = set([])
        for i, p in enumerate(pieces):
            res += p
            if i not in idx:
                continue
            if i not in cites:
                continue
            for c in cites[i]:
                assert int(c) < len(chunk_v)
            for c in cites[i]:
                if c in seted:
                    continue
                res += f" [ID:{c}]"
                seted.add(c)
        return res, seted
````

#### 3.4.2 分步实现讲解

**步骤 1：回答切分**

- 先将代码块（`...`）保护起来，不拆分
- 对非代码块部分，按句子边界切分，支持中文（。；！？）、英文（.?!;）、阿拉伯文（،؛؟۔）标点
- 过滤掉长度小于 5 的片段（太短的句子没有引用价值）

**步骤 2：向量编码与维度对齐**

- 对每个句子片段编码向量
- 检查 chunk 向量维度是否匹配，不匹配时填充零向量并报警

**步骤 3：混合相似度计算**

- 对每个句子，计算与所有 chunk 的混合相似度（向量 + 词项加权）
- 动态阈值：从 0.63 开始，每次乘以 0.8 降低，直到找到匹配或低于 0.3
- 对每个句子，找到相似度超过阈值（最大值的 99%）的 chunk，最多 4 个

**步骤 4：引用插入**

- 按原始顺序拼接回答片段
- 对有引用的句子，在句末插入 `[ID:N]` 标记
- 避免重复引用同一个 chunk

***

### 3.5 查询理解与全文检索：FulltextQueryer 类

#### 3.5.1 完整功能实现代码

```python
class FulltextQueryer(QueryBase):
    def __init__(self):
        self.tw = term_weight.Dealer()
        self.syn = synonym.Dealer()
        self.query_fields = [
            "title_tks^10", "title_sm_tks^5", "important_kwd^30", "important_tks^20",
            "question_tks^20", "content_ltks^2", "content_sm_ltks",
        ]

    def question(self, txt, tbl="qa", min_match: float = 0.6):
        original_query = txt
        txt = self.add_space_between_eng_zh(txt)
        txt = re.sub(r"[ :|\r\n\t,，。？?/`!！&^%%()\[\]{}<>*~'"\\]+", " ", rag_tokenizer.tradi2simp(rag_tokenizer.strQ2B(txt.lower()))).strip()
        otxt = txt
        txt = self.rmWWW(txt)
        if not self.is_chinese(txt):
            txt = self.rmWWW(txt)
            tks = rag_tokenizer.tokenize(txt).split()
            keywords = [t for t in tks if t]
            tks_w = self.tw.weights(tks, preprocess=False)
            tks_w = [(re.sub(r"[ \\"'^]", "", tk), w) for tk, w in tks_w]
            tks_w = [(re.sub(r"^[\+-]", "", tk), w) for tk, w in tks_w if tk]
            tks_w = [(tk.strip(), w) for tk, w in tks_w if tk.strip()]
            syns = []
            for tk, w in tks_w[:256]:
                syn = [rag_tokenizer.tokenize(s) for s in self.syn.lookup(tk)]
                keywords.extend(syn)
                syn = ["\"{}\"^{:.4f}".format(s, w / 4.) for s in syn if s.strip()]
                syns.append(" ".join(syn))
            q = ["({}^{:.4f}".format(tk, w) + " {})".format(syn) for (tk, w), syn in zip(tks_w, syns) if tk and not re.match(r"[.^+\(\)-]", tk)]
            for i in range(1, len(tks_w)):
                left, right = tks_w[i - 1][0].strip(), tks_w[i][0].strip()
                if not left or not right:
                    continue
                q.append('"%s %s"^%.4f' % (tks_w[i - 1][0], tks_w[i][0], max(tks_w[i - 1][1], tks_w[i][1]) * 2))
            if not q:
                q.append(txt)
            query = " ".join(q)
            return MatchTextExpr(self.query_fields, query, 100, {"original_query": original_query}), keywords

        def need_fine_grained_tokenize(tk):
            if len(tk) < 3:
                return False
            if re.match(r"[0-9a-z\.\+#_\*-]+$", tk):
                return False
            return True

        txt = self.rmWWW(txt)
        qs, keywords = [], []
        for tt in self.tw.split(txt)[:256]:
            if not tt:
                continue
            keywords.append(tt)
            twts = self.tw.weights([tt])
            syns = self.syn.lookup(tt)
            if syns and len(keywords) < 32:
                keywords.extend(syns)
            logging.debug(json.dumps(twts, ensure_ascii=False))
            tms = []
            for tk, w in sorted(twts, key=lambda x: x[1] * -1):
                sm = rag_tokenizer.fine_grained_tokenize(tk).split() if need_fine_grained_tokenize(tk) else []
                sm = [re.sub(r"[ ,\./;'\[\]\\`~!@#$%\^&\*\(\)=\+_<>\?:\"\{\}\|，。；‘’【】、！￥……（）——《》？：“”-]+", "", m) for m in sm]
                sm = [self.sub_special_char(m) for m in sm if len(m) > 1]
                sm = [m for m in sm if len(m) > 1]
                if len(keywords) < 32:
                    keywords.append(re.sub(r"[ \\"']+", "", tk))
                    keywords.extend(sm)
                tk_syns = self.syn.lookup(tk)
                tk_syns = [self.sub_special_char(s) for s in tk_syns]
                if len(keywords) < 32:
                    keywords.extend([s for s in tk_syns if s])
                tk_syns = [rag_tokenizer.fine_grained_tokenize(s) for s in tk_syns if s]
                tk_syns = [f"\"{s}\"" if s.find(" ") > 0 else s for s in tk_syns]
                if len(keywords) >= 32:
                    break
                tk = self.sub_special_char(tk)
                if tk.find(" ") > 0:
                    tk = '"%s"' % tk
                if tk_syns:
                    tk = f"({tk} OR (%s)^0.2)" % " ".join(tk_syns)
                if sm:
                    tk = f'{tk} OR "%s" OR ("%s"~2)^0.5' % (" ".join(sm), " ".join(sm))
                if tk.strip():
                    tms.append((tk, w))
            tms = " ".join([f"({t})^{w}" for t, w in tms])
            if len(twts) > 1:
                tms += ' ("%s"~2)^1.5' % rag_tokenizer.tokenize(tt)
            syns = " OR ".join(['"%s"' % rag_tokenizer.tokenize(self.sub_special_char(s)) for s in syns])
            if syns and tms:
                tms = f"({tms})^5 OR ({syns})^0.7"
            qs.append(tms)
        if qs:
            query = " OR ".join([f"({t})" for t in qs if t])
            if not query:
                query = otxt
            return MatchTextExpr(self.query_fields, query, 100, {"minimum_should_match": min_match, "original_query": original_query}), keywords
        return None, keywords

    def hybrid_similarity(self, avec, bvecs, atks, btkss, tkweight=0.3, vtweight=0.7):
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np
        sims = cosine_similarity([avec], bvecs)
        tksim = self.token_similarity(atks, btkss)
        if np.sum(sims[0]) == 0:
            return np.array(tksim), tksim, sims[0]
        return np.array(sims[0]) * vtweight + np.array(tksim) * tkweight, tksim, sims[0]

    def token_similarity(self, atks, btkss):
        def to_dict(tks):
            if isinstance(tks, str):
                tks = tks.split()
            d = defaultdict(int)
            wts = self.tw.weights(tks, preprocess=False)
            for i, (t, c) in enumerate(wts):
                d[t] += c * 0.4
                if i+1 < len(wts):
                    _t, _c = wts[i+1]
                    d[t+_t] += max(c, _c) * 0.6
            return d
        atks = to_dict(atks)
        btkss = [to_dict(tks) for tks in btkss]
        sims = []
        for btks in btkss:
            sim, sumc = 0, 0
            for t, c in atks.items():
                sumc += c
                if t in btks:
                    sim += c * btks[t]
            sims.append(0 if sumc == 0 else sim / sumc)
        return sims
```

#### 3.5.2 分步实现讲解

**步骤 1：查询预处理**

- 中英文之间加空格（`add_space_between_eng_zh`）
- 去除 Infinity 特殊字符（`[ :|\r\n\t,，。？?/`!！&^%%()\[]{}<>\*\~'"\\]\`）
- 繁体转简体、全角转半角、转小写
- 去除停用词（`rmWWW`）

**步骤 2：英文查询处理**

- 分词（`rag_tokenizer.tokenize`）
- 计算词项权重（`term_weight.Dealer`）
- 同义词扩展（`synonym.Dealer`），同义词权重降为原词的 1/4
- 构建检索表达式：每个词项加权 + 相邻词项短语加权（`"word1 word2"^max(weight)*2`）
- 返回 `MatchTextExpr` 和关键词列表

**步骤 3：中文查询处理**

- 按语义切分（`term_weight.split`），最多 256 个片段
- 每个片段细粒度分词，计算权重
- 同义词扩展，构建 OR 表达式
- 相邻词项构建 proximity 查询（`"word1 word2"~2`）
- 返回带 `minimum_should_match` 的 `MatchTextExpr`

**步骤 4：混合相似度计算**

- `hybrid_similarity`：使用 `sklearn.metrics.pairwise.cosine_similarity` 计算向量相似度，结合词项共现相似度，加权融合
- `token_similarity`：基于词项共现和权重计算相似度，相邻词项给予更高权重（0.6 vs 0.4）

***

### 3.6 大模型对话基类：错误分类与重试机制

#### 3.6.1 完整功能实现代码

```python
class Base(ABC):
    def __init__(self, key, model_name, base_url, **kwargs):
        timeout = int(os.environ.get("LLM_TIMEOUT_SECONDS", 600))
        self.client = OpenAI(api_key=key, base_url=base_url, timeout=timeout)
        self.async_client = AsyncOpenAI(api_key=key, base_url=base_url, timeout=timeout)
        self.model_name = model_name
        self.max_retries = kwargs.get("max_retries", int(os.environ.get("LLM_MAX_RETRIES", 5)))
        self.base_delay = kwargs.get("retry_interval", float(os.environ.get("LLM_BASE_DELAY", 2.0)))
        self.max_rounds = kwargs.get("max_rounds", 5)
        self.is_tools = False
        self.tools = []
        self.toolcall_sessions = {}

    def _get_delay(self):
        return self.base_delay * random.uniform(10, 150)

    def _classify_error(self, error):
        error_str = str(error).lower()
        keywords_mapping = [
            (["quota", "capacity", "credit", "billing", "balance", "欠费"], LLMErrorCode.ERROR_QUOTA),
            (["rate limit", "429", "tpm limit", "too many requests", "requests per minute"], LLMErrorCode.ERROR_RATE_LIMIT),
            (["auth", "key", "apikey", "401", "forbidden", "permission"], LLMErrorCode.ERROR_AUTHENTICATION),
            (["invalid", "bad request", "400", "format", "malformed", "parameter"], LLMErrorCode.ERROR_INVALID_REQUEST),
            (["server", "503", "502", "504", "500", "unavailable"], LLMErrorCode.ERROR_SERVER),
            (["timeout", "timed out"], LLMErrorCode.ERROR_TIMEOUT),
            (["connect", "network", "unreachable", "dns"], LLMErrorCode.ERROR_CONNECTION),
            (["filter", "content", "policy", "blocked", "safety", "inappropriate"], LLMErrorCode.ERROR_CONTENT_FILTER),
            (["model", "not found", "does not exist", "not available"], LLMErrorCode.ERROR_MODEL),
            (["max rounds"], LLMErrorCode.ERROR_MODEL),
        ]
        for words, code in keywords_mapping:
            if re.search("({})".format("|".join(words)), error_str):
                return code
        return LLMErrorCode.ERROR_GENERIC

    @property
    def _retryable_errors(self) -> set[str]:
        return {LLMErrorCode.ERROR_RATE_LIMIT, LLMErrorCode.ERROR_SERVER}

    def _should_retry(self, error_code: str) -> bool:
        return error_code in self._retryable_errors

    def _exceptions(self, e, attempt) -> str | None:
        logging.exception("OpenAI chat_with_tools")
        error_code = self._classify_error(e)
        if attempt == self.max_retries:
            error_code = LLMErrorCode.ERROR_MAX_RETRIES
        if self._should_retry(error_code):
            delay = self._get_delay()
            logging.warning(f"Error: {error_code}. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{self.max_retries})")
            time.sleep(delay)
            return None
        msg = f"{ERROR_PREFIX}: {error_code} - {str(e)}"
        logging.error(f"sync base giving up: {msg}")
        return msg

    async def _exceptions_async(self, e, attempt):
        logging.exception("OpenAI async completion")
        error_code = self._classify_error(e)
        if attempt == self.max_retries:
            error_code = LLMErrorCode.ERROR_MAX_RETRIES
        if self._should_retry(error_code):
            delay = self._get_delay()
            logging.warning(f"Error: {error_code}. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{self.max_retries})")
            await asyncio.sleep(delay)
            return None
        msg = f"{ERROR_PREFIX}: {error_code} - {str(e)}"
        logging.error(f"async base giving up: {msg}")
        return msg

    async def async_chat_streamly(self, system, history, gen_conf: dict = {}, **kwargs):
        if system and history and history[0].get("role") != "system":
            history.insert(0, {"role": "system", "content": system})
        gen_conf = self._clean_conf(gen_conf)
        ans = ""
        total_tokens = 0
        for attempt in range(self.max_retries + 1):
            try:
                async for delta_ans, tol in self._async_chat_streamly(history, gen_conf, **kwargs):
                    ans = delta_ans
                    total_tokens += tol
                    yield ans
                yield total_tokens
                return
            except Exception as e:
                e = await self._exceptions_async(e, attempt)
                if e:
                    yield e
                    yield total_tokens
                    return
```

#### 3.6.2 分步实现讲解

**步骤 1：初始化与配置**

- 创建同步和异步 OpenAI 客户端，超时时间从环境变量 `LLM_TIMEOUT_SECONDS` 读取（默认 600 秒）
- 最大重试次数从 `LLM_MAX_RETRIES` 读取（默认 5 次）
- 基础延迟从 `LLM_BASE_DELAY` 读取（默认 2.0 秒）

**步骤 2：错误分类（`_classify_error`）**

- 将异常字符串通过关键词匹配映射到 10 种标准错误码：
  - `quota/capacity/credit/billing` → `ERROR_QUOTA`（配额不足）
  - `rate limit/429/tpm limit` → `ERROR_RATE_LIMIT`（限流）
  - `auth/key/apikey/401` → `ERROR_AUTHENTICATION`（认证失败）
  - `invalid/bad request/400` → `ERROR_INVALID_REQUEST`（请求无效）
  - `server/503/502/504/500` → `ERROR_SERVER`（服务器错误）
  - `timeout/timed out` → `ERROR_TIMEOUT`（超时）
  - `connect/network/unreachable` → `ERROR_CONNECTION`（连接错误）
  - `filter/content/policy/blocked` → `ERROR_CONTENT_FILTER`（内容过滤）
  - `model/not found` → `ERROR_MODEL`（模型错误）

**步骤 3：可重试错误判断（`_should_retry`）**

- 仅 `ERROR_RATE_LIMIT` 和 `ERROR_SERVER` 会触发重试
- 其他错误直接返回，避免无效重试浪费资源

**步骤 4：指数退避重试（`_exceptions_async`）**

- 延迟 = `base_delay * random.uniform(10, 150)`，即 20-300 秒之间的随机值
- 异步版本使用 `asyncio.sleep`，同步版本使用 `time.sleep`
- 达到最大重试次数后返回错误信息

**步骤 5：流式对话重试循环（`async_chat_streamly`）**

- 插入 system prompt 到 history 头部
- 清理生成配置（过滤不支持参数）
- 循环重试（最多 `max_retries + 1` 次）
- 流式输出 token，支持 reasoning content（`<think>` 标签包裹）
- 检测 `finish_reason == "length"`，追加截断提示
- 异常时调用 `_exceptions_async` 处理

***

### 3.7 大模型对话基类：工具调用机制

#### 3.7.1 完整功能实现代码

```python
    async def async_chat_with_tools(self, system: str, history: list, gen_conf: dict = {}):
        gen_conf = self._clean_conf(gen_conf)
        if system and history and history[0].get("role") != "system":
            history.insert(0, {"role": "system", "content": system})
        ans = ""
        tk_count = 0
        hist = deepcopy(history)
        for attempt in range(self.max_retries + 1):
            history = deepcopy(hist)
            try:
                for _ in range(self.max_rounds + 1):
                    logging.info(f"{self.tools=}")
                    response = await self.async_client.chat.completions.create(model=self.model_name, messages=history, tools=self.tools, tool_choice="auto", **gen_conf)
                    tk_count += total_token_count_from_response(response)
                    if any([not response.choices, not response.choices[0].message]):
                        raise Exception(f"500 response structure error. Response: {response}")
                    if not hasattr(response.choices[0].message, "tool_calls") or not response.choices[0].message.tool_calls:
                        _reasoning = getattr(response.choices[0].message, "reasoning_content", None) or getattr(response.choices[0].message, "reasoning", None)
                        if _reasoning:
                            ans += "<think>" + _reasoning + "</think>"
                        ans += response.choices[0].message.content
                        if response.choices[0].finish_reason == "length":
                            ans = self._length_stop(ans)
                        return ans, tk_count

                    async def _exec_tool(tc):
                        name = tc.function.name
                        try:
                            args = json_repair.loads(tc.function.arguments)
                            if hasattr(self.toolcall_session, "tool_call_async"):
                                result = await self.toolcall_session.tool_call_async(name, args)
                            else:
                                result = await thread_pool_exec(self.toolcall_session.tool_call, name, args)
                            return tc, name, args, result, None
                        except Exception as e:
                            logging.exception(f"Tool call failed: {tc}")
                            return tc, name, {}, None, e

                    logging.info(f"Response tool_calls={response.choices[0].message.tool_calls}")
                    results = await asyncio.gather(*[_exec_tool(tc) for tc in response.choices[0].message.tool_calls])
                    history = self._append_history_batch(history, results)
                    for tc, name, args, result, err in results:
                        ans += self._verbose_tool_use(name, args, err if err else result)

                logging.warning(f"Exceed max rounds: {self.max_rounds}")
                history.append({"role": "user", "content": f"Exceed max rounds: {self.max_rounds}"})
                response, token_count = await self._async_chat(history, gen_conf)
                ans += response
                tk_count += token_count
                return ans, tk_count
            except Exception as e:
                e = await self._exceptions_async(e, attempt)
                if e:
                    return e, tk_count
        assert False, "Shouldn't be here."

    def _append_history_batch(self, hist, results):
        hist.append({
            "role": "assistant",
            "tool_calls": [
                {
                    "index": tc.index,
                    "id": tc.id,
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    "type": "function",
                }
                for tc, _, _, _, _ in results
            ],
        })
        for tc, _, _, result, err in results:
            if err:
                content = str(err)
            elif isinstance(result, dict):
                content = json.dumps(result, ensure_ascii=False)
            else:
                content = str(result)
            hist.append({"role": "tool", "tool_call_id": tc.id, "content": content})
        return hist
```

#### 3.7.2 分步实现讲解

**步骤 1：初始化与 History 准备**

- 清理生成配置，插入 system prompt
- 深拷贝 history，避免修改原始数据

**步骤 2：工具调用循环（最多** **`max_rounds`** **轮）**

- 每轮调用 LLM，传入 `tools` 和 `tool_choice="auto"`
- 如果响应中没有 `tool_calls`，直接返回文本回答
- 如果有 `tool_calls`，并行执行所有工具（`asyncio.gather`）

**步骤 3：工具执行（`_exec_tool`）**

- 解析工具参数（`json_repair.loads`，容错解析）
- 优先调用异步工具方法（`tool_call_async`），否则用 `thread_pool_exec` 包装同步方法
- 返回工具执行结果或错误

**步骤 4：History 更新（`_append_history_batch`）**

- 按照 OpenAI 协议，添加一个 assistant 消息（包含所有 tool\_calls）
- 然后为每个工具调用添加一个 tool 消息（包含执行结果）
- 继续下一轮对话

**步骤 5：超限处理**

- 如果超过 `max_rounds`，向 history 添加提示信息，然后请求最终回答

***

### 3.8 嵌入模型统一封装

#### 3.8.1 完整功能实现代码

```python
class Base(ABC):
    def __init__(self, key, model_name, **kwargs):
        pass
    def encode(self, texts: list):
        raise NotImplementedError("Please implement encode method!")
    def encode_queries(self, text: str):
        raise NotImplementedError("Please implement encode method!")

class BuiltinEmbed(Base):
    _FACTORY_NAME = "Builtin"
    MAX_TOKENS = {"Qwen/Qwen3-Embedding-0.6B": 30000, "BAAI/bge-m3": 8000, "BAAI/bge-small-en-v1.5": 500}
    _model = None
    _model_name = ""
    _max_tokens = 500
    _model_lock = threading.Lock()

    def __init__(self, key, model_name, **kwargs):
        logging.info(f"Initialize BuiltinEmbed according to settings.EMBEDDING_CFG: {settings.EMBEDDING_CFG}")
        embedding_cfg = settings.EMBEDDING_CFG
        if not BuiltinEmbed._model and "tei-" in os.getenv("COMPOSE_PROFILES", ""):
            with BuiltinEmbed._model_lock:
                BuiltinEmbed._model_name = settings.EMBEDDING_MDL
                BuiltinEmbed._max_tokens = BuiltinEmbed.MAX_TOKENS.get(settings.EMBEDDING_MDL, 500)
                BuiltinEmbed._model = HuggingFaceEmbed(embedding_cfg["api_key"], settings.EMBEDDING_MDL, base_url=embedding_cfg["base_url"])
        self._model = BuiltinEmbed._model
        self._model_name = BuiltinEmbed._model_name
        self._max_tokens = BuiltinEmbed._max_tokens

    def encode(self, texts: list):
        batch_size = 16
        token_count = 0
        ress = None
        for i in range(0, len(texts), batch_size):
            embeddings, token_count_delta = self._model.encode(texts[i : i + batch_size])
            token_count += token_count_delta
            if ress is None:
                ress = embeddings
            else:
                ress = np.concatenate((ress, embeddings), axis=0)
        return ress, token_count

    def encode_queries(self, text: str):
        return self._model.encode_queries(text)

class OpenAIEmbed(Base):
    _FACTORY_NAME = "OpenAI"
    def __init__(self, key, model_name="text-embedding-ada-002", base_url="https://api.openai.com/v1"):
        if not base_url:
            base_url = "https://api.openai.com/v1"
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name

    def encode(self, texts: list):
        batch_size = 16
        texts = [truncate(t, 8191) for t in texts]
        ress = []
        total_tokens = 0
        for i in range(0, len(texts), batch_size):
            res = self.client.embeddings.create(input=texts[i : i + batch_size], model=self.model_name, encoding_format="float", extra_body={"drop_params": True})
            try:
                ress.extend([d.embedding for d in res.data])
                total_tokens += total_token_count_from_response(res)
            except Exception as _e:
                log_exception(_e, res)
                raise Exception(f"Error: {res}")
        return np.array(ress), total_tokens

    def encode_queries(self, text):
        res = self.client.embeddings.create(input=[truncate(text, 8191)], model=self.model_name, encoding_format="float", extra_body={"drop_params": True})
        try:
            return np.array(res.data[0].embedding), total_token_count_from_response(res)
        except Exception as _e:
            log_exception(_e, res)
            raise Exception(f"Error: {res}")
```

#### 3.8.2 分步实现讲解

**步骤 1：基类定义**

- `Base` 类定义嵌入模型接口：`encode(texts: list)` 批量编码，`encode_queries(text: str)` 单查询编码

**步骤 2：BuiltinEmbed（本地内置模型）**

- 使用单例模式（`_model` 类变量）避免重复加载
- 线程锁（`_model_lock`）保证并发安全
- 批量编码，batch\_size=16，自动截断到模型最大 token 数
- 支持 `Qwen/Qwen3-Embedding-0.6B`（30000 tokens）、`BAAI/bge-m3`（8000 tokens）、`BAAI/bge-small-en-v1.5`（500 tokens）

**步骤 3：OpenAIEmbed（OpenAI API）**

- batch\_size=16（OpenAI 限制）
- 自动截断到 8191 tokens
- 使用 `encoding_format="float"` 和 `extra_body={"drop_params": True}` 兼容不同模型

***

### 3.9 Rerank 模型统一封装

#### 3.9.1 完整功能实现代码

```python
class Base(ABC):
    def __init__(self, key, model_name, **kwargs):
        pass
    def similarity(self, query: str, texts: list):
        raise NotImplementedError("Please implement encode method!")
    @staticmethod
    def _normalize_rank(rank: np.ndarray) -> np.ndarray:
        min_rank = np.min(rank)
        max_rank = np.max(rank)
        if not np.isclose(min_rank, max_rank, atol=1e-3):
            rank = (rank - min_rank) / (max_rank - min_rank)
        else:
            rank = np.zeros_like(rank)
        return rank

class JinaRerank(Base):
    _FACTORY_NAME = "Jina"
    def __init__(self, key, model_name="jina-reranker-v2-base-multilingual", base_url="https://api.jina.ai/v1/rerank"):
        self.base_url = "https://api.jina.ai/v1/rerank"
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name

    def similarity(self, query: str, texts: list):
        texts = [truncate(t, 8196) for t in texts]
        data = {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts)}
        res = requests.post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as _e:
            log_exception(_e, res)
        return rank, total_token_count_from_response(res)

class XInferenceRerank(Base):
    _FACTORY_NAME = "Xinference"
    def __init__(self, key="x", model_name="", base_url=""):
        if base_url.find("/v1") == -1:
            base_url = urljoin(base_url, "/v1/rerank")
        if base_url.find("/rerank") == -1:
            base_url = urljoin(base_url, "/v1/rerank")
        self.model_name = model_name
        self.base_url = base_url
        self.headers = {"Content-Type": "application/json", "accept": "application/json"}
        if key and key != "x":
            self.headers["Authorization"] = f"Bearer {key}"

    def similarity(self, query: str, texts: list):
        if len(texts) == 0:
            return np.array([]), 0
        pairs = [(query, truncate(t, 4096)) for t in texts]
        token_count = 0
        for _, t in pairs:
            token_count += num_tokens_from_string(t)
        data = {"model": self.model_name, "query": query, "return_documents": "true", "return_len": "true", "documents": texts}
        res = requests.post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as _e:
            log_exception(_e, res)
        return rank, token_count

class LocalAIRerank(Base):
    _FACTORY_NAME = "LocalAI"
    def __init__(self, key, model_name, base_url):
        if base_url.find("/rerank") == -1:
            self.base_url = urljoin(base_url, "/rerank")
        else:
            self.base_url = base_url
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name.split("___")[0]

    def similarity(self, query: str, texts: list):
        texts = [truncate(t, 500) for t in texts]
        data = {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts)}
        token_count = 0
        for t in texts:
            token_count += num_tokens_from_string(t)
        res = requests.post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as _e:
            log_exception(_e, res)
        rank = Base._normalize_rank(rank)
        return rank, token_count
```

#### 3.9.2 分步实现讲解

**步骤 1：基类定义**

- `Base` 类定义 Rerank 模型接口：`similarity(query: str, texts: list)` 返回相关性分数数组
- `_normalize_rank` 方法将分数归一化到 0-1 范围，避免除零

**步骤 2：JinaRerank**

- base\_url 固定为 `https://api.jina.ai/v1/rerank`
- 自动截断文档到 8196 tokens
- 返回 `relevance_score` 填充到对应 index

**步骤 3：XInferenceRerank**

- base\_url 自动补全 `/v1/rerank`
- 支持空 key（本地服务）
- 返回 `relevance_score`

**步骤 4：LocalAIRerank**

- 固定截断到 500 tokens
- 返回分数后做归一化（`_normalize_rank`）

***

## 4. 模块完整执行流程总结

### 4.1 文档分块完整链路

```
用户上传文件
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 1 步：文件类型识别与路由                                   │
│  • 根据扩展名匹配解析器（DOCX/PDF/Excel/TXT/Markdown 等）     │
│  • PDF 根据 layout_recognize 配置选择具体解析器               │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 2 步：嵌入文件提取（根调用）                               │
│  • 提取 OLE 嵌入文件，递归调用 chunk 处理                     │
│  • 结果合并到主文档                                           │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 3 步：超链接追踪（可选）                                   │
│  • 提取文档中的 URL                                           │
│  • 下载网页内容，递归分块                                     │
│  • 失败时降级为纯 HTML 分块                                   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 4 步：格式特定解析                                         │
│  ├─ DOCX：段落 + 图片 + 表格 + 标题层级                       │
│  ├─ PDF：OCR → 版面 → 表格 → 合并（deepdoc 管道）            │
│  ├─ Markdown：文本 + 图片提取 + 视觉模型增强                  │
│  ├─ Excel：表格转文本/HTML                                    │
│  └─ TXT/代码：按分隔符切分                                    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 5 步：分块与重叠                                          │
│  • naive_merge：按分隔符切分，合并到目标 token 数             │
│  • naive_merge_with_images：分块时关联图片                   │
│  • overlapped_percent：相邻 chunk 重叠率控制                  │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 6 步：Tokenize 与输出                                     │
│  • tokenize_chunks：生成 chunk 元数据（token、位置、标签等）  │
│  • 返回结构化 chunk 列表，供后续索引                           │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 检索召回完整链路

```
用户提问
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 1 步：查询理解与转换                                       │
│  • FulltextQueryer.question：问题 → MatchTextExpr            │
│  • 中英文分词、同义词扩展、权重计算、停用词过滤                │
│  • 生成关键词列表                                             │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 2 步：向量编码                                             │
│  • EmbeddingModel.encode_queries：问题 → 向量                │
│  • 构建 MatchDenseExpr（cosine 相似度，topk，阈值）           │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 3 步：混合检索                                             │
│  • 全文检索（MatchTextExpr）+ 向量检索（MatchDenseExpr）      │
│  • FusionExpr 加权融合（默认 0.05:0.95）                      │
│  • 空结果兜底：降低 min_match 和相似度阈值重试                │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 4 步：重排序                                               │
│  • 有 Rerank 模型：rerank_by_model（模型 + 词项 + 标签）      │
│  • 无 Rerank 模型：rerank（内置混合相似度 + 标签）            │
│  • Infinity 引擎：跳过重排序（引擎内部已归一化）              │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 5 步：阈值过滤与分页                                       │
│  • similarity_threshold 过滤（vector_weight=0 时阈值=0）      │
│  • doc_ids 显式指定时绕过阈值                                 │
│  • 分页返回（支持跨页循环）                                   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 6 步：结果组装                                             │
│  • chunk 内容、相似度、向量、位置、高亮                       │
│  • 文档聚合统计（doc_aggs）                                    │
│  • 返回结构化结果                                             │
└─────────────────────────────────────────────────────────────┘
```

***

## 5. 工程设计亮点 & 核心机制解读

### 5.1 多解析器策略模式

`PARSERS` 字典实现了**策略模式**，根据配置动态选择 PDF 解析器（deepdoc/mineru/docling/tcadp/paddleocr/plaintext）。这种设计让系统可以灵活适配不同场景：本地 OCR 保护隐私、第三方服务解析质量高、腾讯云 API 适合中文文档、纯文本速度快。

### 5.2 混合检索与加权融合

RAGFlow 的检索是**三层融合**：全文检索（BM25）+ 向量检索（cosine）+ `FusionExpr` 加权融合（默认 0.05:0.95，向量占主导）。这种设计兼顾了精确性和语义性。当向量权重为 0 时，系统退化为纯全文检索，阈值自动设为 0。

### 5.3 空结果兜底策略

混合检索可能因阈值过高返回空结果。系统设计了**两级兜底**：首次检索用 `min_match=0.3` 和 `similarity=0.1`；空结果时自动降级到 `min_match=0.1` 和 `similarity=0.17`。这种自动降级保证了用户总能得到一些结果。

### 5.4 错误分类与智能重试

LLM 调用封装了完整的**错误分类和重试机制**：10 种标准错误码覆盖所有常见异常；仅对 `RATE_LIMIT` 和 `SERVER_ERROR` 触发重试（避免无效重试浪费资源）；指数退避：延迟 = `base_delay * random.uniform(10, 150)`，即 20-300 秒随机延迟；最大重试 5 次，超时 600 秒。

### 5.5 引用溯源机制

`insert_citations` 实现了**生成回答与检索结果的自动关联**：将回答按句子切分（支持中/英/阿拉伯文标点）；对每个句子编码向量，与 chunk 向量计算混合相似度；动态阈值（0.63 → 0.3，步长 0.8 倍），确保找到最佳匹配；在回答中插入 `[ID:N]` 标记，前端可渲染为可点击引用。

### 5.6 标签系统与特征加权

RAGFlow 实现了基于 TF-IDF 的**自动标签系统**：`tag_content` 为文档 chunk 自动提取标签，基于 chunk 内容与全局标签分布的 TF-IDF 分数；`tag_query` 为查询提取标签特征；`_rank_feature_scores` 将标签匹配分数和 PageRank 分数加权到重排序结果中。标签特征的计算公式本质上是**余弦相似度的变种**。

### 5.7 模型家族策略

`_apply_model_family_policies` 函数实现了**模型特定的参数适配**：`qwen3` 禁用思考模式；`gpt-5` 清空不兼容参数；`kimi-k2.5` 支持 reasoning 参数，启用 thinking 时强制 temperature=1.0；`HunYuan` 移除不支持的重复惩罚参数。这种设计让系统能适配各种模型的特殊要求。

### 5.8 流式工具调用

`async_chat_streamly_with_tools` 实现了**流式输出下的工具调用**：LLM 流式返回时，tool\_call 参数可能分多 chunk 到达；使用 `final_tool_calls` 字典按 index 聚合参数片段；流完成后统一解析参数、执行工具、追加结果；工具执行过程也流式输出（`Begin to call...` → 结果）。

### 5.9 多后端统一封装

RAGFlow 通过基类 + 子类的方式，统一封装了 20+ 种 LLM/Embedding/Rerank 后端。统一接口让上层业务代码无需关心具体后端，切换模型只需改配置。

### 5.10 递归分块与嵌套处理

`chunk` 函数的 `is_root` 参数实现了**递归分块**：根调用时提取嵌入文件（OLE 对象），递归处理；子调用时跳过嵌入文件提取，避免无限递归；结果自动合并到父文档。这种设计确保复杂文档（如带附件的邮件、嵌套表格的 DOCX）能被完整解析。

***

> **文档生成说明**：本文档基于 RAGFlow 项目 `rag` 模块的源代码进行工程级深度解析，覆盖 `app/naive.py`、`nlp/search.py`、`nlp/query.py`、`llm/chat_model.py`、`llm/embedding_model.py`、`llm/rerank_model.py` 的核心逻辑。每个核心功能都提供了完整代码块和分步实现讲解，适配 Kimi-K2.6 的长上下文推理特性。

