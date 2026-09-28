# Paperlight · 结构化论文阅读器

Paperlight 将 PDF 解析为稳定的 Document Model，再由 Next.js 阅读器按章节、段落、图表、公式和参考文献渲染。Web、Python parser 与 GROBID 由项目根目录的 Docker Compose 管理。

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

- 首次安装时由管理员初始化账号，密码从标准输入读取，不写入数据库明文：`$password = Read-Host -MaskInput '初始管理员密码'; $password | docker compose run --rm -T parser python -m app.bootstrap_admin --username admin`。密码至少 12 位。现有服务器论文会登记到初始管理员名下，原文件不会移动。
- 管理员登录后从「管理」创建、停用或删除其他用户，并可重置密码。默认「我的论文」只显示当前账号的文档；管理员在用户管理页选择用户后可查看其论文。
- 登录后导入 PDF、阅读并做笔记。PDF 和解析资源保存在 `userdata/users/{user_id}/documents/{document_id}/`，文档归属、笔记、阅读进度和设置保存在 `userdata/paperlight.db`。不同设备使用同一账号即可恢复。
- 「我的论文」分最近阅读和全部论文。用户可删除自己的论文；管理员可管理其他用户的论文和账户。删除账户会一并清理其服务器数据。
- 阅读器提供高亮、下划线、区域标记、笔记、专注模式以及可持久化的排版设置。
- 升级前保存在 `userdata/documents/` 的文档仍留在原位，数据库会将其登记为管理员的文档。旧版浏览器或用户自选文件夹内的数据不会自动删除；如有尚未同步的笔记，请先保留这些备份。
- `result/` 保存解析快照。`userdata/` 与 `result/` 都不提交到 Git。
- Docling 模型保存在 Docker 卷中。GROBID 用于引用和元数据增强；公式识别不可靠时显示原 PDF 裁图。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `apps/web/` | Next.js 阅读器与同源 parser 代理 |
| `backend/app/` | FastAPI、PDF 解析和 Document Model 标准化 |
| `backend/tests/` | 解析与 API 测试 |
| `docker-compose.yml` | Web、parser、GROBID 的正式运行配置 |
| `scripts/` | Windows 登录启动与停止脚本 |

本机开发时先停止根目录 Compose，再用 `backend/docker-compose.yml` 临时提供 `127.0.0.1:8000` 的 parser API，随后运行 `npm run dev`。完成后停止临时后端，再运行根目录的 `docker compose up -d --no-build` 恢复远程服务。更多 API 路由与解析参数见 [backend/README.md](backend/README.md)。
