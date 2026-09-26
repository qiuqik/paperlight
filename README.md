# Paperlight · 结构化论文阅读器

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
| `backend/docker-compose.yml` | API 与 GROBID 的容器配置 |
| `backend/storage/` | 运行时 PDF、JSON、图像；不提交到 Git |

## 先在 Windows 本机运行

安装 Docker Desktop 并启用 WSL 2 后，在 PowerShell 执行：

```powershell
cd backend
docker compose up --build -d
curl.exe http://127.0.0.1:8000/health
```

第一次启动会下载 Docling 模型与 GROBID 镜像，需要较长时间和数 GB 磁盘空间。健康检查中的 `doclingAvailable` 应为 `true`。API 文档位于 `http://127.0.0.1:8000/docs`。日志可用 `docker compose logs -f api` 查看；停止服务用 `docker compose down`，不会删除已解析的文件。

在另一个 PowerShell 窗口，从项目根目录启动本地网页：

```powershell
py -m http.server 8765
```

打开 `http://127.0.0.1:8765`，导入有文字层的 PDF。网页会自动尝试本机 `http://127.0.0.1:8000`；页面不再提示“服务器解析不可用”且显示 Docling/GROBID 结果，表示前后端已连接。也可以在「Aa 阅读设置 → Parser API URL」手动填写 API 地址。

## 从已发布的网站连接 Windows 服务器

已发布的 Paperlight 网站使用 HTTPS，**不能直接把 Windows 的 HTTP 端口填入 Parser API URL**。需要为 Windows API 配置一个可访问的 HTTPS 反向代理或安全隧道，再把它的根地址（例如 `https://parser.example.com`，不带 `/api/documents`）填入网站设置。

上线前请为 API 增加访问认证、上传限流和 HTTPS，并把 `PAPERLIGHT_CORS_ORIGINS` 限制为 `https://paperlight-reader.yqzheng432.chatgpt.site`。当前容器配置默认只把 API 绑定到本机回环地址，适合先验证；公开端口时应通过受保护的反向代理接入，不要直接暴露 GROBID 的 `8070` 端口。

更多 API 路由、存储与参数见 [backend/README.md](backend/README.md)。
