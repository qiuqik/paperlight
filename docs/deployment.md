# 部署、Mac 兼容性与运行成本

更新：2026-10-09。下文文件大小按体积使用十进制 GB、MB 或 KB（1 GB = 1000 MB，1 MB = 1000 KB，1 KB = 1000 字节）；内存使用 GiB / MiB。实测来自 Windows 主机上的 Linux x86 Docker 容器，**不是 Mac 性能测试**。具体解析版本为 Docling 2.131.0、PyTorch 2.14.0+cpu、torchvision 0.29.0+cpu、RapidOCR 3.9.2；requirements 中部分依赖允许升级，未来重新构建的体积和速度可能变化。

## Mac 能否部署

可以使用 Docker 部署，Intel Mac 和 Apple Silicon（M 系列）都有部署路径。当前已经实测的是 Windows x86 Docker；Mac 路径根据 Docker 官方支持和依赖的 Linux ARM 安装包核验，尚未在 Mac 实机完成端到端测试。

| 设备 | 默认运行方式 | 需要了解的限制 |
| --- | --- | --- |
| Intel Mac | Linux amd64 容器 | 按根目录 Compose 启动即可；无需安装主机 Python / Node |
| Apple Silicon Mac | Web / parser 使用 Linux arm64；GROBID 使用 amd64 模拟运行 | PyTorch 与 torchvision 的当前固定版本都有 Python 3.13 Linux aarch64 安装包；其他依赖的整体 ARM 构建尚未实机验证。GROBID 已在 Compose 指定 `linux/amd64`，可能比原生运行慢 |
| Windows x86 / Linux x86 | Linux amd64 容器 | 当前性能实测环境 |

Docker Desktop 提供 Intel 和 Apple Silicon 两个安装版本；系统版本需满足其官方支持范围，详见 [Mac 安装说明](https://docs.docker.com/desktop/setup/install/mac-install/)。ARM 依赖依据：[PyTorch CPU 安装包](https://download.pytorch.org/whl/cpu/torch/)、[torchvision CPU 安装包](https://download.pytorch.org/whl/cpu/torchvision/)。

当前 Dockerfile 安装的是 CPU PyTorch。**在 Mac 的 Docker 部署中不使用 Apple GPU / Metal / MPS**，也不要求 NVIDIA 显卡。Mac 的 3D 首页由浏览器渲染，与 PDF 模型推理分开。

## Mac 首次安装

1. 安装适合芯片的 Docker Desktop，并启动它。
2. 下载或克隆本项目，在终端进入项目根目录。完整部署建议 Docker 虚拟机分配 10–12 GiB 内存、至少 4 个 CPU 核，磁盘预留 20–30 GB；这是资源预算建议，不是实测最低配置。
3. 按 [README 的准备环境步骤](../README.md#deepseek-api-配置必备) 配置 DeepSeek API key，获得发表信息查询与公式辅助转写的完整体验；然后启动服务并创建管理员：

```sh
docker compose up --build -d
docker compose exec parser python -m app.bootstrap_admin --username admin
```

按提示输入两遍管理员密码，再打开 `http://localhost:8040`。普通注册仅创建普通用户；首位管理员通过这条服务器命令创建，不存在公共默认密码。已有普通用户不会阻止初始化；已有管理员会阻止重复初始化。

首次构建需访问系统软件源、PyPI、PyTorch 安装源和容器镜像仓库。首次解析还需下载 Hugging Face 模型；扫描件的 RapidOCR Torch 权重来自 ModelScope。首次部署等待时间主要由网络决定，本文没有测量 Mac 的构建或下载时长。

### Apple Silicon 构建失败时的兼容路径

若某个 ARM 依赖没有合适的安装包，可强制 parser / MinerU 使用经过 x86 测试的架构：

```sh
docker compose -f docker-compose.yml -f docker-compose.mac-compat.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.mac-compat.yml exec parser python -m app.bootstrap_admin --username admin
```

该配置保留 Web 原生架构，parser 与 GROBID 通过 x86 模拟运行。模拟运行有额外性能代价，不能套用本文的 x86 实测速度。以后启动、构建、停止同一部署时继续带上这两个 `-f` 参数；MinerU 可选 profile 也能叠加使用。

## 需要多少内存和磁盘

| 场景 | 建议主机内存 | Docker / 服务器可用内存 | 空闲磁盘预算 |
| --- | ---: | ---: | ---: |
| 个人使用、关闭 GROBID 的轻量部署 | 8 GB 可以尝试，16 GB 更稳妥 | 6–8 GiB | 15–20 GB 起 |
| 默认完整部署 | 16 GB 起；复杂扫描件或多用户建议 24–32 GB | 10–12 GiB 起 | 20–30 GB 起 |
| 加装 MinerU Basic | 16 GB 起，建议 24 GB 或以上 | 至少在完整部署上增加余量，未测并行峰值 | 额外预留约 5 GB |

这些是根据下方实测给出的预算，**不是最大内存保证**。大页幅、高分辨率扫描件、图表很多的 PDF 和同时进行的后台任务可能使用更多内存。当前上传解析会排队，不能按“用户数量 × 每分钟固定篇数”估算吞吐。纸面页数也不能单独决定耗时。

2026-10-09 容器空闲快照：Web 约 40 MiB，parser 尚未推理时约 55 MiB，GROBID 约 **3.35 GiB**。parser 在本次连续解析中记录的进程最高 RSS 为 **4009 MiB（约 3.92 GiB）**。这是单进程累计高水位，不是整个 Docker 虚拟机的峰值；包含 Python 与模型内存，不能解释成权重大小。加载模型后的空闲 parser 也可能比启动时占用更多内存。

### 轻量部署：不启动 GROBID

如果主要处理有文字层、带编号参考文献的论文，可以用 PDF 自身文本恢复参考文献，减少约 3.35 GiB 的常驻 GROBID 开销。首次部署在 Mac / Linux 终端运行：

```sh
PAPERLIGHT_REFERENCE_MODE=fast docker compose up --build --no-deps -d parser web
docker compose exec parser python -m app.bootstrap_admin --username admin
```

`--no-deps` 跳过 GROBID；命令显式启动 parser 和 Web。若已运行完整部署，先 `docker compose stop grobid`。为后续启动保持相同模式，可在根目录 `.env` 写入 `PAPERLIGHT_REFERENCE_MODE=fast`，启动时仍需 `--no-deps`，否则普通 Compose 启动会再次拉起 GROBID。

代价是未编号、图片形式或难以从 PDF 文本层恢复的参考文献可能不完整。`fast` 模式仍执行 Docling、图表与公式裁图，不调用 GROBID。扫描件 OCR 仍会运行。

## 模型模块：每个多大、是否必需

下表是本机实际模型文件大小。权重不会提交到 GitHub，部署时由依赖或服务下载；GROBID 模型随其镜像提供。模型名称来自实际文件 / 配置，不把软件包、容器镜像或 3D GLB 当作 AI 权重。

| 模块 / 模型 | 权重大小 | 默认情况 | 用途与耗时口径 |
| --- | ---: | --- | --- |
| Docling Layout Heron | 172 MB | 启用 | 页面版面与块类型识别；计入 `doclingSeconds` |
| TableFormer Accurate | 213 MB | 启用 | 表格结构；计入 `doclingSeconds`，无表格页面也仍经过页面处理 |
| TableFormer Fast | 145 MB | 未选用，但本机历史缓存含有 | 可选表格模型；没有单独测速 |
| RapidOCR PP-OCRv6 检测 Torch | 10.25 MB | 仅需要 OCR 时启用 | 扫描页面文字区域检测 |
| RapidOCR 方向分类 Torch | 590 KB | 仅需要 OCR 时启用 | 文字方向分类 |
| RapidOCR PP-OCRv6 识别 Torch | 21.33 MB | 仅需要 OCR 时启用 | 文字识别；三个 Torch 权重合计约 **32 MB** |
| RapidOCR 随包 ONNX 权重 | 31.75 MB 合计 | 当前 parser 自动选择 Torch；这些文件随包存在 | 检测 9.93 MB、方向 590 KB、识别 21.23 MB；不另算成正在使用的第二套 OCR |
| GROBID CRF 模型目录 | 约 460 MB | 服务默认启动，`auto` 下按需调用 | 文献、作者、标题等结构提取；文件大小包含模型目录配套文件；历史调用约 39–342 秒，包含服务等待与文献信息整合 |
| Docling CodeFormulaV2 | 权重 631 MB；含 tokenizer / 配置约 640 MB | **未启用**，本机历史缓存含有 | 当前 `do_formula_enrichment=False`；原公式以 PDF 裁图显示，不要求下载该模型；没有独立测速 |
| DeepSeek 云端元数据 / 视觉转写 | 本地 0 MB | 仅配置 API key 时启用 | 发表信息查询、公式辅助转写；按服务商 API 计费，与本地正文解析分开 |
| 豆包云端视觉转写 | 本地 0 MB | 仅配置 key 和支持的视觉模型时启用 | 公式辅助转写；费用、模型与网络耗时依部署者选择 |

默认核心 Docling 权重（Layout + Accurate + Torch OCR）合计约 **417 MB**；另外有随包 ONNX 文件、配置文件，以及镜像内的 GROBID 模型。不要据此认为完整程序只占 417 MB，运行库和镜像占用见下节。

普通文本 PDF 先关闭 OCR 解析，文字不足时再执行 OCR 转换。因此扫描件可能经过两轮转换。当前 RapidOCR 自动选择 Torch 后端，首次扫描件额外下载约 32 MB 的 Torch 权重。本次下载初始化约 38 秒，随后扫描件测速已使用下载好的权重。Torch OCR 文件目前写在 parser 容器内的 Python 包目录，**重建 / 重建容器后可能重新下载**，不属于 `/models` 的持久卷。

Docling 版面、表格、OCR 共用转换流程，现有计时没有把每个神经网络单独拆开；不能把整篇论文耗时任意分摊给每个模型。下方提供联合转换时间与整条本地解析流程时间。GROBID 可与 Docling 同时执行，其计时也不能简单相加。

### 可选：MinerU Basic CPU

当前默认不启动 MinerU，也不下载其模型。若需要试验，在根目录 `.env` 中设置 `PAPERLIGHT_PDF_PARSER=mineru` 后运行：

```sh
docker compose --profile mineru up --build -d
```

只有启动 profile 并选择解析器两者都完成，新 PDF 才走 MinerU。选用 MinerU 后，故障会回退到 Docling。缓存目录为 `backend/storage/mineru/`；保留它避免反复下载。

| MinerU Basic 子模块 | 实测权重大小 |
| --- | ---: |
| PP-DocLayoutV2 | 214 MB |
| PP-FormulaNet Plus M | 591 MB |
| OCR 检测 | 1.78 MB |
| OCR 识别 | 21.16 MB |
| 表格分类 PP-LCNet | 6.78 MB |
| 表格结构 SLANet Plus | 7.76 MB |
| 表格 UNet | 8.34 MB |
| **权重合计 / 实际缓存目录** | **852 MB / 853 MB** |

2026-09-30 的历史测试：同一篇 11 页论文，Basic 本地 ONNX CPU 核心解析，缓存后 **48.27 秒**，首次含下载 **1278 秒（21.3 分钟）**。它是 Windows 隔离环境的旧测试，不是当前 Docker / Mac 整条导入流程测速，也不能表示所有论文速度。Flash 曾测到 7.15 秒，但不是本项目默认路径。详见 [MinerU 历史试验](mineru-evaluation.md)。Standard / 视觉语言大模型没有启用、下载或测试，本项目不提供它们的体积与速度承诺。

## 镜像、缓存和数据占用

| 项目 | 本机实测体积 | 说明 |
| --- | ---: | --- |
| parser 运行镜像 | 3.002 GB | Python、CPU PyTorch、Docling 等；不含 `/models` 持久卷 |
| Web 运行镜像 | 447 MB | Next.js、Node 与静态资源 |
| GROBID CRF 镜像 | 1.717 GB | 已含上表的约 460 MB 模型目录，不要重复相加 |
| 三个默认镜像逻辑大小合计 | 5.167 GB | Docker 共享层可能减少实际占用；这是解压后镜像大小，**不是网络下载量** |
| Docling 当前历史模型缓存卷 | 1.872 GB | 含未启用的公式 / Fast 模型及重复缓存文件，不能当作全新部署的必需权重下载量 |
| 可选 MinerU 镜像 | 3.71 GB | 与 parser 共享部分构建层，不能把整个镜像都算作净新增磁盘 |
| 可选 MinerU 模型缓存 | 853 MB | 默认不需要 |
| 测试的 20 篇论文文档目录 | 278 MB | 含原 PDF、解析 JSON 和图片；原 PDF 合计 145 MB；不含额外调试快照 |
| 本机全部 `userdata/` | 686 MB | 包括其他已有论文和账号数据，非空库基线 |
| 本机全部 `result/` | 约 2.86 GB | 含历史测试和调试产物，非 20 篇论文独占 |

源码构建还会生成中间层、包下载和构建缓存；Docker 虚拟磁盘也有额外开销，所以预算明显大于运行镜像之和。全新默认部署的核心 Layout + Accurate 首次模型下载约 384 MB，OCR Torch 首次用时另加约 32 MB；上游版本变化或额外启用模型会改变下载量。首页 GLB 约 3.84 MB，无需 AI 推理服务。

数据增长跟图片分辨率、图表数量和原 PDF 大小有关。上述 20 篇样本平均文档目录约 14 MB / 篇，仅用于了解本批样本；不能保证扫描 PDF 也只有这么大。保留 `userdata/` 做数据备份，`result/` 是另计的调试存储；升级时保留模型卷，避免重新下载。

## 实际推理和解析时间

### 2026-10-09 当前版本复测

环境：Intel Core i7-8700K，6 核 / 12 线程，主机约 32 GiB 内存；Docker 可见 12 个 CPU、15.59 GiB 内存。模型已下载，CPU 推理；三篇文本 PDF 在同一个独立 Python 进程中顺序转换，输出写临时目录，未改动论文库。计时从 `parse_pdf` 调用到返回，包含模型初始化、结构识别、图表与公式裁图修复，**不含上传、入库、发表信息查询和后台云端公式转写**。

| 样本 | Docling 联合转换 | 本地解析总耗时 | 是否 OCR | 进程累计最高 RSS |
| --- | ---: | ---: | --- | ---: |
| 11 页文本 PDF | 31.22 秒 | 38.39 秒 | 否 | 2089 MiB |
| 23 页文本 PDF | 61.45 秒 | 79.28 秒 | 否 | 3447 MiB |
| 45 页文本 PDF | 66.07 秒 | 73.23 秒 | 否 | 4009 MiB |
| 一页模拟扫描 PDF | 38.03 秒 | 42.28 秒 | 是 | 2540 MiB（独立进程） |

文本 PDF 三个样本均从 PDF 恢复参考文献，未调用 GROBID；扫描样本关闭 GROBID。扫描样本是把同一篇文本论文首页按 2× 渲染为图片后写入无文字层 PDF，不代表真实扫描噪声、中文 OCR 或长篇扫描件的性能。自动 OCR 确实触发，输出约 2498 个文本字符；没有评估逐字识别准确率。

23 页比 45 页慢，是因为文档结构和裁图修复工作量不同。这里没有为 Mac 推导倍速，也没有把一页扫描耗时直接乘页数：只有 Mac 实机测试才能给出其可信吞吐量。

### 20 篇、338 页历史导入记录

读取此前这批论文保存的 `status.json`：20 篇全部 ready，`totalSeconds` 中位数 **35.02 秒**，最短 **20.91 秒**，最长 **384.46 秒（6.41 分钟）**；20 篇记录耗时相加为 **1463.16 秒（24.39 分钟）**。这只是逐篇计时总和，不等于整批上传的实际墙钟时间。

历史样本是此前版本的实际导入，和今天的直接转换复测口径不同。较慢样本包括 34 页论文的 Docling 阶段约 360 秒，以及一篇论文 GROBID 阶段约 342 秒。其余文本论文大多在几十秒级。历史后台公式转写记录约 5–16 秒，与正文解析分开；它只覆盖部分样本，不能作为云端服务稳定速度保证。

`grobidSeconds` 从提交服务到拿到结果计时，包含等待和文献信息整合，并非纯 CRF 神经 / 统计模型内核耗时。云端元数据请求没有完整、可比较的延迟测量，本文不填造秒数。

本次匿名化测量数据见 [deployment-benchmark.json](deployment-benchmark.json)，不包含用户账号、路径、原始论文或 API 密钥。

## 费用怎么计算

- 本地 CPU PDF 结构解析不调用付费 LLM API，也不要求购买显卡；这部分没有按篇 API 费用，仍有电费、存储和带宽成本。完整使用体验需要配置 DeepSeek，其辅助调用费用另计。
- Docker Desktop 的个人、教育、非商业开源及满足条件的小企业用途可免费，其他商业场景可能需要订阅，见 [官方许可说明](https://docs.docker.com/subscription-billing/desktop-license/)。
- 完整部署需配置 DeepSeek API，用于在导入 / 打开论文时执行元数据查询、公式辅助转写；豆包为另外支持的视觉转写服务。费用由实际模型、输入输出 token、搜索工具、重试和供应商价格决定，本文没有账单实测，不能承诺每篇固定价格。
- API 预算可按“请求输入 token × 输入单价 + 输出 token × 输出单价 + 搜索 / 其他工具费”计算，并把重试计入。云端只提供辅助信息，阅读原 PDF / 本地解析不依赖它们。
- 租服务器时，按上述 CPU、内存和磁盘预算询价；主机、备份、域名与外网流量是独立费用。本项目没有绑定云厂商，也没有可靠报价，故不写虚构月租。
- 电费估算为“平均功率 W ÷ 1000 × 开机小时数 × 当地每 kWh 电价”。例如自行假设 60 W、每天 8 小时、30 天、0.6 元/kWh，结果是 8.64 元/月；**这不是 Paperlight 的实测功耗**，也不含整机购买成本。

## 自己部署后如何核对

```sh
docker compose ps
docker stats --no-stream
docker system df
docker compose logs --tail 100 parser
```

`docker stats` 是运行时快照，`docker system df` 区分镜像、容器、卷和构建缓存。`docker compose ps` 只说明进程状态，不代表模型已下载或解析功能已验证；首次应上传一篇小 PDF，确认进入 ready 后能读正文与图片。每篇文档目录的 `status.json` 保存 `doclingSeconds`、`normalizingSeconds`、`grobidSeconds`、`totalSeconds` 等计时，可作为本机实测依据。正常保留持久卷及用户目录；不要为清理缓存误删账号和论文。