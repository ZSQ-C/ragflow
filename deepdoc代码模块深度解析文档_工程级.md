# 代码模块解析文档：RAGFlow deepdoc 文档解析引擎

## 1. 模块核心功能总览

`deepdoc` 是 RAGFlow 项目的**文档解析核心引擎**，承担将多格式文档（PDF、DOCX、Excel、HTML、Markdown、EPUB、PPT 等）转换为结构化文本块（chunks）的全链路职责。其设计目标是在工业场景下处理复杂版式、扫描件、乱码字体、多栏排版、嵌套表格等极端情况，输出可供 RAG 检索和生成使用的高质量结构化数据。

**核心能力拆解：**

| 能力维度 | 具体实现 |
|----------|----------|
| PDF 深度解析 | `pdfplumber` 文本提取 + 自定义 OCR 视觉管道双重保障，两层乱码检测自动识别提取失败页面 |
| 视觉 AI 管道 | 文本检测（DB 算法）→ 文本识别（CTC 算法）→ 版面分析（11 类分类）→ 表格结构识别（TSR） |
| 表格智能处理 | 自动旋转评估（0°/90°/180°/270°），旋转后重新 OCR，坐标逆变换回原始空间 |
| 版面理解重构 | KMeans 聚类检测分栏、XGBoost 模型判断段落拼接、版面类型标注与垃圾过滤 |
| 多格式统一 | 通过不同 Parser 类统一处理 DOCX、Excel、HTML、Markdown、EPUB、PPT、TXT、JSON |
| 工程化保障 | ONNX Runtime 推理、多 GPU 并行、模型缓存、显存限制、自动重试、CPU/GPU 自动降级 |

**模块文件规模：**
- `pdf_parser.py`：2057 行，PDF 解析核心，最复杂文件
- `ocr.py`：757 行，OCR 视觉管道（检测 + 识别 + 协调）
- `layout_recognizer.py`：456 行，版面识别（11 类分类 + YOLOv10 变体 + 昇腾适配）
- `table_structure_recognizer.py`：612 行，表格结构识别与 HTML/文本生成
- `recognizer.py`：441 行，所有视觉识别器的基类，提供通用工具方法

---

## 2. 关键类 / 核心方法 / 全局变量清单

### 2.1 关键类清单

| 类名 | 所属文件 | 核心职责 |
|------|----------|----------|
| `RAGFlowPdfParser` | `pdf_parser.py` | PDF 主解析器，协调 pdfplumber + OCR + 版面分析 + 表格识别的完整管道 |
| `PlainParser` | `pdf_parser.py` | 纯文本解析器，仅使用 pypdf 提取文本，无 OCR 和版面分析 |
| `VisionParser` | `pdf_parser.py` | 视觉大模型解析器，调用 GPT-4V 等模型对 PDF 每页进行图像描述 |
| `TextDetector` | `ocr.py` | 文本检测器，基于 DB（Differentiable Binarization）算法检测图像中的文本区域 |
| `TextRecognizer` | `ocr.py` | 文本识别器，基于 CTC（Connectionist Temporal Classification）算法识别文本内容 |
| `OCR` | `ocr.py` | OCR 协调器，整合检测 + 识别，提供端到端 OCR 接口，支持多 GPU |
| `Recognizer` | `recognizer.py` | 所有视觉识别器的基类，提供排序、重叠计算、预处理、后处理等通用工具 |
| `LayoutRecognizer` | `layout_recognizer.py` | 版面识别器，11 类版面分类（Text/Title/Figure/Table/Header/Footer 等） |
| `LayoutRecognizer4YOLOv10` | `layout_recognizer.py` | YOLOv10 版版面识别器，使用 YOLOv10 目标检测模型 |
| `AscendLayoutRecognizer` | `layout_recognizer.py` | 华为昇腾 NPU 版版面识别器 |
| `TableStructureRecognizer` | `table_structure_recognizer.py` | 表格结构识别器，检测行列、跨列跨行单元格，生成 HTML/文本 |

### 2.2 核心方法清单

| 方法名 | 所属类 | 核心作用 |
|--------|--------|----------|
| `__init__` | `RAGFlowPdfParser` | 初始化 OCR、版面识别器、表格识别器、XGBoost 拼接模型 |
| `__images__` | `RAGFlowPdfParser` | PDF→图片渲染、pdfplumber 字符提取、两层乱码检测、异步 OCR 调度 |
| `__ocr` | `RAGFlowPdfParser` | 单页 OCR 处理：检测 → 排序 → 合并 pdfplumber 字符 → 乱码回退 → 识别 |
| `_is_garbled_text` | `RAGFlowPdfParser` | **第一层检测**：PUA 私有区字符 + CID 占位符检测 |
| `_is_garbled_by_font_encoding` | `RAGFlowPdfParser` | **第二层检测**：子集字体编码损坏检测（CJK 映射到 ASCII） |
| `_evaluate_table_orientation` | `RAGFlowPdfParser` | 表格自动旋转评估，4 角度 OCR 评分选最优 |
| `_table_transformer_job` | `RAGFlowPdfParser` | 表格结构识别主流程：裁剪 → 旋转评估 → TSR → 坐标映射 |
| `_ocr_rotated_tables` | `RAGFlowPdfParser` | 对旋转后的表格重新 OCR，坐标逆变换回原始空间 |
| `_layouts_rec` | `RAGFlowPdfParser` | 版面识别：调用 LayoutRecognizer 标注每个文本框的版面类型 |
| `_assign_column` | `RAGFlowPdfParser` | KMeans 聚类检测页面分栏数，为每个文本框分配列 ID |
| `_text_merge` | `RAGFlowPdfParser` | 横向合并同一行内相邻的文本框 |
| `_naive_vertical_merge` | `RAGFlowPdfParser` | 纵向合并：基于标点启发式规则的简单拼接 |
| `_concat_downward` | `RAGFlowPdfParser` | 纵向拼接：XGBoost 模型判断上下文本框是否属于同一段落 |
| `_updown_concat_features` | `RAGFlowPdfParser` | 提取 32 维特征向量供 XGBoost 拼接判断 |
| `_extract_table_figure` | `RAGFlowPdfParser` | 提取表格和图表区域，匹配标题，生成 HTML/文本输出 |
| `_filter_forpages` | `RAGFlowPdfParser` | 过滤目录页、致谢页等干扰内容 |
| `__filterout_scraps` | `RAGFlowPdfParser` | 过滤页眉页脚等碎片文本，保留有效正文 |
| `__call__` | `RAGFlowPdfParser` | 主入口：编排整个 PDF 解析流程 |
| `parse_into_bboxes` | `RAGFlowPdfParser` | 带进度回调的解析入口，返回结构化 bbox 列表 |
| `load_model` | 全局函数（`ocr.py`） | ONNX 模型加载：CUDA/CPU 自动选择、显存限制、模型缓存 |
| `__call__` | `TextDetector` | DB 检测主流程：预处理 → ONNX 推理 → 后处理 → 过滤小框 |
| `__call__` | `TextRecognizer` | CTC 识别主流程：批量归一化 → ONNX 推理 → CTC 解码 |
| `__call__` | `OCR` | 端到端 OCR：检测 → 排序 → 裁剪 → 识别 → 过滤低置信度 |
| `get_rotate_crop_image` | `OCR` | 透视变换裁剪 + 自动旋转校正（处理竖排文字） |
| `__call__` | `LayoutRecognizer` | 版面识别主流程：检测 → 过滤垃圾版面 → 匹配 OCR 框 → 标注类型 |
| `__call__` | `TableStructureRecognizer` | TSR 主流程：ONNX 推理 → 行列对齐 → 修正坐标 |
| `construct_table` | `TableStructureRecognizer` | 从 OCR 框构建表格结构：排序 → 分行列 → 处理跨行跨列 → HTML/文本输出 |

### 2.3 核心全局变量与环境变量

| 变量/配置 | 位置 | 用途 |
|-----------|------|------|
| `loaded_models` | `ocr.py` 全局 | 模型缓存字典，键为 `model_file_path + device_id`，避免重复加载 ONNX 模型 |
| `LOCK_KEY_pdfplumber` | `pdf_parser.py` 全局 | pdfplumber 全局锁键名，通过 `sys.modules` 实现跨线程锁，防止并发访问 PDF |
| `OCR_GPU_MEM_LIMIT_MB` | 环境变量 | GPU 显存限制，默认 2048MB |
| `OCR_ARENA_EXTEND_STRATEGY` | 环境变量 | GPU 内存分配策略，默认 `kNextPowerOfTwo` |
| `OCR_GPUMEM_ARENA_SHRINKAGE` | 环境变量 | 是否启用 GPU 内存竞技场收缩，设为 `1` 开启 |
| `OCR_INTRA_OP_NUM_THREADS` | 环境变量 | ONNX Runtime 算子内线程数，默认 2 |
| `OCR_INTER_OP_NUM_THREADS` | 环境变量 | ONNX Runtime 算子间线程数，默认 2 |
| `PARALLEL_DEVICES` | `settings` 配置 | 并行设备数，大于 1 时启用多 GPU 并发 |
| `LAYOUT_RECOGNIZER_TYPE` | 环境变量 | 版面识别器类型，`onnx` 或 `ascend`，默认 `onnx` |
| `TABLE_STRUCTURE_RECOGNIZER_TYPE` | 环境变量 | 表格结构识别器类型，`onnx` 或 `ascend`，默认 `onnx` |
| `TABLE_AUTO_ROTATE` | 环境变量 | 表格自动旋转开关，默认 `true` |
| `TENSORRT_DLA_SVR` | 环境变量 | TensorRT DLA 服务地址，启用远程 DLA 推理 |

---

## 3. 代码分模块逻辑解析

### 3.1 全局模型缓存与工具函数（`ocr.py` 第 36-68 行）

`deepdoc` 在 `ocr.py` 模块顶部维护了一个全局字典 `loaded_models = {}`，这是整个视觉管道的**模型缓存中心**。其设计思路是：ONNX 模型加载涉及磁盘 I/O 和 GPU 显存分配，耗时较长，同一模型在同一设备上只需加载一次，后续全部复用缓存的会话对象。

缓存键的生成逻辑为 `model_file_path + str(device_id)`，这意味着同一模型在不同 GPU 设备上会被分别缓存。这种设计支持多 GPU 场景下每个设备独立持有自己的会话实例，避免跨设备共享导致的并发冲突。

`transform` 函数是一个通用的数据流水线执行器，接收数据和操作列表，按顺序执行每个操作，如果任一操作返回 `None` 则提前终止。`create_operators` 函数则根据配置列表动态实例化图像处理操作类，通过 `getattr` 从 `operators` 模块中查找对应类名，实现配置驱动的预处理流水线。这两个函数共同支撑了 DB 检测器的预处理阶段。

### 3.2 ONNX 会话与运行参数配置（`ocr.py` 第 71-136 行）

`load_model` 是整个视觉管道最重要的基础设施函数，负责 ONNX 模型的加载、设备适配、显存管理和缓存。其执行流程分为四个阶段：

**阶段一：缓存检查**。先拼接模型文件路径（`model_dir + nm + ".onnx"`），再用 `device_id` 生成缓存标签。如果缓存命中，直接返回已加载的会话和运行选项，避免重复初始化。

**阶段二：CUDA 可用性检测**。内部嵌套函数 `cuda_is_available` 先调用 `pip_install_torch` 确保 torch 已安装，然后检查 `torch.cuda.is_available()` 和 `torch.cuda.device_count() > target_id`。这里的设计亮点是**惰性安装**：torch 不是项目强制依赖，只有在需要 GPU 时才尝试安装，降低了环境部署门槛。

**阶段三：会话选项配置**。创建 `ort.SessionOptions` 并设置三个关键参数：`enable_cpu_mem_arena = False` 关闭 CPU 内存竞技场，减少内存碎片；`execution_mode = ORT_SEQUENTIAL` 启用顺序执行模式，降低多线程竞争；`intra_op_num_threads` 和 `inter_op_num_threads` 分别从环境变量读取，默认值为 2，控制算子内和算子间的线程数，防止在多 worker 环境中过度订阅 CPU 资源。

**阶段四：GPU/CPU 会话创建与缓存**。如果 CUDA 可用，创建 `CUDAExecutionProvider` 会话，配置 `device_id`、`gpu_mem_limit`（默认 2GB）和 `arena_extend_strategy`（默认按 2 的幂次扩展）。如果环境变量 `OCR_GPUMEM_ARENA_SHRINKAGE` 设为 `1`，则在运行选项中添加 `memory.enable_memory_arena_shrinkage` 配置，**每次推理后主动收缩 GPU 内存竞技场，将显存释放回系统**，这是防止长时间运行服务出现显存泄漏的关键设计。如果 CUDA 不可用，退回到 `CPUExecutionProvider`，并启用 CPU 内存收缩。最终把 `(sess, run_options)` 元组存入全局缓存并返回。

### 3.3 文本识别器 TextRecognizer（`ocr.py` 第 139-414 行）

`TextRecognizer` 是基于 CTC 算法的文本识别器，负责将裁剪后的文本图像块转换为字符串。其初始化时配置输入图像形状为 `[3, 48, 320]`（3 通道、高度 48、宽度 320），批量识别数为 16。后处理参数指定使用 `CTCLabelDecode`，字符字典路径为 `ocr.res`，支持空格字符。

核心方法 `resize_norm_img` 完成图像预处理流水线：OpenCV 缩放到目标尺寸 → 转 `float32` → 维度从 HWC 转 CHW → 除以 255 归一化到 0-1 → 减 0.5 再除以 0.5 映射到 -1 到 1 → 零填充到固定宽度。这个标准化流程是 PaddleOCR 的标准预处理，确保模型输入分布一致。

`__call__` 方法是批量识别入口，其设计亮点是**按宽高比排序后分批处理**。先计算每个图像的宽高比，按 `np.argsort` 排序，让相似宽高比的图像进入同一批次。这样可以最大化批量推理效率，减少因尺寸差异导致的填充浪费。每批最多 16 张，计算该批最大宽高比统一调整宽度，然后拼接成 batch 数组送入 ONNX 推理。

推理阶段有一个**自动重试机制**：`for i in range(100000)` 循环包裹推理调用，如果失败（如 GPU OOM）则最多重试 3 次，每次等待 5 秒。这是一个工业级的容错设计，应对 transient 的 GPU 资源紧张。推理完成后用 `CTCLabelDecode` 把概率矩阵解码为文本，按原始顺序回填结果。

### 3.4 文本检测器 TextDetector（`ocr.py` 第 420-539 行）

`TextDetector` 基于 DB（Differentiable Binarization）算法检测图像中的文本区域。初始化时配置预处理流水线：限制最长边为 960 像素的缩放、标准化（减均值除标准差）、HWC→CHW 转换、保留 image 和 shape 信息。后处理参数包括概率阈值 0.3、框阈值 0.5、最大候选框 1000、展开比例 1.5（把收缩的文本框展开回原大小）。

`order_points_clockwise` 方法实现四边形点的顺时针排序：坐标和最小的是左上，最大的是右下；剩余两点中差值最小的是右上，最大的是左下。这个排序确保后续的透视变换和裁剪正确。

`filter_tag_det_res` 方法过滤检测结果：先顺时针排序，再裁剪到图像边界内，计算宽和高（欧几里得距离），如果宽或高小于等于 3 像素则丢弃。这个过滤有效去除了噪声检测框。

`__call__` 主流程执行：复制原图 → 预处理（缩放、归一化）→ ONNX 推理（同样带 3 次重试）→ DB 后处理（概率图转四边形坐标）→ 过滤小框 → 返回检测框和耗时。

### 3.5 OCR 协调器与多 GPU 支持（`ocr.py` 第 542-757 行）

`OCR` 类是检测器和识别器的协调层，提供端到端的 OCR 接口。其初始化时根据 `settings.PARALLEL_DEVICES` 决定设备数量：如果大于 0，为每个设备创建独立的 `TextDetector` 和 `TextRecognizer` 实例；否则只创建一组。这意味着多 GPU 场景下每个 GPU 持有独立的模型会话，通过 `device_id` 索引访问。

`get_rotate_crop_image` 是 OCR 管道中非常关键的方法，做两件事：一是根据检测到的 4 个点计算透视变换矩阵（`cv2.getPerspectiveTransform`），把倾斜的文本区域拉正；二是判断文本是否为竖排（高度/宽度 ≥ 1.5），如果是则尝试顺时针和逆时针 90° 旋转，分别识别后选置信度最高的方向。这个设计自动处理了竖排中文、旋转文本等复杂情况。

`__call__` 端到端入口的执行流程：检测文本框 → 按阅读顺序排序（从上到下、从左到右）→ 对每个框做透视变换裁剪 → 批量识别 → 过滤置信度低于 0.5 的结果 → 返回 `[([4 个点坐标], (文本, 置信度)), ...]`。

### 3.6 两层乱码检测策略（`pdf_parser.py` 第 200-320 行）

这是 `deepdoc` **最核心的创新设计**之一。PDF 解析的最大痛点是字体编码问题，很多中文 PDF（扫描件、老标准、嵌入式子集字体）用 pdfplumber 提取出来全是乱码。系统设计了**两层互补的检测策略**，覆盖不同类型的编码损坏：

**第一层：PUA/CID 字符级检测（`_is_garbled_text`）**。检测逻辑包括：① 正则匹配 `(cid:123)` 占位符，这是 pdfminer 提取失败时的标志；② 统计非空格字符中乱码的比例，超过阈值（默认 50%，页面级采样用 30%）则判定乱码。单个字符的乱码判定通过 `_is_garbled_char` 完成，检查 Unicode 私有使用区（PUA，0xE000-0xF8FF 等）、替换字符 �（0xFFFD）、控制字符、C0 控制字符（0x80-0x9F）、未分配字符（Cn）和代理项（Cs）。

**第二层：字体编码损坏检测（`_is_garbled_by_font_encoding`）**。有些 PDF 把中文字形映射到了 ASCII 码点，提取出来全是乱码的标点符号，第一层检测不出来。这一层的检测逻辑：① 至少 20 个字符才检测；② 超过 30% 的字符来自子集字体（字体名有 `ABCD+` 前缀）；③ CJK 字符比例低于 5%；④ ASCII 标点符号比例高于 40%。同时满足则判定为编码损坏。

**兜底策略**：检测到乱码后，清空该页字符数组，后续强制走纯 OCR 路径，保证输出质量不受损坏字体影响。

### 3.7 PDF 主解析流程 `__images__`（`pdf_parser.py` 第 1529-1694 行）

`__images__` 是 PDF 解析的核心入口方法，承担 PDF→图片渲染、字符提取、乱码检测、异步 OCR 调度的完整职责。

**PDF 渲染与字符提取**：使用 `pdfplumber.open` 打开 PDF，`p.to_image(resolution=72 * zoomin, antialias=True).annotated` 把每页转成高清图片（zoomin=3 时为 216 DPI）。同时调用 `page.dedupe_chars().chars` 提取字符信息，`_has_color` 过滤掉纯白色/透明的隐藏字符（有些 PDF 用白色字符做排版对齐，需要剔除）。

**两层乱码检测**：对每页前 200 个字符采样，先执行第一层 PUA/CID 检测（阈值 30%），再执行第二层字体编码损坏检测。任一检测命中则清空该页字符，后续走纯 OCR。

**异步 OCR 调度**：内部定义 `__img_ocr` 异步函数处理单页，先对 pdfplumber 字符做空格补全（英文单词间距大时补空格），然后用 `thread_pool_exec` 在线程池中执行 `__ocr`。如果配置了多设备并行（`PARALLEL_DEVICES > 1`），为每页创建 `asyncio.Task`，通过 `asyncio.Semaphore(1)` 控制每个设备的并发数为 1，避免同一 GPU 上多个推理任务竞争显存。所有任务通过 `asyncio.gather` 等待完成，异常时取消所有任务并重新抛出。

**英文检测**：解析完成后，随机采样字符判断是否为英文文档，影响后续的空格处理策略。

**递归兜底**：如果所有页面都没有识别出文本框且 zoomin < 9，自动提高 3 倍分辨率重新解析，应对极小字体的极端情况。

### 3.8 单页 OCR 处理 `__ocr`（`pdf_parser.py` 第 707-796 行）

`__ocr` 是单页 OCR 的核心方法，协调检测、字符合并、乱码回退、识别四个阶段。

**阶段一：文本检测**。调用 `self.ocr.detect` 用 DB 模型检测图像中的所有文本区域，返回四边形框坐标。

**阶段二：检测框排序与字符合并**。把检测框按 Y 坐标排序（先上下后左右）。对每个 pdfplumber 提取的字符，用 `Recognizer.find_overlapped`（二分查找 + 线性扫描）找到它落在哪个检测框里。高度检查：如果字符高度和检测框高度差异超过 70%，认为是匹配错误（小标点匹配到大标题框），丢弃。把匹配成功的字符按阅读顺序拼接成文本，空格处理有精细逻辑：如果字符是空格且前面是英文/数字/标点，才补空格，避免中文被错误加空格。

**阶段三：乱码回退**。对拼接后的文本执行两层乱码检测：如果 pdfplumber 字符中超过 50% 是乱码，或检测到字体编码损坏，清空该框文本。清空的框在下一阶段会用 OCR 重新识别。

**阶段四：OCR 识别兜底**。对文本为空的框，从原图裁剪出对应区域（`get_rotate_crop_image` 做透视变换），批量送入 `TextRecognizer` 识别，结果填回对应框。最后过滤掉仍无文本的框，计算中位高度作为该页面的平均行高。

### 3.9 表格自动旋转与结构识别（`pdf_parser.py` 第 322-705 行）

表格在 PDF 中可能以任意角度出现（特别是扫描件），系统设计了完整的自动旋转校正流程。

**旋转评估（`_evaluate_table_orientation`）**：测试 0°、90°、180°、270° 四个方向。对每个角度旋转图像（PIL 的 rotate 是逆时针，用负角度实现顺时针，`expand=True` 防止裁剪），执行 OCR 计算综合分数 = 平均置信度 × (1 + 0.1 × 区域数/50)。**保守策略**：只有当非 0° 的分数比 0° 高 0.2 以上，且 0° 分数低于 0.8 时，才采用旋转，防止轻微优势导致错误旋转。

**表格结构识别（`_table_transformer_job`）**：遍历版面类型为 "table" 的区域，裁剪出表格图像（带 10 像素边距），评估旋转角度后调用 `TableStructureRecognizer` 识别行列结构。如果启用了自动旋转，对旋转后的表格重新 OCR（`_ocr_rotated_tables`），把结果映射回原始坐标系。

**坐标逆变换（`_map_rotated_point`）**：把旋转后图像上的点映射回原始图像坐标。例如顺时针 90° 旋转后，原始 (x,y) 变成了 (y, W-x)，逆变换就是 (W-y, x)。这个数学变换保证了旋转后的 OCR 结果能正确放回原始版面中。

### 3.10 版面识别与垃圾过滤（`layout_recognizer.py` 第 33-157 行）

`LayoutRecognizer` 把页面分成 11 个类别：`_background_`、Text、Title、Figure、Figure caption、Table、Table caption、Header、Footer、Reference、Equation。

`__call__` 主流程：先调用父类 `Recognizer` 的检测方法获取版面区域，然后对每个版面类型执行 `findLayout` 匹配。匹配逻辑是：对每个未标注的 OCR 文本框，找到和它重叠度最高的对应类型版面区域，把该区域的类型赋给文本框。

**垃圾版面过滤**：如果文本框匹配到了页眉/页脚/参考文献（`garbage_layouts`），且位置符合启发式规则（页眉在顶部 10% 内，页脚在底部 10% 内），则认为是垃圾内容，从结果中删除。被删除的文本按类型统计，出现超过一次的加入全局垃圾集合，最后统一过滤。这个设计有效去除了页眉页脚对正文的干扰。

**未访问版面处理**：对于 figure 和 equation 类型的版面区域，如果没有 OCR 文本框匹配到（可能是纯图片），创建一个空文本框插入，确保版面完整性。

### 3.11 分栏检测与列分配（`pdf_parser.py` 第 806-888 行）

`_assign_column` 用 KMeans 聚类自动检测页面分栏数，无需人工配置。

**单页聚类**：对每页的文本框 x0 坐标聚类，尝试 1 到 4 类（上限为文本框数量）。用轮廓系数（silhouette score）评估聚类质量，系数越接近 1 说明聚类效果越好，选最优的 k 作为该页列数。

**缩进容忍**：把靠近左边缘（在页面宽度 12% 内）的 x0 统一设为最小值，避免缩进段落被误判为新列。

**全局统一**：用多数表决决定全局列数（所有页面中出现次数最多的列数），保证跨页一致性。然后按全局列数重新聚类，为每个文本框分配 `col_id`。

### 3.12 文本合并与智能拼接（`pdf_parser.py` 第 890-1134 行）

**横向合并（`_text_merge`）**：检查同一页、同一列、同一版面区域内的相邻文本框，如果 Y 坐标接近（小于平均行高的 1/3），认为是同一行，合并成一个框。这处理了 pdfplumber 把一个单词拆成多个字符框的情况。

**纵向合并 - 简单版（`_naive_vertical_merge`）**：基于启发式规则判断上下两个框是否应该拼接。条件包括：间距不超过 1.5 倍行高、水平重叠度超过 30%、不以句号等断句标点结尾、不在不同版面区域。这个规则覆盖了大量常见的段落连续情况。

**纵向拼接 - 智能版（`_concat_downward`）**：用 XGBoost 模型判断上下两个文本框是否属于同一段落。先计算每个框的 "同行邻居数"（in_row）作为特征，然后对每对候选框提取 32 维特征，包括：是否同一表格行、Y 距离归一化、页码差、版面类型、是否以标点结尾、是否以标点开头、是否项目符号、大小写特征、词数差异、文本长度差异等。模型输出概率，大于 0.5 则拼接。这个机器学习方案比固定规则更鲁棒，能处理复杂的版面变化。

### 3.13 表格结构识别与 HTML 生成（`table_structure_recognizer.py` 第 30-575 行）

`TableStructureRecognizer` 检测 6 类元素：table（整体）、table column（列）、table row（行）、table column header（列标题）、table projected row header（投影行标题）、table spanning cell（跨行列单元格）。

`__call__` 主流程：对每张表格图像做 ONNX 推理，然后执行行列对齐：行的左右边界取均值（或最小/最大值，根据数量决定），列的上下边界取中位数。这个对齐修正了模型输出的微小偏差，确保行列边界一致。

`construct_table` 从 OCR 文本框构建表格结构：① 提取标题（匹配 "图表 + 数字" 模式）；② 判断每个单元格内容类型（日期 Dt、数字 Nu、英文 En、中文 Tx 等）；③ 按行排序分组（R 标签相同的为一行）；④ 按列排序分组（C 标签相同的为一列）；⑤ 处理孤行列（只有单个值的行列，根据邻居距离决定合并方向）；⑥ 处理跨行跨列（`__cal_spans` 计算 rowspan 和 colspan）；⑦ 生成 HTML 或描述性文本输出。

`__html_table` 生成标准 HTML 表格，支持 caption、th/td 区分、rowspan/colspan 属性。`__desc_table` 生成键值对形式的文本，把表头作为键、内容作为值，更适合 RAG 检索。

### 3.14 识别器基类工具方法（`recognizer.py` 第 31-441 行）

`Recognizer` 是所有视觉识别器的基类，提供通用工具方法。

**排序工具**：`sort_Y_firstly` 先按 Y（上下）排序，Y 差距小于阈值再按 X（左右）排序；`sort_X_firstly` 相反；`sort_C_firstly` 和 `sort_R_firstly` 在 X/Y 排序基础上再按列号/行号微调。这些排序确保阅读顺序正确。

**重叠计算**：`overlapped_area` 计算两个框的重叠面积，支持返回面积比（IoU 变种）。`find_overlapped` 用二分查找定位可能重叠的区域，再线性扫描找最大重叠，比暴力扫描高效得多。`find_overlapped_with_threshold` 带阈值的最优匹配，要求双向重叠都超过阈值才匹配。

**版面清理**：`layouts_cleanup` 清理重叠的版面区域，检查相邻框，如果重叠度超过阈值，保留和 OCR 文本框重叠面积更大的那个。这个 NMS-like 的去重确保版面区域不重叠。

**预处理与后处理**：`preprocess` 支持两种模式：一种是配置驱动的流水线（LinearResize → StandardizeImage → Permute → PadStride），另一种是简单的固定尺寸缩放。`postprocess` 支持两种输出格式：一种是 `[class, score, x1, y1, x2, y2]` 的直接解析，另一种是 YOLO 格式的 `[x, y, w, h]` 转 `[x1, y1, x2, y2]` + NMS。

---

## 4. 模块完整执行流程总结

### 4.1 PDF 解析完整调用链路

```
用户上传 PDF
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 1 步：PDF → 图片渲染（pdfplumber）                        │
│  • resolution=72×zoomin (默认 216 DPI)                       │
│  • 同时提取每页字符信息（位置、字体、颜色）                      │
│  • 全局锁防止并发访问 PDF（sys.modules 锁）                   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 2 步：两层乱码检测                                         │
│  ├─ 第一层：PUA/CID 检测（采样 200 字符，阈值 30%）            │
│  │   • 检测 Unicode 私有区、CID 占位符、未分配字符             │
│  └─ 第二层：字体编码损坏检测（子集字体 + CJK<5% + 标点>40%）    │
│   • 检测字形映射到 ASCII 码点的损坏情况                        │
│  → 乱码页面：清空 chars，后续走纯 OCR                         │
│  → 正常页面：保留 chars，后续做 OCR+pdfplumber 合并            │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 3 步：异步 OCR（所有页面）                                 │
│  • TextDetector：DB 算法检测文本区域                           │
│  • 合并 pdfplumber 字符到检测框（二分查找 + 高度检查）          │
│  • 乱码回退：清空文本 → OCR 重新识别                           │
│  • TextRecognizer：CTC 算法识别文本内容                        │
│  • 多 GPU 并行（asyncio + Semaphore，每设备并发=1）            │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 4 步：版面识别（LayoutRecognizer）                        │
│  • 11 类版面分类：Text/Title/Figure/Table/Header/Footer 等    │
│  • 匹配 OCR 框到版面区域，标注 layout_type                     │
│  • 过滤垃圾版面（页眉页脚参考文献）+ 统计去重                   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 5 步：表格结构识别（TableStructureRecognizer）              │
│  • 裁剪表格区域 → 评估旋转角度（0°/90°/180°/270°）             │
│  • 保守策略：非 0° 必须显著优于 0° 才采用                      │
│  • TSR 模型检测行列结构 → 行列对齐修正                         │
│  • 旋转表格重新 OCR，坐标逆变换回原始空间                      │
│  • 匹配 OCR 框到行列，生成 HTML/文本                          │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 6 步：文本合并与拼接                                       │
│  • _assign_column：KMeans 聚类检测分栏数（1-4 类）            │
│  • _text_merge：横向合并同行相邻框                             │
│  • _naive_vertical_merge：纵向合并（启发式规则）               │
│  • _concat_downward：XGBoost 智能段落拼接（32 维特征）         │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  第 7 步：后处理与输出                                         │
│  • _filter_forpages：过滤目录页、致谢页                        │
│  • _extract_table_figure：提取表格和图表，匹配标题             │
│  • __filterout_scraps：DFS 过滤碎片文本，保留有效正文          │
│  • 输出：结构化文本块 + 表格 HTML + 图片 + 位置标签            │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 OCR 视觉管道调用链路

```
输入图像
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  TextDetector（DB 算法）                                      │
│  • 预处理：缩放（限制最长边 960）、归一化、维度转换            │
│  • ONNX 推理：输出概率图（每个像素是文本的概率）               │
│  • DBPostProcess：概率图 → 文本框四边形坐标                   │
│  • 四边形排序（顺时针）+ 裁剪到边界 + 过滤小框（≤3px）         │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  文本框排序与裁剪                                              │
│  • sorted_boxes：从上到下、从左到右排序                       │
│  • get_rotate_crop_image：透视变换拉正 + 竖排检测旋转          │
│    • 计算透视变换矩阵 → warpPerspective → 判断高宽比           │
│    • 若高宽比 ≥ 1.5，尝试原图/顺时针 90°/逆时针 90°，选最优    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  TextRecognizer（CTC 算法）                                   │
│  • 按宽高比排序 → 分批（每批 16）→ 统一宽度 → resize_norm_img │
│  • 批量 ONNX 推理（带 3 次重试）→ CTC 解码                    │
│  • 输出：[文本, 置信度]，过滤 < 0.5 的低置信度结果             │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. 工程设计亮点 & 核心机制解读

### 5.1 全局模型缓存设计

`loaded_models` 全局字典缓存 ONNX 会话对象，键为 `model_file_path + device_id`。这种设计的工程价值：① 避免重复加载模型的磁盘 I/O 和 GPU 显存分配开销；② 支持多 GPU 场景下每个设备独立缓存，避免跨设备共享导致的并发冲突；③ 进程生命周期内持续有效，适合长运行的文档解析服务。缓存粒度到设备级别，是因为不同 GPU 上的会话对象不能共享。

### 5.2 CPU/GPU 自动适配与降级

系统实现了完整的自动适配链路：`cuda_is_available` 惰性检测 GPU 可用性 → 优先创建 `CUDAExecutionProvider` → 失败或不可用时自动降级到 `CPUExecutionProvider`。这种设计降低了部署门槛，同一套代码可以在无 GPU 的服务器上直接运行。同时通过环境变量 `OCR_GPU_MEM_LIMIT_MB` 限制显存使用，防止大模型或多任务场景下 OOM。

### 5.3 显存限制与内存竞技场收缩

GPU 显存管理是生产环境的关键问题。系统设计了三层保障：① `gpu_mem_limit` 硬限制单模型显存使用（默认 2GB）；② `arena_extend_strategy` 控制内存分配策略（按 2 的幂次扩展，减少碎片）；③ `OCR_GPUMEM_ARENA_SHRINKAGE` 环境变量启用显式收缩，每次推理后把未使用的显存块释放回系统。第三层尤其重要，因为 ONNX Runtime 的默认行为是持有已分配的显存不释放，长时间运行会导致显存持续增长。

### 5.4 自动重试与容错机制

TextDetector 和 TextRecognizer 的推理都被包裹在 `for i in range(100000)` 循环中，失败时最多重试 3 次，每次等待 5 秒。这种设计应对的是 transient 故障：GPU 瞬时资源紧张、驱动超时、其他进程抢占显存等。重试间隔 5 秒给系统恢复留出时间，3 次上限防止无限重试阻塞。这是一个工业级的容错模式。

### 5.5 两层乱码检测策略

这是 `deepdoc` 最核心的技术创新。第一层（字符级）快速覆盖 PUA、CID 等常见损坏；第二层（字体级）专门捕获子集字体编码映射错误这种隐蔽问题。两层互补，覆盖了工业场景下绝大多数 PDF 乱码情况。检测到乱码后不清除整个页面，而是清空字符数组让后续 OCR 兜底，这种**优雅降级**保证了输出质量。

### 5.6 表格自动旋转的保守策略

表格旋转评估不是简单地选最高分，而是设置了保守阈值：非 0° 必须比 0° 高 0.2 以上，且 0° 分数低于 0.8。这个设计防止了轻微优势导致的错误旋转，因为 0° 是大多数情况下的正确方向，旋转引入的坐标变换有误差风险。只有在非 0° 显著更优时才采用，体现了工程上的稳健性思维。

### 5.7 XGBoost 智能段落拼接

传统的段落拼接基于固定规则（间距、缩进），在复杂版式下容易出错。RAGFlow 用 XGBoost 模型学习 32 维特征判断两个文本框是否应该拼接，特征涵盖版面类型、标点模式、词法特征、空间位置、行内邻居数等多个维度。模型在大量文档上训练，比规则更鲁棒，能处理跨栏、跨页、图文混排等复杂情况。

### 5.8 KMeans 自动分栏检测

自动检测页面是单栏、双栏还是多栏，不需要人工配置。对每页 x0 坐标聚类，尝试 1-4 类，用轮廓系数评估质量，选最优。全局用多数表决统一列数，保证跨页一致性。这个设计让系统能自适应学术论文（双栏）、书籍（单栏）、报纸（多栏）等不同版式。

### 5.9 多 GPU 并行与信号量控制

通过 `asyncio.Semaphore(1)` 控制每个 GPU 的并发数为 1，避免同一设备上多个推理任务竞争显存导致 OOM。任务按 `i % PARALLEL_DEVICES` 轮询分配到不同设备，实现负载均衡。异常时通过 `task.cancel()` 取消所有任务，防止僵尸任务占用资源。

### 5.10 环境变量全量可配置

系统的关键参数全部通过环境变量暴露：显存限制、线程数、内存策略、旋转开关、识别器类型等。这种设计让运维人员可以在不修改代码的情况下调整系统行为，适应不同的硬件环境和业务需求。所有环境变量都有合理的默认值，保证开箱即用。

---

> **文档生成说明**：本文档基于 RAGFlow 项目 `deepdoc` 模块的源代码进行工程级深度解析，覆盖 `pdf_parser.py`（2057 行）、`ocr.py`（757 行）、`layout_recognizer.py`（456 行）、`table_structure_recognizer.py`（612 行）、`recognizer.py`（441 行）的全部核心逻辑。解析风格按功能块聚合，强调整体设计思路、执行流程、配置含义和工程考量，适配 Kimi-K2.6 长上下文推理特性。
