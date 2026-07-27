# 07. 参考项目调研

为避免重复造轮子，在设计本项目前调研了以下开源项目。本文档记录每个项目的定位与"我们可以借鉴什么"，供后续开发时查阅其源码/文档。

## 1. 通用 RAG / 文档问答类

### AnythingLLM — https://github.com/Mintplex-Labs/anything-llm
全栈开源、可私有部署的"与文档聊天"应用，支持任意 LLM 供应商，内置文档摄取、向量库、Agent 能力于一体，GitHub star 数很高，是该赛道最成熟的项目之一。
**借鉴点**：多 LLM/多向量库的可插拔适配层设计；"workspace"概念的产品形态（与本项目的"学习空间"高度类似）；一体化部署（docker-compose）的工程组织方式。

### Quivr — https://github.com/QuivrHQ/quivr
定位"第二大脑"，个人/企业知识管理 + RAG 问答。
**借鉴点**：多资料类型摄取的插件式架构；"brain"（知识库）维度的权限与组织方式。

### RAGFlow — https://github.com/infiniflow/ragflow
面向复杂文档（含表格、版式、图表）的深度解析 + RAG 引擎，并融合了 Agent 能力，强调"文档理解质量"。
**借鉴点**：对 PDF 中表格/版式的精细解析思路，值得在 `02-features.md` 的文档解析环节参考其分块策略；其"RAG + Agent"融合的产品定位与本项目"问答只是能力之一，教学产物才是核心"的思路相通。

### Danswer / Onyx — https://github.com/onyx-dot-app/onyx
企业知识库问答，强调多数据源连接器（Slack/Confluence/Google Drive 等）和权限继承。
**借鉴点**：连接器（connector）抽象的设计模式，对本项目"文档/网址/GitHub repo 三种资料源"的摄取抽象有直接参考价值——可以把每种资料源实现为统一 `SourceConnector` 接口下的一个实现。

## 2. AI 学习/教学场景类（与本项目定位最接近）

### EduAgent — https://github.com/StudentTraineeCenter/edu-agent
"基于 LangGraph agents 和 RAG，将静态文档转化为动态、个性化辅导环境"，利用主动回忆（active recall）原理自动生成自适应学习计划、互动测验、闪卡与语义思维导图；讲课环节采用 ReAct 模式的主动式 AI 导师。
**借鉴点**：这是与本项目目标最接近的参考实现——验证了"LangGraph + RAG"用于生成学习计划/测验/思维导图这条路线是可行的；可参考其 agent 图的节点划分方式和"主动回忆"驱动的出题策略。**差异**：本项目额外要求原生支持 GitHub 仓库作为资料源，并强调 Lecture 式的分节讲课体验，这是 EduAgent 相对较弱的部分。

### OpenTutor — https://github.com/zijinz456/OpenTutor
本地运行、自托管的"分块式（block-based）自适应学习工作区"，上传任意资料后生成笔记、测验、闪卡和自适应导师，支持 10+ LLM 供应商，支持 PDF/DOCX/PPTX 及 Canvas LMS 接入。
**借鉴点**：多 LLM 供应商适配的产品化程度高，可直接参考其 Provider 抽象；"block-based"的笔记组织形式对本项目"系统讲解"讲义的呈现方式有参考价值。

## 3. 代码仓库摄取类

### gitingest — https://github.com/coderamp-labs/gitingest
将 "github.com" 换成 "gitingest.com" 或直接用其 CLI/库，把任意 Git 仓库转换为一份对 LLM 友好的文本摘要，内置文件过滤（忽略二进制、遵循 `.gitignore`、支持大小限制）。
**借鉴点**：其过滤规则与摘要格式可直接作为本项目"GitHub repo 摄取"模块的过滤策略基线（`02-features.md` 中提到的忽略规则）；也提供了"repo → 单一文本摘要"这种轻量方案，可作为"仓库导览摘要"生成的参考实现，而不必自己从零设计过滤规则。

### repomix — https://github.com/yamadashy/repomix
把整个仓库打包成单一 AI 友好文件，支持多种输出格式（纯文本/XML/Markdown），并有 token 数统计、代码压缩（去除非关键实现细节保留结构签名）等特性。
**借鉴点**：其"代码压缩"（保留签名、省略实现）的思路，对本项目"大仓库摄取时如何在 token 预算内保留最有信息量的内容"有直接参考价值；多格式输出的设计也提示我们摄取管线应输出中间的结构化格式（而不是直接绑死 Markdown）。

## 4. 文档解析类

### Microsoft MarkItDown — https://github.com/microsoft/markitdown
将 PDF、Word、PowerPoint、Excel、图片（含 OCR）、音频（含转写）、HTML、CSV 等几乎所有常见格式转换为结构化 Markdown，专为 LLM/RAG 场景设计，保留标题、列表、表格等结构。
**借鉴点**：直接作为本项目 PDF/Word/PPT/Excel/Markdown 解析环节的首选库，避免自己写各格式解析器；其局限（扫描版 PDF、复杂多栏排版）需要留意，必要时结合 OCR 插件或 Unstructured 兜底。

### Unstructured — https://github.com/Unstructured-IO/unstructured
面向大规模文档 ETL 的解析库，40+ 数据源连接器、64+ 文件类型支持，更适合复杂/异构文档流水线。
**借鉴点**：当 MarkItDown 在某些复杂 PDF/版式上表现不佳时的备选/兜底方案；其"分区（partition）"抽象（把文档切成 Title/NarrativeText/Table 等元素类型）对本项目 Chunker 设计有参考价值。

## 5. 联网检索服务

本项目的联网检索（`02-features.md` 2.9）需要一个搜索后端，候选方案：

- **Tavily** — https://tavily.com （面向 LLM/Agent 场景的搜索 API，直接返回清洗后的正文摘要，省去自己抓取解析的工作量，LangChain 有现成集成，**建议作为默认实现**）
- **Brave Search API** — 独立索引、价格友好，返回标准搜索结果（需自行抓取正文）
- **SerpAPI** — 代理 Google/Bing 结果，覆盖面广但成本较高
- **SearXNG** — https://github.com/searxng/searxng （自托管元搜索引擎，适合私有化/离线部署场景，无第三方 API 依赖）

**借鉴点**：这些服务的返回结构差异较大，因此在本项目中统一抽象为 `SearchProvider` 接口（见 `03-architecture.md`），业务层只依赖统一的 `SearchResult` 结构，便于按部署场景切换（云端用 Tavily，私有化用 SearXNG）。

## 6. 前端 Chat UI

### assistant-ui — https://github.com/assistant-ui/assistant-ui
TypeScript/React 的 AI Chat UI 组件库，处理流式输出、消息编辑/分支、工具调用展示等复杂状态管理，提供 Radix/Base UI 风格的可定制原语组件，并原生支持接入 Vercel AI SDK 与 **LangGraph** runtime。
**借鉴点**：直接作为本项目 Chat/Lecture 前端界面的基础组件库，避免从零实现流式渲染、消息状态机等基础设施；其官方提供的 LangGraph runtime 适配器可以直接对接本项目 `06-agent-design.md` 中的 LangGraph 后端。

## 7. 综合结论：本项目的差异化定位

调研后可以确认，市面上：
- 通用 RAG 工具（AnythingLLM/Quivr/RAGFlow/Danswer）**问答能力强，但没有教学场景的产物**（学习计划/题库/Lecture）；
- 教学类项目（EduAgent/OpenTutor）**已验证"LangGraph+RAG 生成学习计划/测验"的可行性**，但对 **GitHub 仓库**这种资料源支持较弱，Lecture 式分节讲课的交互深度也有限；
- 摄取/解析类工具（gitingest/repomix/MarkItDown/Unstructured）提供了可直接复用的**基础设施能力**，本项目不需要重新发明这些轮子，而应将其作为摄取管线的构件。

因此本项目的差异化重点应放在：**（1）统一且专门优化的三源摄取（文档/网址/GitHub repo，尤其是代码仓库的结构化理解）；（2）Lecture 式的深度交互讲课体验；（3）学习计划-题库-掌握度三者的闭环联动**，而不是重新做一个"又一个 RAG 聊天工具"。
