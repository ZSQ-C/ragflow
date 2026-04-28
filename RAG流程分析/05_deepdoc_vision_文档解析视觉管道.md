# 05 — deepdoc/vision：文档解析视觉管道完整解读

> **目录位置**：`e:\AI\GitHub\RagFlow\deepdoc\vision\`
> **文件清单**：`__init__.py`、`ocr.py`、`recognizer.py`、`layout_recognizer.py`、`table_structure_recognizer.py`、`operators.py`、`postprocess.py`、`seeit.py`、`t_ocr.py`、`t_recognizer.py`
> **核心定位**：PDF 文档理解的全套视觉 AI 管道——从 OCR 文字检测/识别到版面布局分类再到表格结构重建
> **调用链**：PDF 文件 → `init_in_out()` 渲染页面图片 → `OCR.__call__()` 检测+识别 → `LayoutRecognizer()` 布局分类 → `TableStructureRecognizer()` 表格结构 → 结构化输出

***

## 一、核心总览（带逻辑关系）

### 1.1 核心定位

`deepdoc/vision` 是 RAGFlow 整个文档解析链路的**视觉 AI 核心层**，它基于 **ONNX Runtime** 在本地运行三个深度学习模型（文字检测、文字识别、版面布局），在不需要云端 API 的情况下实现完整的 PDF 理解流水线。这套模块解决了企业级文档问答的最基础也是最困难的一步——把扫描件 PDF、图片型 PDF 中的人类可读文字变成机器可检索的文本块，并按语义（标题/正文/表格/图片）分类打标。它的设计核心理念是"全部本地运行"——模型文件从 HuggingFace 下载一次后缓存到本地 `rag/res/deepdoc/` 目录，之后不再访问网络，满足企业数据不出域的安全要求。

**适用场景**：扫描件 PDF 的自动数字化、中英文混杂文档的文字提取、科技论文的公式/图表/表格精细分类、合同/发票/证件等结构化文档的信息提取预处理。

**解决的业务问题**：传统 PDF 解析工具（如 pdfplumber）面对内嵌字体编码错误的 PDF 会产出大量乱码；Tesseract OCR 对中文识别精度不足；云端 OCR API（百度/腾讯/阿里）存在数据泄密风险和高并发成本。`deepdoc/vision` 用自研 ONNX 模型在本地解决这三个问题。

### 1.2 整体流程串讲

整个视觉管道的启动入口在 `__init__.py` 的 `init_in_out()` 函数。它对输入的 PDF 文件，先用 **pdfplumber**（逐页渲染为图片，200 DPI）将每一页转为一幅 PIL Image；如果是普通图片文件，则直接用 `PIL.Image.open()` 打开。输出是一组图片列表。

图片列表随后被送入 **OCR** 协调器（`ocr.py` 的 `OCR` 类）。OCR 内部包含两个子模型：`TextDetector`（DB 文本检测模型）和 `TextRecognizer`（CTC 文字识别模型）。检测器先在整幅图片上推理一次，找到所有文字区域的四点坐标框；识别器再对每个检测框内的图片裁剪下来逐块识别出文字和置信度。这里有一个重要的性能优化——识别阶段把图片按宽高比排序后再批量处理，因为同样宽高比的图片可以拼成一个 tensor 批量跑 ONNX 推理，速度提升显著。OCR 最终返回的是 `[(box坐标, (文字, 置信度)), ...]` 格式的列表。

OCR 结果出来后进入 **LayoutRecognizer**（布局识别器）。它把每个 OCR 检测框中对应的图片区域裁剪出来，按 batch 送入布局模型推理，识别该区域属于 11 类布局中的哪一类——Text（正文）、Title（标题）、Figure（图片）、Table（表格）、Figure caption（图片说明）、Table caption（表头）、Header（页眉）、Footer（页脚）、Reference（参考文献）、Equation（公式）、_background_（背景）。布局标记完成后，LayoutRecognizer 还会执行一遍"垃圾过滤"——对每一页出现的页眉/页脚/参考文献文本做频次统计，高频重复出现的（如每页都出现的"XX公司年度报告"页眉）直接丢弃。

最后是 **TableStructureRecognizer**（表格结构识别器）。它对布局识别中标记为 Figure/Table 的图片区域，再做一轮表格结构推理，识别出**行列结构、表头、跨行跨列单元格（spanning cell）**，并用 `construct_table()` 方法输出含 colspan/rowspan 属性的 HTML `<table>` 字符串。表格内容中的每个单元格还会通过 `blockType()` 做数据类型分类（日期/数字/货币/英文/文本/姓名等），方便后续结构化查询。

**底层依赖链条**：`pdfplumber`（PDF→图片）→ `onnxruntime`（模型推理）→ `cv2`/`numpy`（图像预处理）→ `PIL`（图片格式转换）→ `rag_tokenizer`（中文分词做内容类型判断）

***

## 二、模块拆分（固定顺序 + 关系说明）

### 模块1：入口与图片初始化 —— `init_in_out()` + `__init__.py`

**作用**：整个视觉管道的"启动开关"。负责把 PDF 文件/图片文件转为 ONNX 模型可接受的 PIL Image 列表，同时管理 pdfplumber 的全局线程锁（防止多线程并发打开 PDF 导致崩溃）。

**与其他模块的配合关系**：输出的图片列表是后续所有 OCR、LayoutRecognizer、TableStructureRecognizer 的输入源。`__init__.py` 还集中导出了 `OCR`、`Recognizer`、`LayoutRecognizer`、`TableStructureRecognizer` 四个核心类，供上层 `deepdoc/parser/pdf_parser.py` 调用。

### 模块2：OCR 文字检测与识别 —— `ocr.py`（`TextDetector` + `TextRecognizer` + `OCR`）

**作用**：视觉管道的"第一步加工"。负责在图片上找到所有文字的位置（检测），再识别每个位置的具体文字内容（识别）。`OCR` 类作为协调器串起检测和识别两阶段，并提供了 `detect()`、`recognize()`、`recognize_batch()` 三个精细粒度接口。

**与其他模块的配合关系**：OCR 的输出（文字块坐标+内容）是 `LayoutRecognizer` 和 `TableStructureRecognizer` 的输入。没有 OCR 就没有文字坐标，就没有后续的布局分类。

### 模块3：通用识别器基类 —— `recognizer.py`（`Recognizer`）

**作用**：`LayoutRecognizer` 和 `TableStructureRecognizer` 的**共同父类**。提供 ONNX 模型的加载、预处理（`create_inputs`）、后处理（`postprocess` 含 IoU NMS 去重）、批量推理（`__call__`）以及**全套空间排序和重叠分析工具方法**。

**与其他模块的配合关系**：被 `LayoutRecognizer` 和 `TableStructureRecognizer` 继承，它们只覆写 `__init__`（传入自己的标签列表和模型名）、`preprocess`（YOLO 有特殊预处理）和 `__call__`（加业务逻辑）。

### 模块4：布局识别器 —— `layout_recognizer.py`（`LayoutRecognizer` + `LayoutRecognizer4YOLOv10`）

**作用**：视觉管道的"第二步分类"。对 OCR 识别出的每个文字框，裁剪对应图片区域→推理→打上 11 类布局标签。还负责垃圾过滤、Layout→OCR 框的关联匹配、跨页一致性处理。

### 模块5：表格结构识别器 —— `table_structure_recognizer.py`

**作用**：视觉管道的"第三步结构化"。对表格图片区域识别行列结构，重建 HTML 表格。`construct_table()` 是整个 deepdoc 中最复杂的算法函数之一——用行列 ID 聚类、spanning cell 检测、跨页表格合并等逻辑。

### 模块6：图像预处理算子库 —— `operators.py`

**作用**："管道中的预处理工具站"。提供 `DecodeImage`（字节→RGB数组）、`NormalizeImage`（归一化）、`DetResizeForTest`（文本检测专用resize）、`ToCHWImage`（HWC→CHW）、`NMS`（非极大值抑制去重）、`preprocess()`（通用流水线）等 20+ 个可插拔算子。

**与其他模块的配合关系**：`TextDetector` 和 `TextRecognizer` 通过 `create_operators()` 函数从配置字典动态组合预处理算子链，在推理前对图片做标准化。

### 模块7：ONNX 模型加载工具 —— `load_model()` + `loaded_models` 全局缓存

**作用**：模型加载的**全局单例缓存**。同一个 ONNX 模型文件路径只创建一次 `InferenceSession`，后续调用复用。支持 CPU/CUDA 自动切换、GPU 显存限制、arena 收缩策略。

***

## 三、方法详细解析（强制 5 要素 + 文字流程串讲）

### 3.1 `load_model()` —— ONNX 模型加载与全局缓存

#### 方法文字流程串讲（`ocr.py` L71-L136）

当函数被调用时，先用 `os.path.join(model_dir, nm + ".onnx")` 拼接出模型文件的完整路径（如 `rag/res/deepdoc/rec.onnx`），再用模型路径+设备 ID 生成缓存标签 `model_cached_tag`。全局字典 `loaded_models`（L36 定义的模块级变量）就是缓存——如果标签已存在说明模型已被加载过，直接 `return loaded_model`，省去重新加载的重开销。

如果缓存未命中，第一步是校验文件是否存在（`os.path.exists`），不存在直接抛 `ValueError`。接下来是关键的分支判断：`cuda_is_available()` 函数尝试导入 torch → 检测 `torch.cuda.is_available()` → 如果可用，创建 **CUDAExecutionProvider** 的 `InferenceSession`，并配置 `gpu_mem_limit`（默认 2048MB）和 `arena_extend_strategy`（默认 `kNextPowerOfTwo`）。如果环境变量 `OCR_GPUMEM_ARENA_SHRINKAGE=1`，则启用 GPU 显存 arena 收缩——推理完成后释放显存回系统。如果 cuda 不可用，创建 **CPUExecutionProvider** 的 session，同样启用 CPU arena 收缩。两个 session 都配置了线程数限制（`intra_op_num_threads=2`、`inter_op_num_threads=2`）防止 ONNX Runtime 在多 worker 环境下抢占所有 CPU 核心。

最终 `(session, run_options)` 元组存入全局缓存并返回。

#### 强制 5 要素

| 要素         | 内容                                                                                                      |
| ---------- | ------------------------------------------------------------------------------------------------------- |
| **入参**     | `model_dir`（str，模型目录）、`nm`（str，模型文件名不含.onnx，如 `"rec"`、`"det"`）、`device_id`（int\|None，GPU 设备编号，None=CPU） |
| **核心逻辑**   | 路径拼接→全局缓存查找→cuda 可用性检测→创建 CUDA/CPU InferenceSession→配置线程/显存→缓存返回                                        |
| **输出形式**   | `(ort.InferenceSession, ort.RunOptions)` 元组                                                             |
| **底层关键依赖** | `onnxruntime.InferenceSession`、`torch.cuda.is_available()`、全局字典 `loaded_models`                         |
| **关键代码片段** | `loaded_model = loaded_models.get(model_cached_tag); if loaded_model: return loaded_model`              |

#### 特殊处理标注

- **全局字典缓存**：`loaded_models = {}`（L36），模块级单例，所有 OCR/Recognizer 实例共享
- **GPU 显存限制**：`gpu_mem_limit` 通过环境变量 `OCR_GPU_MEM_LIMIT_MB` 控制，默认 2048MB，防止 ONNX 吃掉全部显存
- **arena 收缩**：仅当 `OCR_GPUMEM_ARENA_SHRINKAGE=1` 时才启用，因为 arena 收缩有性能开销
- **线程限制**：`OCR_INTRA_OP_NUM_THREADS=2` / `OCR_INTER_OP_NUM_THREADS=2` 环境变量可调

***

### 3.2 `TextDetector` —— DB 文本检测器

#### 方法文字流程串讲（`ocr.py` L420-L539）

**初始化（L421-L457）**：首先定义预处理算子列表——`DetResizeForTest`（限制最大边长 960px 保持宽高比）、`NormalizeImage`（ImageNet 均值/标准差归一化）、`ToCHWImage`（HWC→CHW）、`KeepKeys`（只保留 image 和 shape）。后处理用 `DBPostProcess`，参数 `thresh=0.3`（二值化阈值）、`box_thresh=0.5`（框置信度）、`unclip_ratio=1.5`（膨胀比例）、`max_candidates=1000`。加载 `det.onnx` 模型后，读取 ONNX input tensor 的 shape 覆盖 resize 的目标尺寸（如果模型固定了输入大小）。

**`__call__`** **调用（L509-L536）**：输入是一幅图片，先 `copy()` 备份原始图像（后处理需要用原图尺寸做坐标裁剪）。将图片送入 `transform(data, self.preprocess_op)` 执行预处理算子链——resize→归一化→CHW→keepkeys。预处理后的图片 `np.expand_dims(axis=0)` 加 batch 维度。调用 `self.predictor.run(None, input_dict, self.run_options)` 执行 ONNX 推理，这里有一个**重试机制**——最外层 `for i in range(100000)`，实际上只在 `i < 3` 时重试（遇到异常等待 5 秒），超过 3 次抛出异常。推理输出 `outputs[0]` 是二值化概率图，送入 `self.postprocess_op` 做 DB 后处理——从概率图中提取文字区域的轮廓、做 unclip 膨胀、计算最小外接矩形。最后 `filter_tag_det_res()` 对检测框做**四点排序**和**边界裁剪**——检测框坐标限制在图片尺寸内，过滤掉宽度<3 或高度<3 的无效框，按顺时针排序（左上→右上→右下→左下）。

#### 强制 5 要素

| 要素         | 内容                                                                 |
| ---------- | ------------------------------------------------------------------ |
| **入参**     | `img`（np.ndarray，BGR 格式图片）                                         |
| **核心逻辑**   | 预处理算子链→ONNX 推理（最多 3 次重试）→DB 后处理→坐标排序+裁剪+过滤                         |
| **输出形式**   | `dt_boxes`（np.array，shape=(N,4,2)，N 个框的四点坐标）或 `None`               |
| **底层关键依赖** | `onnxruntime.InferenceSession.run()`、`cv2` 图像处理、`operators` 预处理算子  |
| **关键代码片段** | `outputs = self.predictor.run(None, input_dict, self.run_options)` |

#### 特殊处理标注

- **Onnx退避重试**：`for i in range(100000)` 实际上只做 3 次重试（L401-L408），`i>=3` 抛出原始异常
- **四点排序**：`order_points_clockwise()`（L459-L468）用内角和外接矩形算法保证顺时针顺序
- **无效框过滤**：`rect_width <= 3 or rect_height <= 3` 直接丢弃（L486-L487）

***

### 3.3 `TextRecognizer` —— CTC 文字识别器

#### 方法文字流程串讲（`ocr.py` L139-L418）

**支持的识别架构**：`resize_norm_img`（标准 DB 架构）、`resize_norm_img_vl`（VL 架构）、`resize_norm_img_srn`（SRN 架构）、`resize_norm_img_sar`（SAR 架构）、`resize_norm_img_spin`（SPIN 架构）、`resize_norm_img_svtr`（SVTR 架构）、`resize_norm_img_abinet`（ABI-Net 架构）、`norm_img_can`（CAN 架构）。不同架构用不同的 resize 和归一化策略，通过 `rec_image_shape` 配置参数自动选择。

**`__call__`** **调用（L369-L414）**：输入是已裁剪的文字区域图片列表。方法先将所有图片按宽高比排序（`indices = np.argsort(width_list)`）——相同宽高比的图片可以一起 resize 后拼成 batch，提升推理效率。然后按 `batch_num=16` 分批处理。每批先把图片统一 resize 到同样的高度和最大宽度（用 `resize_norm_img()`），再 `np.stack` 拼成 batch tensor。送入 ONNX 推理后，输出 `preds` 是一个 `(batch, T, nclass)` 的概率张量，送入 `self.postprocess_op`（CTCLabelDecode）做**CTC 贪心解码**——对每个时间步取最大概率的字符索引，去重连续重复字符，得到最终文字和置信度。

#### 强制 5 要素

| 要素         | 内容                                                                       |
| ---------- | ------------------------------------------------------------------------ |
| **入参**     | `img_list`（list\[np.ndarray]，文字区域裁剪后的图片列表）                               |
| **核心逻辑**   | 宽高比排序→批量 resize→ONNX 推理→CTC 贪心解码→返回文字+置信度                                |
| **输出形式**   | `rec_res`（list\[tuple\[str, float]]，每个图片的文字和置信度）、`elapsed_time`（float，秒） |
| **底层关键依赖** | `onnxruntime`、`cv2.resize`、`CTCLabelDecode` 后处理器、`ocr.res` 字符字典文件        |
| **关键代码片段** | `preds = outputs[0]; rec_result = self.postprocess_op(preds)`            |

#### 特殊处理标注

- **宽高比排序**：`indices = np.argsort(width_list)`（L376），相同比例的图片拼 batch 不需要过度 padding
- **8 种 resize 策略**：通过 `rec_image_shape` 的第 0 维判断——1 是灰度图架构（SRN/CAN）、3 是 RGB 架构（DB/SAR/SVTR/ABI-Net）

***

### 3.4 `OCR` —— 检测+识别协调器

#### 方法文字流程串讲（`ocr.py` L542-L757）

**初始化（L543-L588）**：首先尝试从本地 `rag/res/deepdoc` 目录加载模型。如果目录不存在（首次运行），通过 `snapshot_download(repo_id="InfiniFlow/deepdoc")` 从 HuggingFace 下载模型文件到本地。**多 GPU 支持**：如果 `settings.PARALLEL_DEVICES > 0`，为每个 GPU 设备分别创建 `TextDetector` 和 `TextRecognizer` 实例（L562-L567），存入列表 `self.text_detector` 和 `self.text_recognizer`；否则创建一份 CPU 实例。`drop_score = 0.5` 是识别置信度的最低阈值，低于此分数的文字结果被丢弃。

**`__call__`** **调用（L714-L757）**：输入一幅图片和 GPU 设备 ID（默认 0）。先 `copy()` 备份原图。调用 `self.text_detector[device_id](img)` 执行**文本检测**→得到检测框列表。调用 `self.sorted_boxes(dt_boxes)` 按从上到下、从左到右排序。对每个检测框，调用 `self.get_rotate_crop_image(ori_im, box)` 将四点坐标按**透视变换**矫正为矩形裁剪图。所有裁剪图送入 `self.text_recognizer[device_id](img_crop_list)` 批量识别。最后按 `drop_score=0.5` 过滤低置信度结果，返回 `[(box.tolist(), (text, score)), ...]`。

**`get_rotate_crop_image()`——自动旋转矫正（L590-L644）**：这是 OCR 中最精妙的一个细节。当检测框高度明显大于宽度（高宽比≥1.5）时，文字可能是竖向排列。方法对裁剪图做三次识别尝试——原始方向、顺时针旋转90°、逆时针旋转90°——选置信度最高的那个方向作为最终结果。这个机制解决了竖向排版文字（如中文书信）的识别问题。

#### 强制 5 要素

| 要素         | 内容                                                                                                        |
| ---------- | --------------------------------------------------------------------------------------------------------- |
| **入参**     | `img`（np.ndarray）、`device_id`（int，GPU设备编号，默认0）                                                            |
| **核心逻辑**   | TextDetector检测→sorted\_boxes排序→get\_rotate\_crop\_image矫正裁剪→TextRecognizer批量识别→drop\_score过滤              |
| **输出形式**   | `list[(list[float;4,2], tuple[str, float])]`——框坐标+文字+置信度                                                  |
| **底层关键依赖** | `TextDetector`、`TextRecognizer`、`snapshot_download`（首次下载模型）                                               |
| **关键代码片段** | `dt_boxes = self.text_detector[device_id](img); rec_res = self.text_recognizer[device_id](img_crop_list)` |

#### 特殊处理标注

- **HuggingFace 自动下载**：首次运行通过 `snapshot_download` 从 `InfiniFlow/deepdoc` 下载模型
- **多 GPU 并行**：`PARALLEL_DEVICES` 环境变量控制，每个 GPU 一份独立模型实例
- **自动旋转矫正**：`get_rotate_crop_image()` 中高宽比≥1.5 的竖排文字自动尝试 0°/90°/270° 三个方向
- **透视变换裁剪**：`cv2.getPerspectiveTransform` + `cv2.warpPerspective` 将任意四边形的文字矫正为矩形

***

### 3.5 `Recognizer` —— 通用识别器基类

#### 方法文字流程串讲（`recognizer.py` L31-L441）

**核心方法列表**：作为 `LayoutRecognizer` 和 `TableStructureRecognizer` 的父类，提供了 10 个静态工具方法和 4 个实例方法。工具方法分为**排序类**（`sort_Y_firstly`、`sort_X_firstly`、`sort_C_firstly`、`sort_R_firstly`）和**重叠分析类**（`overlapped_area`、`find_overlapped`、`find_overlapped_with_threshold`、`find_horizontally_tightest_fit`、`layouts_cleanup`）。

**`sort_Y_firstly(arr, threshold)`（L55-L62）**：主 Y 排序（从上到下），Y 坐标差 < threshold 时按 X 排序（同行的从左到右）。threshold 通常取文本框平均高度的一半。

**`overlapped_area(a, b, ratio=True)`（L114-L132）**：计算两个矩形在 Y 轴方向的重叠面积。`ratio=True` 时返回归一化为第一个矩形面积的比值。这是后续所有布局去重、垃圾过滤的基础算法。

**`layouts_cleanup(boxes, layouts, far=2, thr=0.7)`（L135-L176）**：布局去重算法。当两个相邻布局框（相邻不超过 `far=2` 个位置）类型相同、且重叠面积 > `thr=0.7` 时，保留与 OCR 文字框重叠面积更大的那个。这是为了处理模型可能对同一区域输出多个重叠布局框的情况。

**`__call__()`** **批量推理（L415-L437）**：按 `batch_size=16` 分批，每批先调 `self.preprocess()`，然后 `self.ort_sess.run(None, inputs, self.run_options)` 执行推理，输出送入 `self.postprocess()`。最终返回所有 batch 的布局/表格结构结果。

#### 强制 5 要素

| 要素         | 内容                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **入参**     | `image_list`（list\[np.ndarray]）、`thr=0.7`（置信度阈值）、`batch_size=16`      |
| **核心逻辑**   | batch迭代→preprocess→ONNX推理→postprocess→返回结果列表                          |
| **输出形式**   | `list[list[dict]]`——每个图片一个 dict 列表，每个 dict 含 type/bbox/score          |
| **底层关键依赖** | `onnxruntime`、`self.preprocess()`（子类覆写）、`self.postprocess()`（IoU NMS） |
| **关键代码片段** | `bb = self.postprocess(self.ort_sess.run(...)[0], ins, thr)`          |

#### 特殊处理标注

- **YOLOv10 特化预处理**（L186-L200）：`LayoutRecognizer4YOLOv10` 覆写了 `preprocess()`，按 YOLO 原论文的 LetterBox resize 策略（保持宽高比+padding）
- **IoU NMS**（`postprocess` L359-L403）：每个类别独立做 NMS，iou\_threshold=0.2——保留不同类别的重叠框但去掉同类的重复框

***

### 3.6 `LayoutRecognizer` —— 布局识别器

#### 方法文字流程串讲（`layout_recognizer.py` L33-L157）

**初始化**：11 类标签——_background_、Text、Title、Figure、Figure caption、Table、Table caption、Header、Footer、Reference、Equation。`garbage_layouts = ["footer", "header", "reference"]` 标记为可丢弃的布局类型。支持 TensorRT DLA 加速客户端（`DLAClient`）——如果环境变量 `TENSORRT_DLA_SVR` 指定了推理服务器地址，就用远程推理替代本地 ONNX。

**`__call__()`** **调用（L63-L157）——核心业务逻辑**：这是整个视觉管道中最复杂的单个方法。

1. **模型推理**（L68-L71）：有 DLA 客户端→远程推理；否则调父类 `super().__call__()` 本地 ONNX 批量推理；
2. **缩放还原**（L81-L93）：模型输出坐标要除以 `scale_factor=3` 映射回原图大小；
3. **排序+去重**（L94-L95）：按 Y 坐标排序 → `layouts_cleanup()` 去重；
4. **OCR 框匹配**（L98-L132）：对每类布局（footer→header→reference→figure caption→table caption→title→table→text→figure→equation 按顺序），调用 `find_overlapped_with_threshold()` 找到与布局框重叠的 OCR 文本框，打上 `layout_type` 和 `layoutno` 标记；
5. **垃圾过滤**（L120-L125）：如果布局类型是 garbage\_layouts 且不是"底部偏上的 footer"或"顶部偏下的 header"，丢弃对应的 OCR 框并记录文本到 garbages 字典；
6. **跨页频次过滤**（L149-L156）：对 garbages 中每类布局的文本做 Counter 统计，出现超过 1 次的文本（如每页都出现的页眉）加入垃圾集合，从最终 OCR 结果中移除；
7. **无文本 Figure 补充**（L134-L143）：有些 Figure 区域没有 OCR 文字（纯图片），补充一个空文本的 layout 框。

#### 强制 5 要素

| 要素         | 内容                                                                                                                         |
| ---------- | -------------------------------------------------------------------------------------------------------------------------- |
| **入参**     | `image_list`（list\[PIL.Image]）、`ocr_res`（list\[list\[dict]]，OCR 输出）、`scale_factor=3`、`thr=0.2`、`batch_size=16`、`drop=True` |
| **核心逻辑**   | 模型推理→缩放→排序去重→OCR框匹配→垃圾过滤→频次过滤→无文本Figure补充                                                                                  |
| **输出形式**   | `ocr_res`（list\[dict]，每个 dict 含 text/x0/x1/top/bottom/layout\_type/layoutno）+ `page_layout`（原始布局信息）                        |
| **底层关键依赖** | 父类 `Recognizer.__call__()`、`find_overlapped_with_threshold`、`Counter` 频次统计                                                 |
| **关键代码片段** | `bxs[i]["layout_type"] = lts_[ii]["type"]`                                                                                 |

#### 特殊处理标注

- **layout 匹配顺序**：footer/header 先匹配（优先丢掉），title/table/text 后匹配（保留最重要信息）
- **header/footer 位置宽容**：header 在页面顶部 10% 以上、footer 在底部 10% 以上不会丢弃（这些位置可能是正文标题而非页眉页脚）
- **公式归类为 Figure**：`"equation" if ... else "figure"`（L128），因为公式在视觉上与图片类似

***

### 3.7 `TableStructureRecognizer` —— 表格结构识别器

#### 方法文字流程串讲（`table_structure_recognizer.py` L30-L111）

**标签体系**（6 类）：`table`（表格边界）、`table column`（列）、`table row`（行）、`table column header`（表头列）、`table projected row header`（表头行）、`table spanning cell`（跨单元格）。

**`__call__()`** **调用（L54-L111）**：先判断后端类型——`TABLE_STRUCTURE_RECOGNIZER_TYPE` 环境变量，`"onnx"` 走本地 ONNX、`"ascend"` 走华为 Ascend NPU 加速。然后对检测结果做行列对齐——rows/headers 框被限制到同一个左右边界、columns 框被限制到同一个上下边界，用均值或中位数（样本数>4 用均值、≤4 用最值）平滑边缘噪声。

**`construct_table()`（L152-L200+）——表格结构化核心算法**：

1. **Caption 提取**：找出包含"图"/"表"编号的 caption 文本框，移出 boxes 列表单独存储；
2. **内容类型分类**：每个 cell 调用 `blockType()` 判断数据类型（日期/数字/货币/英文/文本/姓名等）；
3. **行检测**：按 R\_top/R\_bott 分组——相同 R 标签且在 Y 方向上重叠的 box 归入同一行；
4. **列检测**：支持**跨页表格**检测（`crosspage = len(set([b["page_number" for b in boxes])) > 1`）——跨页时用 `sort_X_firstly`（按 X 坐标排序），单页用 `sort_C_firstly`（按 C 标签排序）；
5. **Spanning cell 检测**：对每个 cell，横向比较同行其他 cell 的列号，纵向比较同列其他 cell 的行号，找出 colspan 和 rowspan——生成 HTML `<td colspan="2" rowspan="1">` 格式。

**`blockType()`** **内容类型分类（L121-L149）**：使用正则表达式分类，优先级从高到低——日期格式（如 `2024年1月1日`）→纯数字→纯大写字母→英文→混合数字/字母→单字符→中文分词 token 统计（token 数 3\~12 即短句→`Tx`文本、>12→`Lx`长文本、单个 token 且词性为 `nr` → `Nr` 人名）。

#### 强制 5 要素

| 要素         | 内容                                                                                                         |
| ---------- | ---------------------------------------------------------------------------------------------------------- |
| **入参**     | `boxes`（list\[dict]，含 text/x0/x1/top/bottom/R/R\_top/R\_bott/C/C\_left/C\_right）                           |
| **核心逻辑**   | caption 提取→blockType 分类→行列聚类→spanning cell检测→HTML 生成                                                       |
| **输出形式**   | HTML 字符串（含 colspan/rowspan）或描述文本                                                                           |
| **底层关键依赖** | 父类 `Recognizer.__call__()`、`rag_tokenizer`（中文分词+词性标注）                                                      |
| **关键代码片段** | `boxes = Recognizer.sort_R_firstly(boxes, rowh/2)` — 行排序；`Recognizer.sort_C_firstly(boxes, colwm/2)` — 列排序 |

#### 特殊处理标注

- **跨页表格支持**：检测 `page_number` 集合大小>1 → `sort_X_firstly`，不同页的表格拼接
- **Ascend NPU 支持**：华为 Ascend 910B 芯片加速推理

***

## 四、同类逻辑对比表

### 4.1 文字识别 resize 策略对比

| 架构          | 方法                       | 归一化方式                     | 适用场景           | 图片要求     |
| ----------- | ------------------------ | ------------------------- | -------------- | -------- |
| **DB (标准)** | `resize_norm_img`        | BGR→RGB均值0.5/标准差0.5       | 通用横排文字         | 3通道 RGB  |
| **VL**      | `resize_norm_img_vl`     | BGR→RGB /255              | 长文本            | 3通道 RGB  |
| **SRN**     | `resize_norm_img_srn`    | BGR→Gray                  | 不规则文字          | 1通道灰度    |
| **SAR**     | `resize_norm_img_sar`    | 均值0.5/标准差0.5 + -1 padding | 不规则长文本         | 3通道 RGB  |
| **SVTR**    | `resize_norm_img_svtr`   | 均值0.5/标准差0.5              | Transformer 结构 | 3通道 RGB  |
| **ABI-Net** | `resize_norm_img_abinet` | ImageNet 均值/标准差           | 视觉语言模型         | 3通道 RGB  |
| **CAN**     | `norm_img_can`           | 仅灰度 /255                  | 仅灰度图           | 1通道 Gray |
| **SPIN**    | `resize_norm_img_spin`   | ImageNet 127.5 均值         | 旋转不变           | 1通道 Gray |

### 4.2 布局排序方法对比

| 方法               | 排序主键         | 排序次键     | 使用场景    |
| ---------------- | ------------ | -------- | ------- |
| `sort_Y_firstly` | top（Y坐标）     | x0（X坐标）  | 通用文字排序  |
| `sort_X_firstly` | x0（X坐标）      | top（Y坐标） | 跨页表格列排序 |
| `sort_C_firstly` | C\_left（列标签） | top      | 普通表格列排序 |
| `sort_R_firstly` | R\_top（行标签）  | x0       | 表格行排序   |

***

## 五、疑惑解答

**Q1：为什么 OCR 的** **`get_rotate_crop_image()`** **需要自动旋转矫正？**

很多中文文档（尤其是书信、合同）中的文字是竖向排版的。如果直接用检测框裁剪送入识别器，模型会把竖向文字横向拉伸，导致识别不出。通过三次尝试（原始/顺时针90°/逆时针90°）取最高置信度结果，可以自动适应横排和竖排两种文字方向。

**Q2：为什么** **`layouts_cleanup()`** **用重叠面积更大的一方来去重，而不是直接丢弃？**

因为 OCR 的文本框是内容实际出现的区域，布局框是模型预测的语义区域。布局框之间重叠时，和 OCR 框重叠更多的那个说明它覆盖了更多实际文字，是该区域最准确的布局分类——保留它比盲目丢弃更合理。

**Q3：`blockType()`** **的类型优先级为什么是日期→数字→大写字母→英文→混合？**

因为正则匹配是"贪婪从左到右"的。如果先匹配"纯数字"再匹配"日期"，日期 `2024年1月1日` 中的 `2024` 会先被误判为纯数字。所以更具体的模式（日期含中文数字）必须放在泛化模式（纯数字）之前。这是正则优先级设计的典型实践。

***

## 六、规范修正

- "PaddleOCR"在视觉层没有直接使用，正确描述是"自研 ONNX OCR 引擎"
- "YOLO 系列"在这里对应"YOLOv10 布局识别"
- "CTC"指 Connectionist Temporal Classification——处理序列对齐问题的损失函数/解码方法
- "NMS"统一使用"非极大值抑制"
- "HWC/CHW"——Height-Width-Channel / Channel-Height-Width 图片维度顺序

***

## 七、可复现实操步骤（傻瓜式落地）

| 步骤 | 操作内容   | 依赖 API / 模块              | 最简代码                                                                                            | 注意事项                       |
| -- | ------ | ------------------------ | ----------------------------------------------------------------------------------------------- | -------------------------- |
| 1  | 安装依赖   | pip                      | `pip install onnxruntime pdfplumber opencv-python pillow numpy`                                 | onnxruntime 可用 openvino 替代 |
| 2  | 下载模型   | HuggingFace              | `snapshot_download(repo_id="InfiniFlow/deepdoc", local_dir="../rag/res/deepdoc")`               | 约 500MB，GitHub 可用 HF 镜像    |
| 3  | PDF→图片 | pdfplumber               | `pdf=pdfplumber.open("doc.pdf");imgs=[p.to_image(resolution=200).annotated for p in pdf.pages]` | 200 DPI 平衡精度和速度            |
| 4  | OCR 检测 | TextDetector             | `ocr=OCR(); boxes=ocr("img.jpg")`                                                               | 自动下载模型、自动 CPU/CUDA 切换      |
| 5  | 布局识别   | LayoutRecognizer         | `lr=LayoutRecognizer("layout"); lts=lr(imgs, ocr_res)`                                          | 模型名可选 `"layout"`           |
| 6  | 表格结构   | TableStructureRecognizer | `tsr=TableStructureRecognizer(); html=tsr.construct_table(boxes)`                               | boxes 需含 R/C 标签和坐标         |

***

## 八、关键模块总览

| 模块名称                       | 文件                              | 负责功能         | 在流程中的核心作用                                         |
| -------------------------- | ------------------------------- | ------------ | ------------------------------------------------- |
| `init_in_out`              | `__init__.py`                   | PDF/图片→图片列表  | 管道入口，将输入统一为图片格式                                   |
| `load_model`               | `ocr.py`                        | ONNX 模型加载+缓存 | 全局单例缓存，避免重复加载                                     |
| `TextDetector`             | `ocr.py`                        | DB 文本检测      | 找到图片中所有文字位置的坐标框                                   |
| `TextRecognizer`           | `ocr.py`                        | CTC 文字识别     | 对每个裁剪区域识别出文字字符                                    |
| `OCR`                      | `ocr.py`                        | 检测+识别协调器     | 串联检测→排序→矫正裁剪→批量识别                                 |
| `Recognizer`               | `recognizer.py`                 | 通用识别器基类      | 提供排序、重叠分析、IoU NMS 等工具                             |
| `LayoutRecognizer`         | `layout_recognizer.py`          | 11类布局分类      | 为 OCR 框打 Text/Title/Table/Figure 等标签              |
| `LayoutRecognizer4YOLOv10` | `layout_recognizer.py`          | YOLOv10 特化布局 | 更快的布局检测，LetterBox 预处理                             |
| `TableStructureRecognizer` | `table_structure_recognizer.py` | 表格结构重建       | 识别行列/表头/spanning cell→HTML table                  |
| `operators.*`              | `operators.py`                  | 图像预处理算子库     | 提供 resize/归一化/NMS/CHW转换等 20+ 算子                   |
| `postprocess.*`            | `postprocess.py`                | 后处理器         | DBPostProcess(二值化概率图→框坐标)、CTCLabelDecode(概率序列→文字) |

