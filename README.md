# 中国大陆节假日长期订阅日历

提供放假、补班两个独立订阅，保留原 `holiday.ics` 合并订阅。所有地址长期固定，从 2027 年起持续维护正式公布的年度安排，以后每年无需重新订阅。

## 订阅

| 订阅 | 固定 HTTPS 地址 | 内容 |
| --- | --- | --- |
| 放假日历 | https://oybw0824.github.io/china-holiday/holidays.ics | 仅正式放假区间 |
| 调休补班日历 | https://oybw0824.github.io/china-holiday/workdays.ics | 仅调休上班日期 |
| 原合并订阅 | https://oybw0824.github.io/china-holiday/holiday.ics | 放假与补班，兼容原订阅 |

**分色使用方法：** 在 Apple 日历中分别添加“放假日历”和“调休补班日历”两个订阅，然后给两个日历选择不同颜色，例如放假红色、补班蓝色。颜色在客户端设置，拆分后的源不会强制覆盖个人颜色选择。

如果已订阅原合并版，请隐藏或取消原合并订阅，避免重复显示。继续使用合并版的用户无需任何操作。拆分日历继承原事件 UID、日期和时间戳，不修改原合并文件内容。

发布后，网站首页会显示当前仓库的实际 HTTPS 订阅地址。在 Apple 日历中选择“添加订阅日历”，粘贴该地址。直接下载文件后导入属于一次性导入；持续接收更新请使用订阅。更新时间取决于客户端刷新安排。

## 唯一数据依据

仅接受中国政府网 `www.gov.cn` 发布的《国务院办公厅关于 YYYY 年部分节假日安排的通知》正式原文，不接入民间日历、预测安排或模型生成的日期。

- 公告发现：国务院政策文件库官方检索接口 `https://sousuo.www.gov.cn/search-gov/data`，限定国务院文件和发文机关“国务院办公厅”。
- 公告正文：`https://www.gov.cn/zhengce/content/...` 或 `https://www.gov.cn/zhengce/zhengceku/...`，核对正式标题、发文字号、批准文字、落款机关和日期。
- 2027 年依据：[国办发明电〔2026〕10号，2026 年 11 月 5 日发布](https://www.gov.cn/zhengce/content/202611/content_7085101.htm)。包含 7 段假期、29 个假期日期、5 个调休补班日期。
- **2028 年及以后，只有正式公告发布并通过校验后才加入；未检索到时保持日历原样，不生成猜测数据。**

日历标注年度通知明确的连续放假区间及补班日期。连续区间包括其中的周末，不将区间内每一天都解释为加班工资口径的法定节日。

## 自动更新

`.github/workflows/update-holidays.yml` 每周一北京时间 **09:23** 检查一次，GitHub 调度可能延迟。

1. 通过官方检索接口发现正式年度公告，读取官方正文。
2. 校验发文身份、日期、全部七个节日、区间天数、星期、日期冲突及补班日期。
3. 将原文归档至 `data/sources/YYYY.txt`，结构化安排保存至 `data/years/YYYY.json`，记录原文 SHA-256。
4. 从全部历史年度同步生成 `holiday.ics`、`holidays.ics`、`workdays.ics`，校验放假与补班两类事件互斥且合计覆盖合并版全部事件，保留原 UID，并通过独立 iCalendar 解析器测试。
5. 全部通过后一次 Git 提交保存新增年度、日历和日志；失败时不提交或发布新日历。
6. 显式请求 Pages 从 `main` 根目录重新构建，核对部署提交及三个固定 HTTPS 地址返回的日历字节。

现有 2027 年 12 个 UID 保留。新增事件使用基于年度和事件身份的确定性 UUID；重复运行不会产生重复事件。没有新公告时仅记录成功检查，不改写 ICS。

`CHANGELOG.md` 保存年度导入记录与来源；`CHECKS.md` 和 `data/check-status.json` 记录成功检查。每周检查记录也会提交，避免长期没有新公告时仓库完全无活动。失败原因保留在 GitHub Actions 日志中。

## 手动触发

在 Actions → **Update official holidays** → **Run workflow**，选择 `main`：

- `source_url` 留空：自动检索正式公告。
- 填入已正式发布的中国政府网年度通知原文 HTTPS URL：直接核验该原文，用于搜索索引延迟时补录。第三方链接、预测内容及未来落款日期均拒绝。

工作流使用仓库 `GITHUB_TOKEN` 的 `contents: write` 和 `pages: write` 权限，无需个人访问令牌。组织策略或分支保护如禁止机器人直接提交，维护者需调整匹配此项目的权限；不得强制推送绕过保护。

## Pages 发布设置

仓库须为 Public，默认分支 `main`；Settings → Pages：**Deploy from a branch → main → / (root)**。

保持该设置、仓库名称及三个 ICS 文件路径，订阅地址就保持不变。`.nojekyll` 关闭 Jekyll 处理。使用 `GITHUB_TOKEN` 的提交不会自行触发分支式 Pages 构建，因此更新工作流会显式请求构建。

## 本地维护和验证

需要 Python 3.12 或更高版本。更新脚本本身只使用标准库，测试安装独立 RFC 5545 解析器。

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python scripts/holiday_calendar.py validate
python scripts/holiday_calendar.py update
```

`validate` 核对归档原文、年度 JSON、三个 ICS 的日期及历史 UID，并验证 UTF-8、CRLF、75 字节折行、全天事件及排他式 DTEND。测试覆盖错误天数、缺少节日、网络故障、结构变化及更新失败不覆盖文件。

如需从已归档并通过校验的历史数据重新生成三个订阅，可运行 `python scripts/holiday_calendar.py generate`；该命令不会预测或新增年份。

首次发布后可运行：

```bash
python scripts/verify_published.py --repository oybw0824/china-holiday --wait-seconds 420
```

本地读取 Pages 构建元数据需要 `GH_TOKEN` 提供具有 Pages 读取权限的 GitHub 令牌；Actions 中已自动传入内置令牌，无需额外配置。

公告措辞或网站结构变化时，解析器会安全失败。维护者应查看 Actions 日志，以官方原文为依据调整解析规则并补充测试。已有年度安排发生变化时，脚本拒绝自动重写历史；维护者应审查更正并同步归档与数据，保留已有 UID。不得手工猜测日期。

`tests/fixtures/2025.txt` 和 `2026.txt` 为已公布的历史公告测试样本，不加入实际订阅日历。
