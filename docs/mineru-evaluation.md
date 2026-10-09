# MinerU PDF 解析试验（2026-09-30）

> 2026-10-09 更新：当前正式 Compose 默认使用 Docling；MinerU 已调整为可选实验 profile。下文保留当时的试验记录，当前部署方式和成本以 [部署说明](deployment.md) 为准。

测试原件：[The Urban Toolkit 2308.07769v1 官方 PDF](https://arxiv.org/pdf/2308.07769v1)，11 页。Paperlight 在这篇论文的官方 HTML 中漏掉标题前的首页图，并将作者脚注与 LaTeX 命令当作正文。以下只比较同一份 PDF 的解析结果，没有用 HTML 结果作为准确性依据。

| 解析器 | 环境 | 耗时 | 首页图 | 目录 | 公式与引用 |
| --- | --- | ---: | --- | --- | --- |
| 当前 Docling + Paperlight 规则 | 正式 CPU parser 容器 | 36.64 秒 | 检出，共 9 张图 | 初次把两行标题的第二行误列为第 1 章；通用标题合并规则修复后为 `1 Introduction` 起始 | 行间公式保留 PDF 裁图；编号参考文献由 PDF 文本层恢复 |
| MinerU 4.0.10 Flash | Windows 本地隔离环境 | 核心推理 7.15 秒 | 检出 | 正文章节编号正确，但把作者邮箱脚注和语法示例误标为标题 | 3 个公式块主要保留图片；74 条编号参考文献 |
| MinerU 4.0.10 Basic | 同一环境，ONNX CPU | 首次 1278 秒（含模型下载）；缓存后 48.27 秒 | 检出 | 28 个章节标题，与 PDF 的编号层次相符；作者脚注被分离 | 8 个公式块，保存识别的 LaTeX 与原始裁图；74 条编号参考文献 |

MinerU Basic 的 10 个 image 块对应 9 幅图，其中 Figure 8 被拆成两个图像块，因此块数不能直接当作图数。首页图位于 `page_0_image_4.jpg`，视觉检查确认不是空白占位。Basic 的公式 LaTeX 尚未逐字符与 PDF 对照，不能直接替换阅读器当前使用的原 PDF 裁图。该论文没有表格，不能据此判断 MinerU 的表格质量。

**历史试验决定（已调整）：** 新上传的 PDF 默认交给 Compose 中独立的 MinerU Basic CPU 服务解析，再将 MiddleJson 转换成 Paperlight 的 `DocumentModel`。原 PDF、MinerU 的结构化结果、图片和公式裁图保存在该用户自己的文档目录；行间公式优先显示裁图，MinerU 识别的 LaTeX 仅作辅助转写。无法取得 MinerU 图片素材时，用 PDF 原区域裁图；包含 MinerU 行内公式片段的段落保留原 PDF 整段视觉呈现，以免误排上下标。文献编号会连接到参考文献栏。MinerU 服务故障时回退到 Docling，并在文档提示中标明。已有文档不会自动重解析，原笔记仍关联原文档；重新上传一篇此前用 Docling 解析的相同 PDF 会生成单独的 MinerU 文档。Basic 缓存后约 48 秒，比此前 Docling 约 37 秒稍慢；复杂表格和行内公式仍需更多论文验证。

生产 Compose 不向外暴露 MinerU 端口，只允许 parser 容器调用。模型缓存位于 `backend/storage/mineru`，与用户文档分开。重新构建时需保留这个目录，否则首次解析可能下载约 0.8 GB 的模型并显著延长耗时。可通过 `PAPERLIGHT_PDF_PARSER=docling` 临时切回旧解析器，故障时的自动回退也仍保留。

MinerU 的 [官方档位说明](https://opendatalab.github.io/MinerU/usage/tiers/)将 Basic 定位于 OCR、公式和表格，Standard 另外使用视觉语言模型；[官方 Docker 部署说明](https://opendatalab.github.io/MinerU/quick_start/docker_deployment/)的 4.0 镜像针对 NVIDIA/CUDA 环境。本试验使用本地 CPU Basic，未把模型服务暴露到网络。
