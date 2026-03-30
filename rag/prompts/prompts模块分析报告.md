# prompts 模块分析报告

## 一、核心总览（带逻辑关系）

### 核心定位
`prompts` 模块是 RAGFlow 的**提示词模板管理中心**，负责提示词的加载、渲染、格式化和 LLM 调用封装。核心解决的问题是：如何统一管理大量提示词模板，如何动态渲染模板参数，如何封装 LLM 调用逻辑以提高复用性。

### 整体流程串讲
执行链路从 `template.py` 的 `load_prompt()` 开始：从磁盘加载 .md 文件 → 缓存到内存字典 → 通过 Jinja2 环境渲染模板 → `generator.py` 中的函数封装 LLM 调用逻辑（如 `keyword_extraction`、`content_tagging`）→ 返回结构化结果。整个模块采用延迟加载和缓存机制，避免重复 I/O 操作。

---

## 二、模块拆分（固定顺序 + 关系说明）

### 1. 初始化模块（template.py）
**作用**：提供提示词模板的加载和缓存功能。
**位置**：整体流程的基础设施层，被所有提示词函数依赖。
**配合关系**：被 `generator.py` 中的全局变量加载调用。

```python
PROMPT_DIR = os.path.dirname(__file__)
_loaded_prompts = {}

def load_prompt(name: str) -> str:
    if name in _loaded_prompts:
        return _loaded_prompts[name]
    
    path = os.path.join(PROMPT_DIR, f"{name}.md")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Prompt file '{name}.md' not found in prompts/ directory.")
    
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        _loaded_prompts[name] = content
        return content
```

### 2. 核心入口方法模块（generator.py 全局变量）
**作用**：在模块加载时预加载所有提示词模板。
**位置**：模块初始化阶段，为后续函数调用提供模板。
**配合关系**：被各提示词函数引用。

```python
CITATION_PROMPT_TEMPLATE = load_prompt("citation_prompt")
KEYWORD_PROMPT_TEMPLATE = load_prompt("keyword_prompt")
QUESTION_PROMPT_TEMPLATE = load_prompt("question_prompt")
# ... 更多模板加载

PROMPT_JINJA_ENV = jinja2.Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
```

### 3. 分支逻辑方法模块（格式化函数）
**作用**：格式化检索结果、消息历史等数据，为 LLM 输入做准备。
**位置**：在 LLM 调用前被调用，处理输入数据。
**配合关系**：被检索流程和对话流程调用。

```python
def chunks_format(reference):
    if not reference or not isinstance(reference, dict):
        return []
    raw_chunks = reference.get("chunks", [])
    return [
        {
            "id": get_value(chunk, "chunk_id", "id"),
            "content": get_value(chunk, "content", "content_with_weight"),
            "document_id": get_value(chunk, "doc_id", "document_id"),
            # ... 更多字段映射
        }
        for chunk in raw_chunks
        if isinstance(chunk, dict)
    ]

def kb_prompt(kbinfos, max_tokens, hash_id=False):
    knowledges = [get_value(ck, "content", "content_with_weight") for ck in kbinfos["chunks"]]
    # ... token 计数和截断逻辑
    return knowledges
```

### 4. 具体实现方法模块（LLM 调用封装）
**作用**：封装各类 LLM 调用场景，包括关键词提取、问题生成、内容标签等。
**位置**：核心业务逻辑层，直接与 LLM 交互。
**配合关系**：被 task_executor、graphrag 等模块调用。

```python
async def keyword_extraction(chat_mdl, content, topn=3):
    template = PROMPT_JINJA_ENV.from_string(KEYWORD_PROMPT_TEMPLATE)
    rendered_prompt = template.render(content=content, topn=topn)
    
    msg = [{"role": "system", "content": rendered_prompt}, {"role": "user", "content": "Output: "}]
    _, msg = message_fit_in(msg, chat_mdl.max_length)
    kwd = await chat_mdl.async_chat(rendered_prompt, msg[1:], {"temperature": 0.2})
    # ... 结果处理
    return kwd

async def content_tagging(chat_mdl, content, all_tags, examples, topn=3):
    template = PROMPT_JINJA_ENV.from_string(CONTENT_TAGGING_PROMPT_TEMPLATE)
    # ... 渲染和调用
    return res
```

### 5. 辅助方法模块（工具函数）
**作用**：提供消息长度控制、JSON 解析、历史格式化等辅助功能。
**位置**：被各 LLM 调用函数内部使用。
**配合关系**：支持核心函数的输入输出处理。

```python
def message_fit_in(msg, max_length=4000):
    def count():
        # ... token 计数逻辑
        return total
    
    c = count()
    if c < max_length:
        return c, msg
    # ... 截断逻辑
    return max_length, msg

async def gen_json(system_prompt: str, user_prompt: str, chat_mdl, gen_conf={}, max_retry=2):
    # ... JSON 生成和解析逻辑
    return res
```

---

## 三、方法详细解析（强制 5 要素 + 文字流程串讲）

### `load_prompt` 方法

**文字流程串讲**：
方法首先检查请求的模板名是否已在缓存字典 `_loaded_prompts` 中，若存在则直接返回缓存内容。若不存在，则构建模板文件路径（模块目录下的 `{name}.md` 文件），检查文件是否存在。文件存在则读取内容、去除首尾空白、存入缓存字典并返回。这种设计实现了延迟加载和缓存复用，避免重复的磁盘 I/O 操作。

**强制 5 要素**：
| 要素 | 内容 |
|------|------|
| 入参 | `name: str`（必填，模板名称，不含扩展名） |
| 核心逻辑 | 缓存检查 → 文件读取 → 缓存存储 |
| 输出形式 | `str`，模板内容字符串 |
| 底层关键依赖 | `os.path.join()`（路径拼接）；`open()`（文件读取） |
| 关键代码片段 | `_loaded_prompts[name] = content` |

---

### `keyword_extraction` 方法

**文字流程串讲**：
方法接收 LLM 模型实例、待提取内容和关键词数量参数。首先从 Jinja2 环境创建模板实例，渲染模板参数（content 和 topn）。然后构建消息列表，包含系统提示和用户提示。调用 `message_fit_in()` 确保消息长度不超过模型限制。接着调用模型的 `async_chat()` 方法执行异步对话，温度设为 0.2 以获得更确定性的输出。最后处理返回结果，去除 Markdown 代码块标记，检查是否包含错误标记，返回提取的关键词字符串。

**强制 5 要素**：
| 要素 | 内容 |
|------|------|
| 入参 | `chat_mdl: LLMBundle`（必填）；`content: str`（必填）；`topn: int`（选填，默认 3） |
| 核心逻辑 | 模板渲染 → 消息构建 → LLM 调用 → 结果处理 |
| 输出形式 | `str`，逗号分隔的关键词 |
| 底层关键依赖 | `jinja2.Environment`（模板渲染）；`chat_mdl.async_chat()`（LLM 调用） |
| 关键代码片段 | `kwd = await chat_mdl.async_chat(rendered_prompt, msg[1:], {"temperature": 0.2})` |

---

### `content_tagging` 方法

**文字流程串讲**：
方法接收 LLM 模型、待标注内容、所有标签字典、示例列表和返回标签数量。首先为每个示例添加 JSON 格式的标签字符串字段，用于提示词中的格式展示。然后渲染模板，传入 topn、all_tags、examples 和 content 参数。构建消息列表并调用 `message_fit_in()` 控制长度。调用 LLM 异步对话，温度设为 0.5 允许一定创造性。尝试使用 `json_repair` 解析返回的 JSON，若失败则尝试提取花括号内的内容重新解析。最后过滤结果，只保留值为正整数的标签，返回标签字典。

**强制 5 要素**：
| 要素 | 内容 |
|------|------|
| 入参 | `chat_mdl: LLMBundle`（必填）；`content: str`（必填）；`all_tags: dict`（必填）；`examples: list`（必填）；`topn: int`（选填，默认 3） |
| 核心逻辑 | 示例格式化 → 模板渲染 → LLM 调用 → JSON 解析 → 结果过滤 |
| 输出形式 | `dict`，标签名到权重的映射 |
| 底层关键依赖 | `json_repair.loads()`（JSON 解析）；`chat_mdl.async_chat()`（LLM 调用） |
| 关键代码片段 | `obj = json_repair.loads(kwd)` |

---

### `message_fit_in` 方法

**文字流程串讲**：
方法接收消息列表和最大 token 长度。首先定义内部计数函数，遍历消息列表计算每条消息的 token 数并累加。然后调用计数函数获取总 token 数，若未超限则直接返回。若超限，则只保留系统消息和最后一条用户消息。再次检查长度，若仍超限则根据系统消息和用户消息的长度比例决定截断哪一条。使用编码器解码器进行精确截断，确保最终消息长度不超过限制。

**强制 5 要素**：
| 要素 | 内容 |
|------|------|
| 入参 | `msg: list`（必填，消息列表）；`max_length: int`（选填，默认 4000） |
| 核心逻辑 | token 计数 → 消息筛选 → 内容截断 |
| 输出形式 | `tuple[int, list]`，实际长度和处理后的消息列表 |
| 底层关键依赖 | `num_tokens_from_string()`（token 计数）；`encoder.encode/decode()`（内容截断） |
| 关键代码片段 | `m = encoder.decode(encoder.encode(m)[: max_length - ll2])` |

---

### `gen_json` 方法

**文字流程串讲**：
方法接收系统提示、用户提示、LLM 模型和生成配置。首先尝试从缓存获取结果，若命中则直接返回解析后的 JSON。若未命中，则调用 `message_fit_in()` 控制消息长度。然后进入重试循环，最多重试 max_retry 次。每次调用 LLM 异步对话，去除 Markdown 代码块标记。尝试使用 `json_repair` 解析结果，若成功则缓存结果并返回。若解析失败，将错误信息和上次生成的内容添加到用户提示中，请求 LLM 修正。重试结束后仍未成功则返回 None。

**强制 5 要素**：
| 要素 | 内容 |
|------|------|
| 入参 | `system_prompt: str`（必填）；`user_prompt: str`（必填）；`chat_mdl: LLMBundle`（必填）；`gen_conf: dict`（选填）；`max_retry: int`（选填，默认 2） |
| 核心逻辑 | 缓存检查 → 消息构建 → LLM 调用 → JSON 解析 → 错误重试 |
| 输出形式 | `dict | list | None`，解析后的 JSON 对象 |
| 底层关键依赖 | `get_llm_cache/set_llm_cache()`（缓存）；`json_repair.loads()`（JSON 解析） |
| 关键代码片段 | `msg[-1]["content"] += f"\nGenerated JSON is as following:\n{ans}\nBut exception..."` |

---

## 四、同类逻辑对比表

| 功能名称 | 核心流程 | 入参 | 底层依赖 API | 输出格式 | 差异场景 |
|---------|---------|------|-------------|---------|---------|
| keyword_extraction | 模板渲染 → LLM 调用 | content, topn | Jinja2, async_chat | str（逗号分隔） | 关键词提取，温度 0.2 |
| question_proposal | 模板渲染 → LLM 调用 | content, topn | Jinja2, async_chat | str（换行分隔） | 问题生成，温度 0.2 |
| content_tagging | 模板渲染 → JSON 解析 | content, all_tags, examples | Jinja2, json_repair | dict（标签权重） | 内容标签，温度 0.5 |
| gen_metadata | 模板渲染 → JSON 解析 | schema, content | Jinja2, json_repair | dict（元数据） | 元数据提取 |
| sufficiency_check | 模板渲染 → JSON 解析 | question, ret_content | Jinja2, json_repair | dict（充分性判断） | 检索充分性检查 |

---

## 五、疑惑解答

**Q1: 为什么使用 Jinja2 而不是 Python f-string？**
A: Jinja2 提供了更强大的模板功能，如条件判断、循环、过滤器等，适合复杂的提示词模板。同时 Jinja2 的 `trim_blocks` 和 `lstrip_blocks` 选项可以自动处理模板中的空白，生成更干净的提示词。

**Q2: 为什么 content_tagging 的温度设为 0.5 而其他函数设为 0.2？**
A: 关键词提取和问题生成需要更确定性的输出，低温度（0.2）可以减少随机性。而内容标签需要一定的灵活性来匹配不同的标签组合，稍高的温度（0.5）允许模型有更多创造性。

**Q3: json_repair 和标准 json.loads 有什么区别？**
A: `json_repair` 可以处理 LLM 输出中常见的 JSON 格式问题，如缺少引号、多余逗号、注释等，比标准 `json.loads` 更宽容，适合解析 LLM 生成的 JSON。

---

## 六、规范修正

1. **命名规范**：`kb_prompt` 建议改名为 `format_knowledge_base_results` 更清晰表达功能
2. **错误处理**：建议在 `gen_json` 中添加更详细的错误日志，记录解析失败的具体原因
3. **缓存策略**：建议为缓存添加过期时间，避免长期运行时内存占用过大

---

## 七、可复现实操步骤

### 步骤 1：加载提示词模板
```python
from rag.prompts.template import load_prompt

# 加载单个模板
template = load_prompt("keyword_prompt")
print(template)
```

### 步骤 2：渲染模板
```python
from rag.prompts.generator import PROMPT_JINJA_ENV, KEYWORD_PROMPT_TEMPLATE

# 渲染模板
template = PROMPT_JINJA_ENV.from_string(KEYWORD_PROMPT_TEMPLATE)
rendered = template.render(content="这是一段测试内容", topn=5)
print(rendered)
```

### 步骤 3：调用关键词提取
```python
import asyncio
from rag.prompts import keyword_extraction
from api.db.services.llm_service import LLMBundle

async def main():
    chat_mdl = LLMBundle(tenant_id, chat_model_config)
    keywords = await keyword_extraction(chat_mdl, "RAG 是一种检索增强生成技术", topn=3)
    print(f"提取的关键词: {keywords}")

asyncio.run(main())
```

### 步骤 4：调用内容标签
```python
async def tag_content():
    all_tags = {"技术": 0, "产品": 0, "设计": 0}
    examples = [{"content": "这是一个技术文章", "tag": {"技术": 1}}]
    
    result = await content_tagging(
        chat_mdl, 
        "RAGFlow 是一个开源的 RAG 引擎", 
        all_tags, 
        examples, 
        topn=3
    )
    print(f"标签结果: {result}")
```

---

## 八、关键模块总览

| 模块名称 | 负责功能 | 在流程中的核心作用 |
|---------|---------|------------------|
| `template.py` | 模板加载和缓存 | 基础设施层，提供模板读取能力 |
| `load_prompt` | 单个模板加载 | 延迟加载，缓存复用 |
| `PROMPT_JINJA_ENV` | Jinja2 渲染环境 | 模板渲染引擎 |
| `keyword_extraction` | 关键词提取 | 从文本中提取关键词 |
| `question_proposal` | 问题生成 | 从内容中生成相关问题 |
| `content_tagging` | 内容标签 | 为内容分配标签权重 |
| `gen_json` | JSON 生成 | 生成结构化 JSON 输出 |
| `message_fit_in` | 消息长度控制 | 确保 LLM 输入不超限 |
| `kb_prompt` | 知识库格式化 | 格式化检索结果 |
