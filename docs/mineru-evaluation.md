# MinerU PDF 解析试验（2026-09-30）

测试原件：[The Urban Toolkit 2308.07769v1 官方 PDF](https://arxiv.org/pdf/2308.07769v1)，11 页。Paperlight 在这篇论文的官方 HTML 中漏掉标题前的首页图，并将作者脚注与 LaTeX 命令当作正文。以下只比较同一份 PDF 的解析结果，没有用 HTML 结果作为准确性依据。

| 解析器 | 环境 | 耗时 | 首页图 | 目录 | 公式与引用 |
| --- | --- | ---: | --- | --- | --- |
| 当前 Docling + Paperlight 规则 | 正式 CPU parser 容器 | 36.64 秒 | 检出，共 9 张图 | 初次把两行标题的第二行误列为第 1 章；通用标题合并规则修复后为 `1 Introduction` 起始 | 行间公式保留 PDF 裁图；编号参考文献由 PDF 文本层恢复 |
| MinerU 4.0.10 Flash | Windows 本地隔离环境 | 核心推理 7.15 秒 | 检出 | 正文章节编号正确，但把作者邮箱脚注和语法示例误标为标题 | 3 个公式块主要保留图片；74 条编号参考文献 |
| MinerU 4.0.10 Basic | 同一环境，ONNX CPU | 首次 1278 秒（含模型下载）；缓存后 48.27 秒 | 检出 | 28 个章节标题，与 PDF 的编号层次相符；作者脚注被分离 | 8 个公式块，保存识别的 LaTeX 与原始裁图；74 条编号参考文献 |

MinerU Basic 的 10 个 image 块对应 9 幅图，其中 Figure 8 被拆成两个图像块，因此块数不能直接当作图数。首页图位于 `page_0_image_4.jpg`，视觉检查确认不是空白占位。Basic 的公式 LaTeX 尚未逐字符与 PDF 对照，不能直接替换阅读器当前使用的原 PDF 裁图。该论文没有表格，不能据此判断 MinerU 的表格质量。

**判断：** MinerU Basic 在这篇论文的目录、首页图和公式结构上表现好，值得作为独立 PDF 解析服务继续试验，但单篇结果不足以替换正式解析器。它的冷启动模型下载很重；缓存后用时约 48 秒，比当前约 37 秒稍慢。下一步若接入，应把 MinerU 输出转换为 Paperlight 的 `DocumentModel`，保留原 PDF、图像、公式裁图与模型识别来源，并用有复杂表格及行内公式的论文再验证。当前网站仍用 Docling 解析新上传的 PDF，MinerU 未接入生产 Compose。

MinerU 的 [官方档位说明](https://opendatalab.github.io/MinerU/usage/tiers/)将 Basic 定位于 OCR、公式和表格，Standard 另外使用视觉语言模型；[官方 Docker 部署说明](https://opendatalab.github.io/MinerU/quick_start/docker_deployment/)的 4.0 镜像针对 NVIDIA/CUDA 环境。本试验使用本地 CPU Basic，未把模型服务暴露到网络。
