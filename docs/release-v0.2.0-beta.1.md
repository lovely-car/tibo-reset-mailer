# Tibo Codex 额度重置监测与邮件提醒 · v0.2.0-beta.1

**把项目链接和收件邮箱交给自己的 Codex，由它帮你配置每小时 Tibo 公告邮件提醒。**

每小时实际读取 Tibo（@thsottiaux）的本人帖子及相关本人回复，筛选新的明确 Codex 额度重置消息。邮件先列北京时间，再列美国洛杉矶/太平洋时间，保留原始表述和原帖链接；没有新公告时保持安静。

## 怎么开始

打开[项目首页](https://github.com/lovely-car/tibo-reset-mailer#怎么用)，复制“请帮我配置”指令，替换收件邮箱后发给自己的 Codex。必要时按提示连接发信邮箱并完成本地运行授权。

Codex 会检查环境、现有提醒、实时来源、邮箱连接与调度能力，完成安装、首次汇总与每小时检查。具备可用邮箱连接时无需手填 SMTP 或额外配置模型 API Key。

## 2026-10-24 补充：真实流程验证

在一台 Windows Codex 桌面环境、Python 3.12 与已连接 Gmail 上，已实际完成：

- 从公开 GitHub 全新 clone，并在独立虚拟环境安装。
- 实时读取 7 条可核验的 Tibo 本人帖子及回复。
- 使用历史预告生成标有“项目流程验证”的双时区邮件，真实经 Gmail 发出；收件人确认 QQ 邮箱收到且内容正常。
- 宿主临时任务自动唤醒后，再次读取来源，返回 quiet；投递数与发信尝试次数没有增加。
- 删除临时验证任务，保留原有每小时提醒。

此前发布时的 44 项离线测试使用邮件替身；这次补充了真实来源、实际收件与一次自动唤醒。验证没有声称发现新的重置公告。完整范围见[脱敏实测记录](https://github.com/lovely-car/tibo-reset-mailer/blob/main/docs/VERIFICATION.md)。

## 版本与边界

继续保持 **v0.2.0-beta.1**。本次整理使用说明与补充验证记录，功能版本保持原样。最新说明以仓库 main 为准；既有下载附件保留发布时内容。

- 这次验证覆盖一台设备和一次短间隔自动唤醒，尚未证明长期运行或其他用户环境都稳定可用。
- 当前 Codex 需要本地执行、可发信的连接与调度能力；本地提醒需要电脑开机联网、桌面宿主保持运行。缺少能力时应明确报告待完成。
- X 公开页面可能不可访问或只展示部分历史。公告提醒不代表个人账户额度已经到账。
- 独立 SMTP、模型 API 和 Docker 使用方式保留在[高级手动部署](https://github.com/lovely-car/tibo-reset-mailer/blob/main/docs/ADVANCED_SETUP.md)，其中单列验证范围与已知问题。
- 私有邮箱、密钥和状态目录由各使用者自己保存，不应上传 GitHub，也不直接与其他监测器互换。

[项目首页](https://github.com/lovely-car/tibo-reset-mailer) · [English](https://github.com/lovely-car/tibo-reset-mailer/blob/main/docs/README.en.md) · [故障排查](https://github.com/lovely-car/tibo-reset-mailer/blob/main/docs/troubleshooting.md)
