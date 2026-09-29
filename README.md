# Paperlight · 结构化论文阅读器

Paperlight 将上传的 PDF 或固定版本的 arXiv 官方 HTML 解析为统一的 Document Model，再由 Next.js 阅读器按章节、段落、图表、公式和参考文献渲染。Web、Python parser 与 GROBID 由项目根目录的 Docker Compose 管理。

## 启动与远程访问

在 Windows 主机的项目根目录运行：

~~~powershell
docker compose up --build -d
~~~

网页只监听本机的 `127.0.0.1:8040`；parser 和 GROBID 只在 Compose 内部网络通信。浏览器统一请求 Web 的 `/api/parser`，由 Web 转发给 parser，不需要单独开放后端端口。

同一 Tailscale 私网中的 Mac 可打开 [Paperlight](https://desktop-c0d40qs.tailc57931.ts.net/)。首次设置或需要恢复转发时，在 Windows 主机运行：

~~~powershell
tailscale serve --bg --https 443 http://127.0.0.1:8040
tailscale serve status
~~~

Windows 主机必须保持开机且已登录，Docker Desktop 与 Tailscale 需要运行。若希望登录后自动启动 Compose，运行 `.\scripts\install-background.ps1`；它会在需要时启动 Docker Desktop，然后执行根目录的 Compose。`.\scripts\stop-background.ps1` 会禁用该登录任务并停止 Compose 服务，不删除文档或模型缓存。启动日志位于 `result/background-service.log`。

## 使用与数据

- 首次安装时由管理员初始化账号，密码从标准输入读取，不写入数据库明文：`$password = Read-Host -MaskInput '初始管理员密码'; $password | docker compose run --rm -T parser python -m app.bootstrap_admin --username admin`。密码至少 4 位，可以使用纯数字。现有服务器论文会登记到初始管理员名下，原文件不会移动。
- 管理员登录后从「管理」创建、停用或删除其他用户，并可重置密码。默认「我的论文」只显示当前账号的文档；管理员在用户管理页选择用户后可查看其论文。
- 登录后先进入 Library，可上传 PDF 或粘贴 arXiv `abs/pdf/html` 链接，并查看最近阅读、搜索与收藏。无版本链接在导入时读取 arXiv 官方版本记录并固定；官方 HTML 不完整时回退到该版本 PDF。点击论文进入 Reader，恢复阅读位置、笔记和设置。原文与解析资源保存在 `userdata/users/{user_id}/documents/{document_id}/`，文档归属、笔记、阅读进度、收藏和设置保存在 `userdata/paperlight.db`。不同设备使用同一账号即可恢复。
- 账号菜单提供个人资料、阅读设置、修改密码和退出登录；管理员还可进入管理后台。用户可删除自己的论文；管理员可管理其他用户的论文和账户。删除账户会一并清理其服务器数据。
- 阅读器提供高亮、下划线、区域标记、笔记、专注模式以及可持久化的排版设置。
- 升级前保存在 `userdata/documents/` 的文档仍留在原位，数据库会将其登记为管理员的文档。旧版浏览器或用户自选文件夹内的数据不会自动删除；如有尚未同步的笔记，请先保留这些备份。
- `result/users/{user_id}/{document_id}/` 保存对应账号的解析调试快照；旧文档的历史快照仍可能在 `result/{document_id}/`。`userdata/` 与 `result/` 都不提交到 Git。
- Docling 模型保存在 Docker 卷中。GROBID 用于 PDF 引用和元数据增强。PDF 行间公式显示原 PDF 裁图；检测到位置可靠但转写不可靠的行内公式时显示原 PDF 段落，并可打开原页。视觉模型的转写仅供复制或修订，不替代原公式。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `apps/web/` | Next.js 阅读器与同源 parser 代理 |
| `backend/app/` | FastAPI、PDF 解析和 Document Model 标准化 |
| `backend/tests/` | 解析与 API 测试 |
| `docker-compose.yml` | Web、parser、GROBID 的正式运行配置 |
| `scripts/` | Windows 登录启动与停止脚本 |

本机开发时先停止根目录 Compose，再用 `backend/docker-compose.yml` 临时提供 `127.0.0.1:8000` 的 parser API，随后运行 `npm run dev`。完成后停止临时后端，再运行根目录的 `docker compose up -d --no-build` 恢复远程服务。更多 API 路由与解析参数见 [backend/README.md](backend/README.md)。
