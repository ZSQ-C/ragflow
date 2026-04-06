# RAGFlow rag/flow 模块工业级详细解析

## 模块架构总览

```
rag/flow/
├── base.py                    # 基类定义
├── pipeline.py                # 管道编排引擎
├── file.py                    # 文件处理组件
├── extractor/
│   ├── extractor.py           # 内容提取器
│   └── schema.py              # 提取器数据模式
├── hierarchical_merger/
│   ├── hierarchical_merger.py # 层级合并器
│   └── schema.py              # 合并器数据模式
├── parser/
│   ├── parser.py              # 文档解析器
│   └── schema.py              # 解析器数据模式
├── splitter/
│   ├── splitter.py            # 文本分割器
│   └── schema.py              # 分割器数据模式
├── tokenizer/
│   ├── tokenizer.py           # 分词向量化器
│   └── schema.py              # 分词器数据模式
```

---

## 一、base.py - 基类定义

### 1. ProcessParamBase 类

#### ① 类注释与设计意图
`ProcessParamBase` 是所有Flow处理组件参数的基类，继承自 `ComponentParamBase`。设计意图是为Flow管道中的处理组件提供统一的参数配置基础设施，包含超时控制和日志持久化配置。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `timeout` | `int` | 组件执行超时时间，默认100000000毫秒 | 防止组件执行无限阻塞，提供超时保护机制 |
| `persist_logs` | `bool` | 是否持久化日志，默认True | 控制日志存储行为，便于调试和审计 |

#### ③ 构造方法
```python
def __init__(self):
    super().__init__()
    self.timeout = 100000000
    self.persist_logs = True
```
**作用**：初始化参数基类，设置默认超时时间和日志持久化标志。

**初始化逻辑**：
1. 调用父类 `ComponentParamBase.__init__()` 初始化基础参数
2. 设置 `timeout` 为极大值（约27.7小时），作为默认无超时限制
3. 设置 `persist_logs` 为 True，默认开启日志持久化

---

### 2. ProcessBase 类

#### ① 类注释与设计意图
`ProcessBase` 是所有Flow处理组件的抽象基类，继承自 `ComponentBase`。设计意图是定义Flow管道中处理组件的标准执行模式，包括超时控制、回调机制、错误处理和执行计时。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `callback` | `functools.partial` | 进度回调函数，绑定组件ID | 向上层报告执行进度和状态，支持UI更新 |

#### ③ 构造方法
```python
def __init__(self, pipeline, id, param: ProcessParamBase):
    super().__init__(pipeline, id, param)
    if hasattr(self._canvas, "callback"):
        self.callback = partial(self._canvas.callback, id)
    else:
        self.callback = partial(lambda *args, **kwargs: None, id)
```
**作用**：初始化处理组件基类，绑定回调函数。

**参数意义**：
- `pipeline`：管道实例，提供执行上下文和组件访问能力
- `id`：组件唯一标识符，用于日志和回调定位
- `param`：组件参数实例，包含配置信息

**初始化逻辑**：
1. 调用父类 `ComponentBase.__init__()` 初始化组件基础属性
2. 检查 `_canvas` 是否有 `callback` 方法
3. 若有，绑定回调函数并预填充组件ID
4. 若无，创建空回调函数，避免调用时报错

#### ④ 普通方法

##### invoke 方法
```python
async def invoke(self, **kwargs) -> dict[str, Any]:
```
**功能**：组件执行的统一入口，负责超时控制、错误处理、计时和回调通知。

**实现步骤**：
1. 调用 `time.perf_counter()` 记录创建时间，存入输出字典的 `_created_time` 键
2. 遍历 `kwargs`，将所有入参存入输出字典
3. 使用 `asyncio.wait_for()` 包装 `_invoke()` 调用，传入 `self._param.timeout` 作为超时参数
4. 执行成功后，调用 `self.callback(1, "Done")` 通知完成
5. 捕获异常时：
   - 若配置了异常默认值，调用 `set_exception_default_value()` 设置默认输出
   - 否则，将错误信息存入 `_ERROR` 键
   - 记录异常日志
   - 调用 `self.callback(-1, str(e))` 通知错误
6. 计算执行耗时，存入 `_elapsed_time` 键
7. 返回输出字典

**数据流向**：
- 入参 `kwargs` → 输出字典（作为中间数据）
- `_invoke()` 执行结果 → 输出字典
- 输出字典 → 返回给调用方

**依赖调用**：
- `asyncio.wait_for()`：异步超时控制
- `time.perf_counter()`：高精度计时
- `logging.exception()`：异常日志记录

**异常处理逻辑**：
- 超时异常：由 `asyncio.wait_for()` 抛出 `asyncio.TimeoutError`
- 业务异常：捕获后存入 `_ERROR` 键，不中断流程

##### _invoke 方法
```python
@timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
async def _invoke(self, **kwargs):
    raise NotImplementedError()
```
**功能**：组件核心执行逻辑的抽象方法，由子类实现。

**设计约束**：
- 使用 `@timeout` 装饰器提供双重超时保护
- 默认超时10分钟，可通过环境变量 `COMPONENT_EXEC_TIMEOUT` 配置
- 子类必须实现此方法

---

## 二、pipeline.py - 管道编排引擎

### 1. Pipeline 类

#### ① 类注释与设计意图
`Pipeline` 继承自 `Graph` 类，是Flow管道的核心编排引擎。设计意图是将多个处理组件按照DSL配置串联执行，提供进度追踪、日志记录、任务取消检测和结果汇总能力。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `_doc_id` | `str \| None` | 文档ID，关联处理的文档 | 用于获取文档元数据和知识库信息 |
| `_flow_id` | `str \| None` | 流程ID，标识当前流程实例 | 用于日志存储和追踪 |
| `_kb_id` | `str \| None` | 知识库ID，从文档关联获取 | 用于知识库相关操作 |
| `error` | `str` | 错误信息，存储执行过程中的错误 | 用于错误传递和最终报告 |

#### ③ 构造方法
```python
def __init__(self, dsl: str|dict, tenant_id=None, doc_id=None, task_id=None, flow_id=None):
```
**作用**：初始化管道实例，解析DSL配置，关联文档和知识库。

**参数意义**：
- `dsl`：DSL配置，可以是JSON字符串或字典，定义组件和连接关系
- `tenant_id`：租户ID，用于多租户隔离
- `doc_id`：文档ID，关联处理的文档
- `task_id`：任务ID，用于任务追踪
- `flow_id`：流程ID，用于日志存储

**初始化逻辑**：
1. 若 `dsl` 为字典，转换为JSON字符串
2. 调用父类 `Graph.__init__()` 初始化图结构
3. 若 `doc_id` 为调试文档ID，置为None
4. 存储文档ID和流程ID
5. 若有文档ID，调用 `DocumentService.get_knowledgebase_id()` 获取知识库ID
6. 若获取知识库ID失败，将文档ID置为None

#### ④ 普通方法

##### callback 方法
```python
def callback(self, component_name: str, progress: float | int | None = None, message: str = "") -> None:
```
**功能**：组件执行进度回调，记录日志到Redis，更新任务进度，检测任务取消。

**实现步骤**：
1. 构造日志存储键：`{flow_id}-{task_id}-logs`
2. 获取当前时间戳
3. 检测任务是否取消：调用 `has_canceled(task_id)`
4. 若已取消，设置进度为-1，消息追加 `[CANCEL]`
5. 从Redis获取现有日志数据
6. 若日志存在且最后一个日志的组件ID与当前相同：
   - 追加新的trace记录到现有trace列表
   - 计算elapsed_time为当前时间戳与上一条记录的差值
7. 若日志不存在或组件ID不同：
   - 创建新的日志条目
   - 初始化trace列表
8. 若非END组件且有文档ID和任务ID：
   - 计算总进度百分比：每个组件贡献 `1.0 / len(components)`
   - 遍历所有日志条目，累加已完成进度
   - 若发现错误（progress < 0），设置finished为-1
   - 构造进度消息，包含组件名和时间戳
   - 调用 `TaskService.update_progress()` 更新任务进度
9. 若为END组件且无文档ID，将当前DSL存入最后一条trace
10. 将日志数据存入Redis，过期时间30分钟
11. 若任务已取消，抛出 `TaskCanceledException`

**数据流向**：
- 组件执行状态 → Redis日志存储
- 进度信息 → TaskService数据库更新
- 取消状态 → 异常抛出

**依赖调用**：
- `REDIS_CONN.get()` / `REDIS_CONN.set_obj()`：Redis存储操作
- `has_canceled()`：任务取消检测
- `TaskService.update_progress()`：任务进度更新
- `json.loads()` / `json.dumps()`：JSON序列化

**异常处理逻辑**：
- Redis操作异常：记录日志，不中断流程
- 任务取消：抛出 `TaskCanceledException` 终止执行

##### fetch_logs 方法
```python
def fetch_logs(self):
```
**功能**：获取当前管道的执行日志。

**实现步骤**：
1. 构造日志存储键
2. 从Redis获取日志数据
3. 若存在，解析JSON并返回
4. 若不存在或异常，返回空列表

##### run 方法
```python
async def run(self, **kwargs):
```
**功能**：异步执行管道，按路径顺序调用各组件。

**实现步骤**：
1. 初始化Redis日志存储为空列表，过期时间10分钟
2. 初始化错误信息为空字符串
3. 若路径为空：
   - 添加"File"组件作为起始点
   - 获取File组件实例并调用 `invoke()`
   - 若执行出错，设置错误信息并回调通知
4. 若有文档ID，更新任务进度为开始状态（0-5%随机进度）
5. 获取路径中最后一个组件的索引
6. 获取该组件的下游组件列表，扩展到路径中
7. 进入执行循环：
   - 获取上一个组件和当前组件
   - 创建异步任务调用当前组件的 `invoke()`，传入上一组件的输出
   - 使用 `asyncio.gather()` 等待任务完成
   - 若组件执行出错，设置错误信息并回调通知，跳出循环
   - 获取当前组件的下游组件，扩展到路径中
   - 索引递增
8. 回调END组件，传递最终输出或错误信息
9. 若无错误，返回最后一个组件的输出
10. 若有错误，更新任务进度为错误状态，返回空字典

**数据流向**：
```
File组件输出 → Parser组件输入 → Parser组件输出 → Splitter组件输入 → ... → 最终输出
```

**依赖调用**：
- `asyncio.create_task()` / `asyncio.gather()`：异步任务管理
- `get_component_obj()`：获取组件实例
- `get_downstream()`：获取下游组件列表

---

## 三、file.py - 文件处理组件

### 1. FileParam 类

#### ① 类注释与设计意图
`FileParam` 是File组件的参数类，继承自 `ProcessParamBase`。设计意图是为文件获取组件提供参数容器，当前无特殊参数配置。

#### ② 成员变量
无额外成员变量，继承父类所有属性。

#### ③ 构造方法
```python
def __init__(self):
    super().__init__()
```
调用父类初始化。

#### ④ 普通方法

##### check 方法
```python
def check(self):
    pass
```
无参数校验逻辑。

##### get_input_form 方法
```python
def get_input_form(self) -> dict[str, dict]:
    return {}
```
返回空字典，表示无输入表单配置。

---

### 2. File 类

#### ① 类注释与设计意图
`File` 是文件获取组件，继承自 `ProcessBase`。设计意图是从数据库或请求中获取文件信息，作为管道的起始组件，为后续组件提供文件名和文件数据。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `component_name` | `str` | 组件名称标识，值为"File" | 用于组件类型识别和日志记录 |

#### ③ 构造方法
继承自 `ProcessBase`，无额外初始化逻辑。

#### ④ 普通方法

##### _invoke 方法
```python
async def _invoke(self, **kwargs):
```
**功能**：获取文件信息，支持从文档ID或请求参数两种来源。

**实现步骤**：
1. 检查 `_canvas._doc_id` 是否存在
2. 若存在文档ID：
   - 调用 `DocumentService.get_by_id()` 获取文档记录
   - 若获取失败，设置 `_ERROR` 输出并返回
   - 将文档名称存入输出字典的 `name` 键
3. 若不存在文档ID：
   - 从 `kwargs` 获取 `file` 参数（列表形式）
   - 取第一个文件元素
   - 将文件名存入 `name` 键
   - 将完整文件信息存入 `file` 键
4. 调用回调通知文件获取完成

**数据流向**：
- 文档ID → DocumentService → 文档名称 → 输出
- 请求参数 → 文件对象 → 文件名和文件数据 → 输出

**依赖调用**：
- `DocumentService.get_by_id()`：文档记录查询

---

## 四、extractor 模块

### 1. ExtractorParam 类 (extractor.py)

#### ① 类注释与设计意图
`ExtractorParam` 是Extractor组件的参数类，多继承自 `ProcessParamBase` 和 `LLMParam`。设计意图是为内容提取器提供LLM配置和目标字段设置。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `field_name` | `str` | 提取结果存储的字段名 | 指定LLM提取内容的存储位置 |

#### ③ 构造方法
```python
def __init__(self):
    super().__init__()
    self.field_name = ""
```
初始化字段名为空字符串。

#### ④ 普通方法

##### check 方法
```python
def check(self):
    super().check()
    self.check_empty(self.field_name, "Result Destination")
```
调用父类校验，并检查 `field_name` 不为空。

---

### 2. Extractor 类 (extractor.py)

#### ① 类注释与设计意图
`Extractor` 是内容提取组件，多继承自 `ProcessBase` 和 `LLM`。设计意图是使用LLM从文本块中提取结构化信息，支持目录（TOC）生成和自定义字段提取。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `component_name` | `str` | 组件名称标识，值为"Extractor" | 用于组件类型识别 |

#### ③ 构造方法
继承自 `ProcessBase` 和 `LLM`，无额外初始化逻辑。

#### ④ 普通方法

##### _build_TOC 方法
```python
async def _build_TOC(self, docs):
```
**功能**：从文档块构建目录结构。

**实现步骤**：
1. 回调通知开始生成目录
2. 对文档块按页码和位置排序
3. 调用 `run_toc_from_text()` 使用LLM生成目录结构
4. 记录目录JSON日志
5. 遍历目录项：
   - 将 `chunk_id` 转换为文档索引
   - 收集该目录项对应的文档ID列表
6. 若目录不为空：
   - 复制最后一个文档块作为目录块
   - 设置目录内容、标记为TOC、页码设为极大值
   - 使用xxhash生成唯一ID
7. 返回目录块或None

**数据流向**：
```
文档块列表 → 排序 → LLM生成目录 → 关联文档ID → 目录块
```

**依赖调用**：
- `run_toc_from_text()`：LLM目录生成
- `xxhash.xxh64()`：快速哈希生成ID

##### _invoke 方法
```python
async def _invoke(self, **kwargs):
```
**功能**：执行内容提取，支持TOC生成和字段提取两种模式。

**实现步骤**：
1. 设置输出格式为"chunks"
2. 回调通知开始生成
3. 获取输入元素，提取参数值
4. 查找列表类型的输入作为chunks
5. 若存在chunks：
   - 若 `field_name` 为"toc"：
     - 为每个chunk添加文档ID和唯一ID
     - 调用 `_build_TOC()` 生成目录
     - 将目录追加到chunks
     - 设置输出并返回
   - 否则执行字段提取：
     - 遍历每个chunk
     - 构建系统提示词和消息
     - 调用 `_generate_async()` 使用LLM提取内容
     - 将提取结果存入指定字段
     - 定期回调进度
     - 设置输出chunks
6. 若不存在chunks：
   - 构建提示词，直接调用LLM生成
   - 将结果作为单元素列表输出

**数据流向**：
```
输入参数 → 识别chunks → LLM提取 → 结果字段填充 → 输出
```

**依赖调用**：
- `_sys_prompt_and_msg()`：构建提示词（继承自LLM）
- `_generate_async()`：异步LLM调用（继承自LLM）

---

### 3. ExtractorFromUpstream 类 (schema.py)

#### ① 类注释与设计意图
`ExtractorFromUpstream` 是pydantic数据模型，用于验证和解析上游组件传递给Extractor的数据。设计意图是提供类型安全的数据接收接口。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `created_time` | `float \| None` | 创建时间，别名为 `_created_time` | 追踪组件执行时间 |
| `elapsed_time` | `float \| None` | 执行耗时，别名为 `_elapsed_time` | 性能监控 |
| `name` | `str` | 文件名 | 必需字段，标识数据来源 |
| `file` | `dict \| None` | 文件信息字典 | 存储文件元数据 |
| `chunks` | `list[dict] \| None` | 文本块列表 | 主要处理数据 |
| `output_format` | `Literal[...] \| None` | 输出格式枚举 | 限制合法输出格式 |
| `json_result` | `list[dict] \| None` | JSON结果，别名为 `json` | 接收JSON格式数据 |
| `markdown_result` | `str \| None` | Markdown结果，别名为 `markdown` | 接收Markdown格式数据 |
| `text_result` | `str \| None` | 文本结果，别名为 `text` | 接收纯文本格式数据 |
| `html_result` | `str \| None` | HTML结果，别名为 `html` | 接收HTML格式数据 |

#### ③ 构造方法
由pydantic自动生成，支持字段别名映射和额外字段禁止。

#### ④ 配置说明
```python
model_config = ConfigDict(populate_by_name=True, extra="forbid")
```
- `populate_by_name=True`：允许通过字段名或别名赋值
- `extra="forbid"`：禁止未定义的字段

---

## 五、hierarchical_merger 模块

### 1. HierarchicalMergerParam 类 (hierarchical_merger.py)

#### ① 类注释与设计意图
`HierarchicalMergerParam` 是层级合并器的参数类。设计意图是配置层级合并的规则，包括层级正则表达式和合并深度。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `levels` | `list` | 层级正则表达式列表，每个元素是对应层级的正则列表 | 用于匹配文本行所属层级 |
| `hierarchy` | `int \| None` | 层级深度限制 | 控制合并的最大层级深度 |

#### ③ 构造方法
```python
def __init__(self):
    super().__init__()
    self.levels = []
    self.hierarchy = None
```
初始化为空列表和None。

#### ④ 普通方法

##### check 方法
```python
def check(self):
    self.check_empty(self.levels, "Hierarchical setups.")
    self.check_empty(self.hierarchy, "Hierarchy number.")
```
校验层级配置和深度不为空。

---

### 2. HierarchicalMerger 类 (hierarchical_merger.py)

#### ① 类注释与设计意图
`HierarchicalMerger` 是层级合并组件。设计意图是根据正则表达式匹配将文本行组织成层级结构，然后按层级深度合并相邻文本，生成符合层级关系的文本块。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `component_name` | `str` | 组件名称标识，值为"HierarchicalMerger" | 用于组件类型识别 |

#### ③ 构造方法
继承自 `ProcessBase`，无额外初始化。

#### ④ 普通方法

##### _invoke 方法
```python
async def _invoke(self, **kwargs):
```
**功能**：执行层级合并，将文本按层级结构组织并合并。

**实现步骤**：
1. 使用 `HierarchicalMergerFromUpstream` 验证输入数据
2. 设置输出格式为"chunks"
3. 回调通知开始合并
4. 根据输入格式提取文本行：
   - 若为markdown/text/html：按换行符分割
   - 若为chunks/json：提取text字段和position_tag
5. 对每行文本进行层级匹配：
   - 遍历层级正则列表
   - 使用 `re.search()` 匹配
   - 记录匹配的层级索引
   - 若无匹配，设为最大层级（叶子节点）
6. 构建层级树结构：
   - 创建根节点（level=-1）
   - 遍历匹配结果，按层级关系构建树
   - 使用DFS算法维护父子关系
7. 遍历树收集合并路径：
   - 使用DFS遍历树
   - 当深度达到 `hierarchy` 限制时，收集路径
   - 路径包含节点索引和文本索引
8. 根据输入格式生成输出：
   - 若为markdown/text/html：直接拼接文本
   - 若为chunks/json：处理图片和位置信息
9. 异步处理图片ID转换
10. 设置输出chunks

**数据流向**：
```
输入数据 → 文本行提取 → 层级匹配 → 树结构构建 → 路径收集 → 文本合并 → 输出
```

**依赖调用**：
- `re.search()`：正则匹配
- `id2image()` / `image2id()`：图片ID转换
- `RAGFlowPdfParser.remove_tag()` / `extract_positions()`：位置处理
- `asyncio.gather()`：异步任务并发

**异常处理逻辑**：
- 输入验证失败：设置 `_ERROR` 输出
- 图片处理异常：取消所有任务，记录错误后重新抛出

---

### 3. HierarchicalMergerFromUpstream 类 (schema.py)

#### ① 类注释与设计意图
与 `ExtractorFromUpstream` 结构相同，用于验证上游数据。设计意图是提供类型安全的数据接收接口。

#### ② 成员变量
与 `ExtractorFromUpstream` 完全相同，参见前文。

---

## 六、parser 模块

### 1. ParserParam 类 (parser.py)

#### ① 类注释与设计意图
`ParserParam` 是文档解析器的参数类。设计意图是配置各种文档类型的解析方法、输出格式和预处理选项。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `allowed_output_format` | `dict[str, list[str]]` | 各文档类型允许的输出格式 | 约束输出格式合法性 |
| `setups` | `dict[str, dict]` | 各文档类型的解析配置 | 存储解析方法和参数 |

#### ③ 构造方法
```python
def __init__(self):
    super().__init__()
    self.allowed_output_format = {...}  # 定义允许的输出格式
    self.setups = {...}  # 定义各类型解析配置
```

**setups 配置详解**：

| 文档类型 | 解析方法 | 输出格式 | 支持后缀 |
|----------|----------|----------|----------|
| `pdf` | deepdoc/plain_text/tcadp/vlm | json/markdown | pdf |
| `spreadsheet` | deepdoc/tcadp | json/markdown/html | xls/xlsx/csv |
| `word` | - | json/markdown | doc/docx |
| `text&markdown` | - | json/text | md/markdown/mdx/txt |
| `slides` | deepdoc/tcadp | json | pptx/ppt |
| `image` | ocr/vlm | json | jpg/jpeg/png/gif |
| `email` | - | json/text | eml/msg |
| `audio` | - | text | 多种音频格式 |
| `video` | - | text | mp4/avi/mkv |
| `epub` | - | json/text | epub |

#### ④ 普通方法

##### check 方法
```python
def check(self):
```
**功能**：校验各文档类型的配置合法性。

**实现步骤**：
1. 检查PDF配置：
   - 解析方法必须为指定值之一
   - 输出格式必须在允许列表中
2. 检查Spreadsheet配置：
   - 输出格式必须在允许列表中
3. 检查Word、Slides、Text、Email、EPUB配置：
   - 输出格式必须在允许列表中
4. 检查Image配置：
   - 若非OCR方法，需要语言配置
5. 检查Audio/Video配置：
   - 需要配置LLM ID

---

### 2. Parser 类 (parser.py)

#### ① 类注释与设计意图
`Parser` 是文档解析组件，是Flow管道中最复杂的组件之一。设计意图是将各种格式的文档解析为结构化数据，支持PDF、Word、Excel、PPT、图片、音频、视频、邮件、EPUB等多种格式。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `component_name` | `str` | 组件名称标识，值为"Parser" | 用于组件类型识别 |

#### ③ 构造方法
继承自 `ProcessBase`，无额外初始化。

#### ④ 普通方法

##### _extract_word_title_lines 静态方法
```python
@staticmethod
def _extract_word_title_lines(doc, to_page=100000):
```
**功能**：从Word文档中提取标题行及其层级。

**实现步骤**：
1. 检查文档和段落是否存在
2. 调用 `bullets_category()` 识别项目符号类型
3. 遍历段落：
   - 调用 `docx_question_level()` 获取标题层级
   - 检测分页符，更新页码
4. 返回(层级, 文本)元组列表

##### _extract_markdown_title_lines 静态方法
```python
@staticmethod
def _extract_markdown_title_lines(sections):
```
**功能**：从Markdown文本中提取标题行及其层级。

**实现步骤**：
1. 提取非空文本段
2. 识别项目符号类型
3. 根据项目符号模式匹配层级
4. 返回(层级, 文本)元组列表

##### _extract_title_texts 静态方法
```python
@staticmethod
def _extract_title_texts(lines):
```
**功能**：从标题行列表中提取标题文本集合。

**实现步骤**：
1. 过滤空文本，收集层级集合
2. 排序层级，确定H2层级阈值
3. 返回层级小于等于H2的文本集合

##### _pdf 方法
```python
def _pdf(self, name, blob, **kwargs):
```
**功能**：解析PDF文档，支持多种解析方法。

**实现步骤**：
1. 获取PDF配置
2. 设置输出格式
3. 提取预处理选项（摘要、作者、标题）
4. 解析方法分发：
   - `deepdoc`：使用 `RAGFlowPdfParser().parse_into_bboxes()`
   - `plain_text`：使用 `PlainParser()`
   - `mineru`：使用MinerU OCR模型
   - `docling`：使用 `DoclingParser`
   - `tcadp parser`：使用腾讯云ADP解析器
   - `paddleocr`：使用PaddleOCR模型
   - 其他：使用VLM视觉语言模型
5. 处理解析结果：
   - 标记图片类型和表格类型
   - 提取作者信息（若启用）
   - 提取摘要信息（若启用）
6. 根据输出格式生成结果：
   - `json`：直接输出bboxes
   - `markdown`：转换为Markdown格式

**依赖调用**：
- `RAGFlowPdfParser` / `PlainParser` / `VisionParser`：PDF解析器
- `DoclingParser` / `TCADPParser`：第三方解析器
- `LLMBundle`：LLM模型调用封装
- `get_model_config_by_type_and_name()`：模型配置获取

##### _spreadsheet 方法
```python
def _spreadsheet(self, name, blob, **kwargs):
```
**功能**：解析电子表格文档。

**实现步骤**：
1. 获取配置和输出格式
2. 若使用TCADP解析器：
   - 根据文件扩展名确定文件类型
   - 调用TCADP解析器
   - 根据输出格式处理结果
3. 若使用DeepDOC解析器：
   - 调用 `ExcelParser`
   - 根据输出格式生成HTML/JSON/Markdown

##### _word 方法
```python
def _word(self, name, blob, **kwargs):
```
**功能**：解析Word文档。

**实现步骤**：
1. 调用 `Docx()` 解析器
2. 若输出JSON：
   - 提取标题行
   - 构建sections列表
   - 处理表格
3. 若输出Markdown：
   - 调用 `to_markdown()` 转换

##### _slides 方法
```python
def _slides(self, name, blob, **kwargs):
```
**功能**：解析PPT文档。

**实现步骤**：
1. 若使用TCADP解析器：
   - 确定文件类型（PPTX/PPT）
   - 调用解析器获取sections和tables
   - 构建JSON结果
2. 若使用DeepDOC解析器：
   - 调用 `RAGFlowPptParser`
   - 输出JSON格式

##### _markdown 方法
```python
def _markdown(self, name, blob, **kwargs):
```
**功能**：解析Markdown/Text文档。

**实现步骤**：
1. 调用 `naive_markdown_parser()` 解析
2. 若输出JSON：
   - 提取标题行
   - 处理图片
   - 合并多个图片
3. 若输出Text：
   - 直接拼接文本

##### _image 方法
```python
def _image(self, name, blob, **kwargs):
```
**功能**：解析图片文档。

**实现步骤**：
1. 使用PIL打开图片
2. 若使用OCR：
   - 调用 `OCR()` 识别文字
3. 若使用VLM：
   - 加载视觉语言模型
   - 调用 `describe()` 或 `describe_with_prompt()` 描述图片
4. 构建JSON结果

##### _audio 方法
```python
def _audio(self, name, blob, **kwargs):
```
**功能**：解析音频文档（语音转文字）。

**实现步骤**：
1. 创建临时文件保存音频
2. 加载语音转文字模型
3. 调用 `transcription()` 转录
4. 输出文本结果

##### _video 方法
```python
def _video(self, name, blob, **kwargs):
```
**功能**：解析视频文档。

**实现步骤**：
1. 加载视觉语言模型
2. 调用 `async_chat()` 处理视频
3. 输出文本结果

##### _email 方法
```python
def _email(self, name, blob, **kwargs):
```
**功能**：解析邮件文档。

**实现步骤**：
1. 根据扩展名判断格式（eml/msg）
2. 若为eml：
   - 使用Python email库解析
   - 提取头部字段
   - 提取正文（支持multipart）
   - 提取附件
3. 若为msg：
   - 使用 `extract_msg` 库解析
   - 提取字段和附件
4. 根据输出格式生成结果

##### _epub 方法
```python
def _epub(self, name, blob, **kwargs):
```
**功能**：解析EPUB文档。

**实现步骤**：
1. 调用 `EpubParser()` 解析
2. 根据输出格式生成JSON或Text

##### _invoke 方法
```python
async def _invoke(self, **kwargs):
```
**功能**：解析器主入口，根据文件类型分发到对应解析方法。

**实现步骤**：
1. 使用 `ParserFromUpstream` 验证输入
2. 获取文件名
3. 根据文档ID或文件信息获取文件二进制数据
4. 遍历 `setups` 配置：
   - 检查文件扩展名是否匹配
   - 调用对应的解析方法
5. 异步处理图片ID转换
6. 设置输出

**数据流向**：
```
文件信息 → 二进制数据 → 类型判断 → 解析方法 → 结构化数据 → 图片ID转换 → 输出
```

---

### 3. ParserFromUpstream 类 (schema.py)

#### ① 类注释与设计意图
`ParserFromUpstream` 是解析器的输入数据模型。设计意图是验证上游传递的文件信息。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `created_time` | `float \| None` | 创建时间 | 时间追踪 |
| `elapsed_time` | `float \| None` | 执行耗时 | 性能监控 |
| `name` | `str` | 文件名 | 必需字段 |
| `file` | `dict \| None` | 文件信息 | 文件元数据 |
| `abstract` | `bool` | 是否提取摘要 | 预处理选项 |
| `author` | `bool` | 是否提取作者 | 预处理选项 |

---

## 七、splitter 模块

### 1. SplitterParam 类 (splitter.py)

#### ① 类注释与设计意图
`SplitterParam` 是文本分割器的参数类。设计意图是配置文本分割的规则，包括块大小、分隔符、重叠率和上下文大小。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `chunk_token_size` | `int` | 每个文本块的Token大小，默认512 | 控制块大小，平衡检索精度和上下文完整性 |
| `delimiters` | `list[str]` | 分隔符列表，默认 `["\n"]` | 定义文本分割边界 |
| `overlapped_percent` | `float` | 块重叠百分比，默认0 | 增加上下文连续性，提高检索召回 |
| `children_delimiters` | `list[str]` | 子分隔符列表 | 二次分割，更细粒度的切分 |
| `table_context_size` | `int` | 表格上下文大小 | 表格数据的额外上下文 |
| `image_context_size` | `int` | 图片上下文大小 | 图片数据的额外上下文 |

#### ③ 构造方法
```python
def __init__(self):
    super().__init__()
    self.chunk_token_size = 512
    self.delimiters = ["\n"]
    self.overlapped_percent = 0
    self.children_delimiters = []
    self.table_context_size = 0
    self.image_context_size = 0
```

#### ④ 普通方法

##### check 方法
```python
def check(self):
    self.check_empty(self.delimiters, "Delimiters.")
    self.check_positive_integer(self.chunk_token_size, "Chunk token size.")
    self.check_decimal_float(self.overlapped_percent, "Overlapped percentage: [0, 1)")
    self.check_nonnegative_number(self.table_context_size, "Table context size.")
    self.check_nonnegative_number(self.image_context_size, "Image context size.")
```
校验各参数的合法性和范围。

---

### 2. Splitter 类 (splitter.py)

#### ① 类注释与设计意图
`Splitter` 是文本分割组件。设计意图是将解析后的文本按照配置规则分割成适合检索的文本块，支持纯文本和带图片的文本分割。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `component_name` | `str` | 组件名称标识，值为"Splitter" | 用于组件类型识别 |

#### ③ 构造方法
继承自 `ProcessBase`，无额外初始化。

#### ④ 普通方法

##### _invoke 方法
```python
async def _invoke(self, **kwargs):
```
**功能**：执行文本分割，支持多种输入格式。

**实现步骤**：
1. 使用 `SplitterFromUpstream` 验证输入
2. 构造分隔符字符串
3. 构造子分隔符正则模式
4. 设置输出格式为"chunks"
5. 回调通知开始分割
6. 规范化重叠百分比
7. 根据输入格式处理：
   - 若为markdown/text/html：
     - 调用 `naive_merge()` 进行简单合并分割
     - 若有子分隔符，进行二次分割
     - 设置输出
   - 若为json/chunks：
     - 处理表格和图片上下文
     - 调用 `attach_media_context()` 附加媒体上下文
     - 提取文本和图片
     - 调用 `naive_merge_with_images()` 进行带图片的分割
     - 处理位置标签
     - 异步转换图片ID
     - 若有子分隔符，进行二次分割
8. 回调通知完成

**数据流向**：
```
输入文本 → 分隔符处理 → naive_merge → 块列表 → 子分隔符分割 → 输出chunks
```

**依赖调用**：
- `naive_merge()` / `naive_merge_with_images()`：文本分割算法
- `attach_media_context()`：媒体上下文附加
- `RAGFlowPdfParser.remove_tag()` / `extract_positions()`：位置处理
- `image2id()`：图片ID转换

---

### 3. SplitterFromUpstream 类 (schema.py)

#### ① 类注释与设计意图
`SplitterFromUpstream` 是分割器的输入数据模型。结构与 `ExtractorFromUpstream` 相同。

---

## 八、tokenizer 模块

### 1. TokenizerParam 类 (tokenizer.py)

#### ① 类注释与设计意图
`TokenizerParam` 是分词向量化器的参数类。设计意图是配置分词和向量化的方法及参数。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `search_method` | `list[str]` | 搜索方法列表，默认 `["full_text", "embedding"]` | 支持全文检索和向量检索 |
| `filename_embd_weight` | `float` | 文件名嵌入权重，默认0.1 | 文件名对向量的影响程度 |
| `fields` | `list[str]` | 处理的字段列表，默认 `["text"]` | 指定需要处理的字段 |

#### ③ 构造方法
```python
def __init__(self):
    super().__init__()
    self.search_method = ["full_text", "embedding"]
    self.filename_embd_weight = 0.1
    self.fields = ["text"]
```

#### ④ 普通方法

##### check 方法
```python
def check(self):
    for v in self.search_method:
        self.check_valid_value(v.lower(), "Chunk method abnormal.", ["full_text", "embedding"])
```
校验搜索方法必须为指定值。

---

### 2. Tokenizer 类 (tokenizer.py)

#### ① 类注释与设计意图
`Tokenizer` 是分词向量化组件。设计意图是对文本块进行分词处理和向量化嵌入，生成用于检索的分词结果和向量表示。

#### ② 成员变量

| 变量名 | 类型 | 作用 | 设计原因 |
|--------|------|------|----------|
| `component_name` | `str` | 组件名称标识，值为"Tokenizer" | 用于组件类型识别 |

#### ③ 构造方法
继承自 `ProcessBase`，无额外初始化。

#### ④ 普通方法

##### _embedding 方法
```python
async def _embedding(self, name, chunks):
```
**功能**：对文本块进行向量化嵌入。

**实现步骤**：
1. 计算处理部分数量
2. 获取嵌入模型配置：
   - 若有知识库ID，从知识库获取配置
   - 否则获取租户默认嵌入模型
3. 加载嵌入模型
4. 提取文本内容，清理HTML标签
5. 对文件名进行嵌入
6. 批量处理文本嵌入：
   - 使用 `embed_limiter` 限制并发
   - 调用 `truncate()` 截断超长文本
   - 调用 `embedding_model.encode()` 生成向量
   - 定期回调进度
7. 计算加权向量：`title_w * title_vec + (1 - title_w) * content_vec`
8. 将向量存入chunk的 `q_{dim}_vec` 字段
9. 返回处理后的chunks和Token消耗

**数据流向**：
```
chunks → 文本提取 → 批量嵌入 → 向量加权 → 存入chunks → 输出
```

**依赖调用**：
- `LLMBundle`：嵌入模型加载
- `truncate()`：文本截断
- `embed_limiter`：并发限制信号量
- `np.concatenate()`：向量拼接

##### _invoke 方法
```python
async def _invoke(self, **kwargs):
```
**功能**：执行分词和向量化。

**实现步骤**：
1. 验证输入数据
2. 设置输出格式为"chunks"
3. 若启用全文检索：
   - 回调通知开始分词
   - 处理chunks：
     - 对文件名进行分词，存入 `title_tks` 和 `title_sm_tks`
     - 处理questions字段
     - 处理keywords字段
     - 处理summary或text字段，存入 `content_ltks` 和 `content_sm_ltks`
   - 或处理markdown/text/html格式
   - 或处理json格式
   - 回调通知分词完成
4. 若启用向量检索：
   - 回调通知开始嵌入
   - 调用 `_embedding()` 生成向量
   - 设置Token消耗输出
   - 回调通知嵌入完成
5. 设置输出chunks

**数据流向**：
```
输入数据 → 格式判断 → 分词处理 → 向量嵌入 → 输出chunks
```

**依赖调用**：
- `rag_tokenizer.tokenize()`：分词
- `rag_tokenizer.fine_grained_tokenize()`：细粒度分词
- `_embedding()`：向量化

---

### 3. TokenizerFromUpstream 类 (schema.py)

#### ① 类注释与设计意图
`TokenizerFromUpstream` 是分词器的输入数据模型，包含额外的验证逻辑。

#### ② 成员变量
与 `SplitterFromUpstream` 结构相同。

#### ③ 模型验证器

##### _check_payloads 方法
```python
@model_validator(mode="after")
def _check_payloads(self) -> "TokenizerFromUpstream":
```
**功能**：验证输入数据的完整性。

**实现步骤**：
1. 若有chunks，直接返回
2. 若输出格式为markdown/text/html，检查对应payload不为空
3. 否则检查json_result或chunks不为空
4. 验证失败抛出 `ValueError`

---

## 九、继承关系图

```
ComponentParamBase (agent/component/base.py)
        │
        ▼
ProcessParamBase (rag/flow/base.py)
        │
        ├── FileParam
        ├── ExtractorParam ←── LLMParam
        ├── HierarchicalMergerParam
        ├── ParserParam
        ├── SplitterParam
        └── TokenizerParam

ComponentBase (agent/component/base.py)
        │
        ▼
ProcessBase (rag/flow/base.py)
        │
        ├── File
        ├── Extractor ←── LLM
        ├── HierarchicalMerger
        ├── Parser
        ├── Splitter
        └── Tokenizer

Graph (agent/canvas.py)
        │
        ▼
Pipeline (rag/flow/pipeline.py)
```

---

## 十、数据流转图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Pipeline 执行流程                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────┐    ┌────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐   │
│  │ File │───▶│ Parser │───▶│ Splitter │───▶│ Tokenizer│───▶│  Output  │   │
│  └──────┘    └────────┘    └──────────┘    └──────────┘    └──────────┘   │
│      │            │              │               │                         │
│      ▼            ▼              ▼               ▼                         │
│  文件信息     文档解析       文本分割        分词向量化                      │
│  name/blob   chunks       chunks         chunks with                      │
│              positions    with images     vectors                         │
│                                                                             │
│  可选组件:                                                                   │
│  ┌───────────┐    ┌────────────────────┐                                   │
│  │ Extractor │───▶│ HierarchicalMerger │                                   │
│  └───────────┘    └────────────────────┘                                   │
│  LLM提取            层级合并                                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 十一、关键设计模式

### 1. 模板方法模式
`ProcessBase.invoke()` 定义了组件执行的标准流程，子类只需实现 `_invoke()` 方法。

### 2. 策略模式
`Parser` 组件根据文件类型选择不同的解析策略（PDF/Word/Excel等）。

### 3. 责任链模式
`Pipeline` 按路径顺序执行组件，每个组件的输出作为下一个组件的输入。

### 4. 工厂模式
通过 `component_class()` 函数根据组件名称动态创建组件实例。

---

## 十二、扩展指南

### 添加新的处理组件

1. 创建参数类，继承 `ProcessParamBase`：
```python
class NewProcessorParam(ProcessParamBase):
    def __init__(self):
        super().__init__()
        self.custom_param = ""
    
    def check(self):
        self.check_empty(self.custom_param, "Custom param")
```

2. 创建组件类，继承 `ProcessBase`：
```python
class NewProcessor(ProcessBase):
    component_name = "NewProcessor"
    
    async def _invoke(self, **kwargs):
        # 实现处理逻辑
        pass
```

3. 创建输入数据模型：
```python
class NewProcessorFromUpstream(BaseModel):
    # 定义字段
    pass
```

4. 在组件注册表中注册新组件。

### 添加新的文档解析器

1. 在 `ParserParam.setups` 中添加新文档类型配置
2. 在 `ParserParam.allowed_output_format` 中添加允许的输出格式
3. 在 `Parser` 类中添加解析方法（如 `_new_format()`）
4. 在 `_invoke()` 方法中添加分发逻辑
