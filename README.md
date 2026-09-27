# Paperlight · 结构化论文阅读器

## v2 阅读器

新版阅读器位于 `apps/web/`，使用 Next.js、React 和 TypeScript。它读取现有 Python Parser API 返回的 Document Model；文档先解析为 JSON，再按段落、图片、表格和公式等块渲染。旧静态网页仍保留在 `index.html` 和 `dist/`，用于兼容已有部署。

本机开发先启动解析服务，再启动阅读器：

```powershell
cd backend
docker compose up --build -d
cd ..
npm install
npm run dev
```

新版网页地址为 `http://127.0.0.1:8040`。示例论文无需解析服务即可阅读；导入 PDF、服务器历史与跨设备批注需要解析服务。解析服务地址默认 `http://127.0.0.1:8000`，可通过 `PAPERLIGHT_PARSER_URL` 指定。浏览器只访问同源的 `/api/parser`，由 Next.js 转发到内部解析服务。

若要用单个 Docker Compose 启动新版网页、解析服务和 GROBID，在项目根目录运行：

```powershell
docker compose up --build -d
```

网页默认只监听本机的 `127.0.0.1:8040`。如果不使用 Tailscale 等本机 HTTPS 转发，而要直接从其他设备连接端口，可在根目录的 `.env` 中设置 `PAPERLIGHT_WEB_BIND=0.0.0.0`，并在外层配置 HTTPS 与访问保护。解析服务和 GROBID 只在 Compose 内部网络通信。数据保存在 `userdata/`，解析快照保存在 `result/`。

如果其他设备已加入同一个 Tailscale 私网，可保持网页只监听 `127.0.0.1:8040`，在 Windows 主机上运行 `tailscale serve --bg --https 443 http://127.0.0.1:8040`。Mac 直接打开 Tailscale 给出的 `https://<主机名>.<tailnet>.ts.net/`；浏览器的文档请求由 Next.js 在容器内部转发到 parser，无需另行暴露 parser 或 GROBID。当前 `desktop-c0d40qs.tailc57931.ts.net` 的首页已经按此方式转发。开发时可用本机 `npm run dev` 提供 8040；长期运行建议关闭开发服务，改由根目录的 Compose 提供同一端口，Tailscale 地址不需要改变。Windows 主机睡眠、Docker Desktop 停止或 Tailscale 断开时，远程页面也会不可用。

新版交互包括顶部工具模式、选中文字后直接高亮或下划线、选中后立即创建并聚焦笔记、图表区域标记、左右面板、专注模式、预设或自定义主题及排版设置。批注与文档缓存在当前浏览器的 IndexedDB，排版偏好在当前浏览器持续保存；服务器文档与批注通过 Parser API 同步。各浏览器和来源地址的本地历史互不相通，旧版 `8765` 网页的浏览器数据不会自动出现在新版 `8040` 网页中。

把服务器上的 PDF 放入 `userdata/library/` 或其子目录，即可在「历史记录 → 服务器 → 服务器文件库」中打开。网页只传文件库生成的标识，不接受任意服务器路径；打开后按 PDF 指纹复用已有解析结果。单独运行 Python 解析服务时，可用 `PAPERLIGHT_LIBRARY_DIR` 指定文件库根目录。

当前解析仍由 Docling 与 GROBID 完成。新版会保存 PDF 块的页码、边界框和最终阅读顺序；公式识别不可靠时使用原 PDF 裁图。更完整的列检测和语义解析仍需后续数据集验证。

## 旧版静态网页

Paperlight 把论文 PDF 转成适合连续阅读的网页：左侧章节目录、中间正文与图表、右侧参考文献。读者可以点击正文中的引用和图表编号跳转，并调整字号与阅读宽度。

项目提供两种解析路径：

- **浏览器本地解析**：静态网页使用 PDF.js 提取有文字层的 PDF，无需服务器即可试用。复杂双栏、扫描页和表格的识别能力有限。
- **Python Parser API**：FastAPI 调用 Docling 提取结构、图表和阅读顺序，调用 GROBID 补充论文元数据及引用；GROBID 不可用时，使用 PDFium 恢复编号参考文献。图像按 Docling 的边界框从原 PDF 高分辨率裁切。上传的 PDF 与解析结果保存在服务器本地。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `index.html`, `src/` | 阅读器源码与浏览器解析器 |
| `dist/` | 可直接托管的静态网页 |
| `backend/app/` | FastAPI 与文档标准化逻辑 |
| `backend/docker-compose.yml`, `backend/.env.example` | API 与 GROBID 容器配置及端口示例 |
| `userdata/documents/` | 上传的 PDF、解析结果、图像和笔记；不提交到 Git |
| `result/` | 解析调试快照与批量测试报告；不提交到 Git |

## 先在 Windows 本机运行

建议先用 Docker Desktop（WSL 2 后端）启动 API 和 GROBID。在 PowerShell 执行：

```powershell
cd backend
Copy-Item .env.example .env
docker compose up --build -d
curl.exe http://127.0.0.1:8000/health
```

默认 API 只监听 Windows 本机的 `127.0.0.1:8000`，GROBID 只在容器网络内供 API 使用。要改 Windows 端口，在 `backend/.env` 中修改 `PAPERLIGHT_API_PORT`；要让其他机器直连，才将 `PAPERLIGHT_BIND_HOST` 改为 `0.0.0.0`，并配置防火墙及访问保护。`PAPERLIGHT_CORS_ORIGINS` 填浏览器网页的来源地址，多个用逗号分隔。修改 `.env` 后执行 `docker compose up -d --force-recreate`。

第一次启动会下载 Docling 模型与 GROBID 镜像，需要较长时间和数 GB 磁盘空间。Docker 配置使用 CPU 版 PyTorch 和 GROBID CRF 镜像；CRF 版镜像较小，但部分引用和元数据识别准确率低于 GROBID full 版。健康检查中的 `doclingAvailable` 应为 `true`。API 文档位于 `http://127.0.0.1:8000/docs`（改端口后同步替换）。日志可用 `docker compose logs -f api` 查看；停止服务用 `docker compose down`，不会删除已解析的文件。

如果希望直接在 Windows 上运行 Python，不使用 Docker，请参照 [后端说明](backend/README.md#run-without-docker-on-windows)。此时 GROBID 可选；未启动时仍会尝试从 PDF 文本恢复编号参考文献。

推荐在项目根目录安装登录后自动运行的后台任务。它会启动 Docker Desktop、API/GROBID 和网页；关闭 PowerShell 或 Codex 后仍持续运行，异常退出后由 Windows 任务计划程序重启。需要登录 Windows 用户账号才能在重启后自动启动。

```powershell
.\scripts\install-background.ps1
```

需要临时前台运行网页时，也可在另一个 PowerShell 窗口执行：

```powershell
py -m http.server 8765 --bind 127.0.0.1 --directory dist
```

打开 `http://127.0.0.1:8765`，导入 PDF。网页会自动尝试本机 `http://127.0.0.1:8000`。解析完成的论文保存在 `userdata/documents/`，可从「历史记录」重新打开；选中正文可高亮或添加笔记，笔记保存在同一目录。也可以在「Aa 阅读设置 → Parser API URL」手动填写 API 地址。

后台服务端口：网页 `127.0.0.1:8765`；API `127.0.0.1:8000`；GROBID `8070` 只在 Docker 容器网络内使用。当前 Tailscale Serve 的网页 HTTPS 端口为 `443`，API HTTPS 端口为 `8443`。需要关闭时，从项目根目录执行 `.\scripts\stop-background.ps1`；这会禁用登录自启动并停止 API/GROBID，但不会删除论文和笔记。重新运行 `.\scripts\install-background.ps1` 即可启用。后台启动日志在 `result/background-service.log`。

如果已经在这台电脑上启用 Tailscale Serve，可在同一 Tailscale 私有网络的其他电脑打开本机的 Serve 网页地址。上传文件、历史记录和笔记都会写入本机的 `userdata/`。要检查转发设置，运行 `tailscale serve status`；网页与 API 需要分别转发到本机的 8765 和 8000 端口。

## 阅读设置、历史记录和批注

- 右上角齿轮可设置字体、字号、宽度、预设或自定义主题，以及批注工具栏位置。设置保存在当前浏览器，并按 32 位“同步档案码”写到本机服务器的 `userdata/profiles/`。在另一台电脑输入相同档案码并点“从服务器读取设置”，即可同步外观偏好；档案码相当于可读取和修改这些偏好的密钥，只在自己的 Tailscale 私网中分享。
- “历史记录”可切换“此浏览器”和“本机服务器”。每台电脑的浏览器各自保存新导入的 PDF 与解析结果；浏览器清理网站数据会清除这份本地副本。服务器记录和批注保存在 `userdata/documents/`，同一私网的其他电脑可访问。浏览器本地解析的论文批注保存在该浏览器的网站数据中。
- 阅读器顶部工具栏可选高亮、下划线、区域标记、颜色和是否写笔记。选择正文文字后应用批注；有笔记时右侧“笔记”页签会显示同色注释。放大阅读区后，左右浮动按钮可重新打开目录和四类材料。
- 对 Docling 独立识别的公式优先显示原 PDF 裁图；含复杂数学符号的正文段落也使用原 PDF 外观，并提供可复制文本。普通行内公式仍取决于 PDF 的文字层与 Docling 识别质量。重新导入论文才能应用新的解析规则；已保存的解析结果不会自动改写。

## 从已发布的网站连接 Windows 服务器

已发布的 Paperlight 网站使用 HTTPS，**不能直接把 Windows 的 HTTP 端口填入 Parser API URL**。需要为 Windows API 配置一个可访问的 HTTPS 反向代理或安全隧道，再把它的根地址（例如 `https://parser.example.com`，不带 `/api/documents`）填入网站设置。

上线前请为 API 增加访问认证、上传限流和 HTTPS，并把 `PAPERLIGHT_CORS_ORIGINS` 加上 `https://paperlight-reader.yqzheng432.chatgpt.site`。默认 API 只绑定本机回环地址，适合先验证；可让同机的 HTTPS 反向代理转发至该端口。GROBID 的 `8070` 端口无需对外开放。

更多 API 路由、存储与参数见 [backend/README.md](backend/README.md)。
