# 高级手动部署：Python、SMTP 与 Docker

普通使用者请先看[首页的 Codex 配置入口](../README.md#怎么用)。该路线由自己的 Codex 判断公告，使用已连接的邮箱和宿主定时器。

本页面向希望自行管理 Python、SMTP、模型接口或服务器的使用者。这是保留的高级 Beta 路线，和 Codex 路线使用不同的配置与状态目录；不要混用命令或让两个实例同时给同一邮箱发提醒。

## 验证范围与已知问题

2026-10-24 完成真实收件与一次定时唤醒验证的是 **Codex + 已连接 Gmail** 路线，详见[实测记录](VERIFICATION.md)。本页独立 SMTP 路线的服务端登录与真实收件、可选模型服务，以及服务器长期运行需要分别验证。

v0.2.1-beta.1 已修复此前离线复现的两个独立 SMTP 问题，并新增回归测试：

- 同一已处理重置主帖下出现无关新回复，不再重复投递同一具体计划/完成确认。
- 首次检查返回空证据与 warnings 时，按失败处理，不发零事件汇总、不初始化。

这些检查使用可控替身；不代表真实 SMTP、模型服务或服务器长期运行均已验证。疑似但未确认的公告保留待复查，来源 warnings 和 unknown 投递仍需检查；详见[新版更新说明](release-v0.2.1-beta.1.md)。

## 手动安装与启动

先下载并解压仓库源码，在项目目录打开终端。也可以使用 `git clone` 下载。

**1. 安装**

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate
python -m pip install -e .
```

**2. 填自己的邮箱配置**

```bash
python -m tibo_mailer setup
```

根据提示输入收件邮箱、发信邮箱、SMTP 主机和邮箱服务商提供的授权码 / 应用密码。默认使用 465 端口的 TLS 连接。需要 587 端口时，在生成的 `.env` 中改成 `SMTP_PORT=587`、`SMTP_SECURITY=starttls`。收件邮箱可以和发信邮箱不同。

也可以复制 `.env.example` 为 `.env` 后手动填写。不要把 `.env`、数据目录或投递记录上传 GitHub。

**3. 检查配置与来源**

```bash
python -m tibo_mailer doctor
python -m tibo_mailer check --dry-run
```

`doctor` 只检查配置格式。`--dry-run` 会实时读取 X、生成邮件内容，不发送邮件、不标记帖子已读。它仍会在私有数据目录记录诊断。

**4. 主动发一封测试邮件**

```bash
python -m tibo_mailer test-email
```

这一步会真实发出一封带 `[演示]` 的邮件。确认收件箱或垃圾邮件文件夹中收到后再启动监测。

**5. 每小时自动运行**

```bash
python -m tibo_mailer run
```

保持程序和电脑运行。默认间隔 3600 秒，在 `.env` 中修改 `CHECK_INTERVAL_SECONDS` 可调整，最短支持 300 秒。首次有效扫描会发送一次汇总，历史帖子合并处理；汇总发送成功后才完成初始化。之后只提醒新消息或正文修改后的新版本。

如需由操作系统任务计划程序调用，使用 `python -m tibo_mailer check`，并使用同一个工作目录、配置文件和数据目录。禁止多个实例共享同一个目录同时运行，程序也会主动拒绝重叠执行。

## 两种判断方式

| 模式 | 配置 | 行为 |
|---|---|---|
| 默认 `rules` | `ANALYSIS_MODE=rules` | 无需模型接口。只接受明确表述，保守匹配，可能漏掉复杂隐喻或长回复链 |
| 可选 `llm` | 填写以下配置 | 由模型整理中文摘要并判断语义，再核验原文证据；需要独立的模型 API 凭据，费用按服务商规则计算 |

```dotenv
ANALYSIS_MODE=llm
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=填写你的可用模型名称
LLM_API_KEY=填写你自己的API密钥
```

使用支持 `POST /chat/completions` 与 JSON 输出的 OpenAI 兼容服务。没有默认模型，不会自动选择套餐或购买额度。只有相关的公开帖子会发给模型，邮箱与 SMTP 配置不会进入模型请求。服务未返回有效证据时会保留帖子等待下次重试。

接口实现参考 [OpenAI Chat Completions 文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。不同兼容服务的支持程度需要自行验证。

## 在服务器上长期运行

有 Docker Compose 时，先复制并填写 `.env`：

```bash
docker compose up -d --build
docker compose logs --tail 50 mailer
docker compose exec mailer python -m tibo_mailer status
```

状态存储在命名卷 `mailer-data`，容器重启后保留。不要删除这个卷，否则会丢失初始化、去重与待发记录。Docker 配置已提供；实际构建验证范围见版本发布说明。

### GitHub Actions 的作用

本仓库提供自动测试和手动 `Check public X source (no email)` 工作流，用于验证 GitHub 运行器能否读取 X。**没有提供无状态的云端定时发信任务**：临时运行器丢失投递状态会造成重复邮件。

第一版长期监测请使用本地程序或带持久卷的服务器。GitHub 云端来源检查通过，也不代表所有地区或后续每次请求都能访问 X。Actions 定时任务可能延迟，公开仓库长期没有活动也可能被自动停用，参见 [GitHub 官方说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

## 私有状态与投递恢复

以下 `status` / `resolve` 仅用于独立 SMTP 路线。Codex 路线请使用[agent 命令与连接邮箱恢复流程](CODEX_MONITOR.md)，不要用这些命令操作 agent 数据目录。

## 投递状态

```bash
python -m tibo_mailer status
```

数据保存在 `.tibo-mailer/state.sqlite3`（可通过 `TIBO_DATA_DIR` 更改），包含去重信息、邮件内容、SMTP 接受回执状态及稳定的 `Message-ID`。**此目录是私有运行数据**，不属于开源源码。

- `pending`：明确未完成，后续检查优先重试。
- `sent`：SMTP 服务端接受邮件；最终是否进入收件箱仍由邮件系统决定。
- `unknown`：发送阶段断线或进程退出，可能已经发出。程序停止自动重发这封邮件。

遇到 `unknown`，先按 `status` 显示的 `Message-ID`、主题和原帖在发信账号中核对，再执行：

```bash
python -m tibo_mailer resolve DELIVERY_ID --sent
# 确认没有发出后，才执行：
python -m tibo_mailer resolve DELIVERY_ID --retry
```

重试会使用原 `Message-ID`，但邮件系统不保证据此去重，因此不能在结果未知时盲目重发。

## 开发检查

在仓库目录执行：

```bash
python -m unittest discover -s tests -v
python -m tibo_mailer preview --output docs/email-preview.html
```

离线测试、实际来源可访问性、邮件服务端受理和最终收件分别验证。`preview` 生成离线格式示例，不会发送邮件。

[返回首页](../README.md) · [故障排查](troubleshooting.md) · [设计与边界](design.md)
