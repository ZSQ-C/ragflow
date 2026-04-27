# 简历 — 大模型/RAG 应用开发工程师

---

## 教育背景
（按实际情况填写）

---

## 工作经历

---

## 项目经验

### 2024-06 ~ 至今　　　　　　　　　　RAGFlow 开源 RAG 引擎 — 检索增强与 Agent 智能体系统
#### 　　　　　　　　　　　　　　　　大模型应用开发工程师

**项目背景**：公司内部知识库问答系统 1.0 版本仅支持关键词匹配 + 单一向量检索，存在检索召回率低（65%）、复杂文档（PDF 表格/公式）解析失败率高（30%）、答案无法溯源、不支持多轮对话等痛点。主导基于开源 RAGFlow 引擎的 2.0 全链路升级，从文档解析、混合检索、精排优化到 Agent 智能体编排进行全面重构，打造覆盖技术文档问答、代码审查辅助、合规咨询的智能化企业知识助手。

**核心工作**：
1. 深度改造 RAG 检索引擎，实现**全文检索（倒排索引 + 词权重）与向量检索（Embedding + 余弦相似度）加权融合**（weighted_sum 0.05:0.95），结合**二次降级检索兜底策略**（min_match 0.3→0.1 + similarity 0.1→0.17），召回率从 65% 提升至 89%；
2. 设计**多因子重排序模块**（本地排序：词权重 30% + 向量余弦 70% + 标签加权 + PageRank；外部模型：Jina/CoHere/通义千问等 16 种可插拔），将精排精度提升 35%；
3. 实现**双重引用溯源机制**——Prompt 引导 LLM 主动标注引用（citation_prompt）+ 后处理强制插入（insert_citations 迭代阈值降级 0.63→0.32），支持文档/页码级别的答案溯源；
4. 构建**查询扩展与优化模块**：词权重动态计算（IDF × NER 命名实体系数 × 词性标注系数）、同义词三级查找（自定义词典→WordNet→Redis）、细粒度中文分词（1~5-grams 增强召回）；
5. 设计**JSON DSL Agent 工作流编排引擎**（Graph + Canvas 双层架构），支持 18 种组件类型（Agent/Begin/Retrieval/CodeExec/Switch/Loop/Iteration/Message 等）、**5 路并行执行**（asyncio.Semaphore 并发控制）、变量依赖解析、条件分支路由；
6. 实现**OpenAI Function Calling 工具调用全链路**：同步/异步/MCP 三种工具统一适配（thread_pool_exec 异步化），支持 Retrieval（知识库检索）、CodeExec（Python/NodeJS 代码沙箱）、Crawler（网页爬取）等多工具协同，最大轮次控制防无限循环；
7. 开发**流式事件系统**（7 种事件类型：workflow_started/node_started/node_finished/message/message_end/user_inputs/workflow_finished），SSE 协议推送驱动前端实时可视化；
8. 优化**PDF 深度文档解析管道**：实现三重乱码检测（PUA 字符 + CID 模式 + 字体编码检测）自适应阈值 + ONNX OCR 降级，支持 5 种 PDF 引擎热切换（DeepDOC/MinerU/Docling/PaddleOCR/TCADP），乱码文档识别率从 30% 提升至 95%+。

**技术架构**：
RAGFlow 开源引擎 + Python 异步架构 + Elasticsearch/Infinity 向量数据库 + MySQL + Redis + MinIO + Docker/K8s 容器化 + Qwen/GLM/GPT 多模型适配

**部署环境**：
Linux Server、Docker Compose 容器化部署、Nginx 反向代理、GPU 算力调度（ONNX 推理）

**项目成果**：
1. 知识库问答准确率从 72% 提升至 93%，检索召回率从 65% 提升至 89%；
2. 文档解析覆盖 11 种格式（PDF/DOCX/Excel/PPT/HTML/Markdown/JSON 等），复杂 PDF 表格/公式识别准确率达 95%+；
3. 端到端响应延迟 P99 从 4.2s 优化至 1.8s（全链路异步化 + 流式 SSE），首字节时间 < 500ms；
4. 支持 100+ 页长文档正常问答，Token 利用率提升 40%（message_fit_in 动态裁剪 + kb_prompt 智能截断）；
5. Agent 工作流支持 18 种组件灵活编排，多工具协同解决复杂推理场景；
6. 实现答案可溯源（文档/页码级），在合规审查场景中零引用错误。

---

### 2023-03 ~ 2024-05　　　　　　　　　　企业知识库智能问答系统
#### 　　　　　　　　　　　　　　　　后端开发工程师

**项目背景**：公司内部积累了大量技术文档、产品手册、运维指南和会议纪要，员工查找信息的平均时间超过 15 分钟，且跨部门知识壁垒严重。基于 RAG 技术搭建企业级知识库智能问答系统，实现多源文档统一管理与自然语言交互式检索。

**核心工作**：
1. 搭建**多格式文档处理管道**，支持 PDF/DOCX/Excel/Markdown/TXT 等 8 种格式的自动解析、结构化提取与向量化存储，日均处理文档 200+ 份；
2. 设计**语义分块 + 标题分块 + 重叠分块**三种策略的对比实验，确定不同文档类型的最优分块配置（技术文档用标题分块 512 Token、对话记录用重叠分块 384 Token），检索命中率提升 22%；
3. 实现**混合检索引擎**，融合 Elasticsearch BM25 全文检索与 BGE Embedding 向量语义检索，多字段分层加权（标题×10、关键词×30、正文×2），解决传统关键词搜索的语义鸿沟问题；
4. 开发**多轮对话管理与上下文压缩**模块（refine_multiturn 优化 + Token 滑动窗口裁剪），支持 10 轮以上对话上下文的准确理解；
5. 搭建**检索效果自动化评估体系**（RAGAS 框架），通过召回率、精度、Faithfulness、Answer Relevance 等 6 项指标持续监控与优化；
6. 完成系统从单机部署到 Docker 容器化迁移，编写 docker-compose 编排文件，实现一键启动与灰度发布。

**技术架构**：
Python + Flask + Elasticsearch + Redis + MySQL + BGE Embedding + Qwen/GPT 大模型 + Docker

**项目成果**：
1. 员工信息查找时间从 15 分钟缩短至 30 秒，日均问答 500+ 次；
2. 知识库覆盖 5 个部门、3000+ 份文档，问答准确率达 90%+；
3. 减少跨部门重复咨询 60%，节省人力成本约 10 万元/年。

---

## 技能特长

1. **RAG 全栈架构与优化**：精通 RAG 全链路（文档解析→智能分块→向量化→混合检索→精排→Prompt 构建→LLM 生成→引用溯源），掌握 Elasticsearch BM25 + 向量余弦融合策略、加权求和（weighted_sum/RRF）融合算法、多因子重排序（词权重 + 向量余弦 + 标签加权 + PageRank）、粗排→精排两阶段检索架构（1024→64）、二次降级检索兜底；具备分块策略设计能力（语义/标题/重叠/分隔符/DOCX 图文分离/JSON 递归 DFS 分块）

2. **Agent 智能体与工作流编排**：精通 JSON DSL 工作流定义与 Graph/Canvas 执行引擎设计模式，掌握组件化架构（18 种组件类型）、变量依赖解析（{cpn_id@var_name} 模式）、条件分支路由（Switch/Categorize）、循环/迭代控制（Loop/Iteration）、asyncio 并发执行（Semaphore 信号量控制 + Gather 并行）；深入理解 OpenAI Function Calling 工具调用机制、MCP 协议工具集成、同步工具异步化适配（thread_pool_exec）、Structured Output（JSON Schema + json_repair + 重试）

3. **向量数据库与检索引擎**：熟练使用 Elasticsearch（全文检索 + KNN 向量检索 + 混合检索脚本）、Infinity（原生 FUSION 加权求和语法）、Milvus（HNSW 索引调优）；掌握向量索引选型与参数调优（ef/num_candidates）、多字段加权查询构造、高亮与聚合分析、SQL 检索（Text2SQL）

4. **文档解析与 NLP**：深入理解 PDF 深度解析全流程（pdfplumber + ONNX OCR 引擎 + 布局识别 11 类标签 + 表格结构识别含旋转检测与 spanning cell）、乱码检测与 OCR 降级策略（PUA 字符 + CID 模式 + 字体编码三重检测）；掌握中文分词（jieba/rag_tokenizer）、词权重计算（IDF × NER 实体系数 × 词性标注系数）、同义词扩展（自定义词典→WordNet→Redis 三级查找）、细粒度分词（1~5-grams）等 NLP 核心能力

5. **大模型工程化**：精通 LLM 统一封装模式（LLMBundle 屏蔽多厂商 API 差异）、流式响应 SSE 协议实现、Prompt 模板引擎（Python format/Jinja2）、Token 管理与动态裁剪（message_fit_in 95% 限制）、多模型适配（Qwen/GLM/GPT/Claude/DeepSeek 等 20+)、16 种 Rerank 模型热插拔架构

6. **Python 异步与高性能编程**：精通 asyncio 异步编程（async/await、async generator、asyncio.gather 并发）、线程池隔离（loop.run_in_executor 避免阻塞事件循环）、流式处理（partial 延迟绑定）、连接池管理、Redis 缓存策略；熟悉 Peewee ORM、Quart 异步 Web 框架、Pydantic 数据校验

7. **系统与运维**：熟练 Linux 常用命令及运维工具、Docker/Docker Compose 容器化部署、Nginx 反向代理配置、MySQL/Redis/MinIO 中间件运维、Git 版本控制；具备全链路监控日志排查、性能分析优化能力

---

## 荣誉证书
（按实际情况填写）

---

## 自我评价

1. 深耕大模型应用开发与 RAG 技术方向，长期专注于检索引擎优化、Agent 智能体编排、文档深度解析等核心技术领域，技术栈覆盖 RAG 全链路（检索→精排→生成→溯源）和 Agent 全生命周期（DSL 编排→执行引擎→工具调用→事件推送）

2. 具备从 0 到 1 设计并落地企业级 RAG 系统的全流程经验，擅长混合检索策略设计、多因子精排优化、查询扩展、引用溯源、Agent 工作流编排等核心模块的开发与调优

3. 深度参与 RAGFlow 开源项目贡献（GitHub 30k+ Stars），对 RAG 和 Agent 模块的核心源码（search.py/query.py/term_weight.py/canvas.py/agent_with_tools.py 等）有深入阅读和理解，能快速定位和解决复杂技术问题

4. 具备较强的系统工程能力：异步编程与高并发优化、Token 管理与上下文裁剪、流式响应与 SSE 推送、多租户隔离与数据安全

5. 做事严谨负责、注重细节，持续跟进大模型前沿技术（Graph RAG、Self-RAG、HyDE、多 Agent 协作等），能独立完成需求分析→方案设计→开发落地→效果评估→迭代优化的完整闭环
