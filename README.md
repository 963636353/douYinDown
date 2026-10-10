# 抖音视频下载器

青柠视频下载是一个以 QFluentWidgets 图形界面为主的抖音视频下载工具，支持浏览器扫码登录、登录信息保存、批量解析链接、作者主页作品解析、画质选择以及并发下载。项目保留了早期的命令行入口，同时包含支付授权、用户统计和更新信息服务端。

> 说明：本项目仅供个人学习、研究和下载本人有权保存的内容使用。请遵守抖音平台规则、当地法律法规以及作品版权要求，不要用于批量搬运、侵权传播或任何违反平台条款的用途。

## 阅读导航

| 你想了解的内容 | 对应章节 |
| --- | --- |
| 软件能做什么 | [功能特性](#功能特性)、[当前支持范围](#当前支持范围)、[截图](#截图) |
| 怎么运行和下载 | [运行环境](#运行环境)、[快速开始](#快速开始)、[GUI 使用流程](#gui-使用流程)、[CLI 备用入口](#cli-备用入口) |
| 怎么配置、各目录有什么用 | [配置说明](#配置说明)、[目录结构](#目录结构) |
| 怎么维护和检查修改 | [架构说明](#架构说明)、[维护与测试](#维护与测试)、[添加新平台](#添加新平台) |
| 怎么部署和发布 | [服务端部署与补充文档](#服务端部署与补充文档)、[打包发布](#打包发布) |
| 出错或准备上传源码 | [常见问题](#常见问题)、[GitHub 上传前检查](#github-上传前检查)、[已知限制](#已知限制)、[许可证](#许可证) |

首次使用可以先阅读快速开始；不熟悉项目文件时，可以直接查看目录结构及用途表。

## 功能特性

- 图形界面：基于 `PyQt-Fluent-Widgets`，左侧导航包含下载、登录、设置和说明。说明页展示版本号、作者微信、电脑身份及教程入口。
- 使用权限：后台登记电脑身份并校验使用期限，到期后点击下载或失败重试才显示单个窗口选套餐和扫码付款；付费授权确认后自动继续下载，体验期间不显示授权入口或收费信息。
- 浏览器扫码登录：依次尝试 Chrome（`undetected_chromedriver`）、Edge/Selenium、可选 Playwright。
- Cookie 管理：登录 Cookie 保存为 Netscape 格式，默认路径为 `cookies/douyin_cookies.txt`。
- 单视频解析：支持抖音短链、普通视频链接、图文/笔记链接以及带 `modal_id` 的页面链接。
- 作者主页解析：支持从抖音用户主页链接中提取 `sec_user_id`，分页获取作者公开作品。
- 批量输入：下载页支持每行一个链接，解析后加入下载队列。
- 视频/音频切换：GUI 可选择下载视频或从作品音乐信息中提取音频链接。
- 画质选择：解析到多个视频源时，可按最高可用、1080p、720p、540p、480p、最低可用或默认源选择。
- 并发下载：可在设置页配置并发数，下载进度会回写到 GUI 列表和底部进度条。
- 更新检查：GUI 启动后请求远程版本配置，远程版本号与本地不同就显示更新提示；更新检查失败时显示提示并退出。
- 用户统计：后台上报启动和成功保存的视频数量，服务端提供公开的活跃用户汇总页面。
- 登录页“检查登录信息”只检查本地 Cookie 是否存在、是否超过保存的有效期；实际接口是否允许访问，以解析结果为准。
- 旧版 CLI 保留：`douyin_downloader.py` 仍可作为命令行版本运行。
- 平台扩展接口：通过 `BasePlatform` 抽象，后续可按目录约定添加新的平台实现。

## 当前支持范围

当前内置平台只有抖音：

| 类型 | 支持情况 |
| --- | --- |
| 抖音短链 `https://v.douyin.com/...` | 支持，会自动跟随跳转 |
| 视频链接 `https://www.douyin.com/video/<id>` | 支持 |
| 图文/笔记链接 `https://www.douyin.com/note/<id>` | 支持解析 ID，能否下载取决于接口返回的视频资源 |
| 带 `modal_id` 的页面链接 | 支持从查询参数提取作品 ID |
| 浏览器精选页 `/jingxuan?modal_id=<id>` | 支持，与分享短链接共用视频详情流程 |
| 作者主页 `/user/<sec_user_id>` | 支持分页获取公开作品 |
| 需要登录或权限受限内容 | 需要有效 Cookie，仍可能因权限、删除、风控等原因失败 |

## 截图

![主界面](images/01.png)
![登录](images/02.png)
![设置](images/03.png)

## 运行环境

推荐环境：

- Windows 10/11
- Python 3.10 或更高版本
- Chrome 或 Edge 浏览器
- 可访问抖音 Web 端、浏览器驱动下载源，以及更新和授权服务的网络环境

主要依赖：

- `f2`：提供 `a_bogus` 签名相关能力。
- `PyQt5`：Qt GUI 基础库。
- `PyQt-Fluent-Widgets`：QFluentWidgets 风格 GUI 组件。
- `requests`：HTTP 请求和流式下载。
- `undetected-chromedriver`：优先使用的扫码登录浏览器自动化方案。
- `selenium`：浏览器控制层。
- `playwright`：可选降级方案。

客户端运行依赖见 [requirements.txt](requirements.txt)。Playwright 作为可选兜底方案在文件中保留为注释，需要时单独安装。日常运行客户端无需安装服务端依赖；本地启动服务端或执行完整测试时才需要 [服务端/requirements.txt](服务端/requirements.txt)。

## 快速开始

### 1. 创建并激活虚拟环境

以下命令在项目根目录执行。虚拟环境是本机保存 Python 和依赖的目录；已有可用环境时，直接激活即可，无需重新创建。

Windows PowerShell：

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Git Bash：

```bash
python -m venv venv
source venv/Scripts/activate
```

本机当前已有 `.venv/`，可以在 PowerShell 中使用：

```powershell
.\.venv\Scripts\Activate.ps1
```

不激活也可以通过 `.\.venv\Scripts\python.exe main.py` 直接启动。`venv` 和 `.venv` 都是环境目录名称，后续运行和安装依赖时应使用实际存在的环境。打包脚本目前固定使用 `venv`，具体见[打包发布](#打包发布)。

### 2. 安装依赖

```bash
python -m pip install -U pip
python -m pip install -r requirements.txt
```

如果希望启用 Playwright 降级方案：

```bash
python -m pip install playwright
python -m playwright install chromium
```

### 3. 启动 GUI

```bash
python main.py
```

首次使用时，进入「登录」界面，点击浏览器扫码登录按钮，用抖音 App 扫码登录。登录成功后，Cookie 会保存到 `cookies/douyin_cookies.txt`，后续启动会自动复用。

客户端默认连接项目配置的远程服务，不会自动启动 `服务端/` 下的程序。启动后会联网检查更新并在后台登记电脑身份；到期后的下载需要有效授权。更新与授权服务配置见[配置说明](#配置说明)，本地联调和服务器部署见[服务端部署与补充文档](#服务端部署与补充文档)。

## GUI 使用流程

1. 启动程序：

   ```bash
   python main.py
   ```

2. 登录：

   - 打开「登录」界面。
   - 点击浏览器扫码登录。
   - 在弹出的浏览器中使用抖音 App 扫码。
   - 程序检测到 `sessionid` 或 `sid_tt` 后会保存 Cookie。

3. 解析链接：

   - 回到「下载」界面。
   - 选择平台，目前默认为「抖音」。
   - 选择下载类型：视频或音频。
   - 下载视频时可选择画质偏好。
   - 解析作者主页时，如需限制作品发布日期，可勾选「按发布日期筛选」并选择开始、结束日期；默认不启用筛选。
   - 在输入框中粘贴一个或多个链接，每行一个。
   - 点击「解析」，或使用 `Ctrl + Enter`。

4. 下载：

   - 解析成功后，作品会进入下载列表。
   - 点击「全部下载」开始并发下载。
   - 文件名会按队列顺序自动加序号前缀，便于保持下载顺序。
   - 使用期限到期时会在下载前显示套餐和支付窗口；关闭窗口不会开始下载，网络异常或设备禁用会分别提示。
   - 整批任务结束后显示成功和失败数量；启用“下载完成后打开文件夹”时，确认结果后打开成功下载所在的目录。

## CLI 备用入口

如果只想使用命令行版本，可以运行：

```bash
python douyin_downloader.py
```

CLI 版本会在首次运行时尝试扫码登录，并把 Cookie 保存到项目根目录下的 `douyin_cookies.txt`。GUI 入口 `main.py` 启动时会自动把旧路径的 Cookie 迁移到 `cookies/douyin_cookies.txt`。

CLI 版本主要面向单链接交互下载；GUI 版本功能更完整，推荐优先使用 GUI。

## 配置说明

### 下载设置

`config.json` 保存本机下载路径、代理和并发等设置；不存在时程序使用默认值，在设置页保存后写入该文件。源码运行时文件位于项目根目录，打包后位于 exe 所在目录。该文件已在 `.gitignore` 中忽略；仓库中提供 [config.example.json](config.example.json) 作为示例：

```json
{
  "download_dir": "",
  "proxy_enabled": false,
  "proxy_url": "",
  "max_concurrent": 2,
  "download_cover": false,
  "open_folder_after": false
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `download_dir` | 下载文件保存目录。为空时使用项目根目录（或 exe 所在目录）下的 `downloads/`。 |
| `proxy_enabled` | 代理开关，目前仅保存设置，未接入下载和抖音 API 请求。 |
| `proxy_url` | 代理地址，目前请求层尚未实际读取该字段。 |
| `max_concurrent` | 并发下载数量。 |
| `download_cover` | 封面下载开关，目前代码已解析 `cover_url`，但 GUI 下载流程尚未接入封面保存。 |
| `open_folder_after` | 整批任务结束、确认结果后，打开第一个成功下载所在的目录；没有成功下载则不打开。 |

### 支付与授权连接

客户端通过 `payment_kit/` 访问 `服务端/` 提供的接口，完成电脑登记、授权校验、支付和下载统计。两者属于同一个项目，但分别运行：客户端在使用者电脑上运行，服务端在服务器上单独部署。

默认 API 地址由 [core/payment_config.py](core/payment_config.py) 中的 `DEFAULT_API_BASE` 设置为 `https://douyindown.secs.cc/api/pc`。需要更换服务时，可参考 [payment_client.example.json](payment_client.example.json)，在项目根目录（或 exe 所在目录）创建本机 `payment_client.json`：

```json
{
  "api_base": "https://douyindown.secs.cc/api/pc"
}
```

服务地址选择顺序为：非空环境变量 `QINGNING_PAYMENT_API_BASE` → 本机配置中的 `api_base` → 代码默认地址。正式远程服务使用 HTTPS，HTTP 仅供本机调试。微信商户配置保存在服务端，不放入客户端连接文件。

Windows 电脑身份、设备令牌和下载待上报队列保存在 `%LOCALAPPDATA%\QingningVideoDownloader\`。首次升级会导入原软件目录 `payment_credentials/` 的旧凭据；升级前不要删除旧凭据，全部凭据丢失时需要联系客服恢复。迁移说明见[统计更新部署](docs/统计更新部署.md)。

### 更新信息

GUI 默认从 `https://douyindown.secs.cc/videoDown.txt` 获取更新信息，地址由 [core/update_checker.py](core/update_checker.py) 中的 `UPDATE_CHECK_URL` 设置，独立于支付 API 地址。

当前判断方式是远程 `version_number` 与本地 `APP_VERSION` **不相等**，并未比较版本大小。普通更新可选择暂不升级；强制更新会在更新窗口关闭后退出。请求失败或返回内容无法解析时，会显示“检查更新失败”窗口并退出。版本维护和服务端文件修改分别见[维护与测试](#维护与测试)及[更新信息部署](docs/更新信息部署.md)。

## 目录结构

下面列出主要源码、文档和运行目录，省略常规的 `__init__.py` 包标记文件。部分运行目录由程序自动创建，不要求在新下载的源码中预先存在。

```text
.
├── README.md                       # 项目说明
├── AGENTS.md                       # AI 编程助手维护此项目时参考的说明
├── .gitignore                      # GitHub 上传忽略规则
├── main.py                         # GUI 入口：注册平台并启动主窗口
├── douyin_downloader.py            # 旧版命令行入口
├── requirements.txt                # 客户端运行依赖
├── config.example.json             # 下载设置示例
├── payment_client.example.json     # 支付授权服务连接配置示例
├── build_exe.ps1                   # Windows PyInstaller 打包脚本
├── package_release.bat             # 打包脚本快捷入口
├── images/                         # README 截图
├── docs/                           # 补充文档，不参与程序启动
│   ├── 产品计划.md                  # 需求、修改记录和待核实事项
│   ├── 统计更新部署.md              # 用户统计升级与旧电脑身份迁移说明
│   └── 更新信息部署.md              # 更新接口部署与 videoDown.txt 维护说明
├── tests/                          # 自动测试：下载接口、Cookie、驱动、支付、授权、统计和更新
├── payment_kit/                    # 客户端直接依赖：支付接口、电脑身份、授权、Qt 窗口和统计上报
├── 服务端/                         # 单独部署的支付授权、用户统计及更新信息后台
│   ├── README.md                   # 服务端安装、配置、接口与联调说明
│   ├── main.py                     # 服务端入口
│   ├── requirements.txt            # 服务端依赖
│   ├── .env.example                # 服务端环境配置示例
│   ├── videoDown.txt               # 对外提供的更新信息，内容为 JSON
│   ├── check_database_dates.py     # SQLite 时间字段只读检查工具
│   └── payment_server/
│       ├── app.py                  # HTTP 接口与应用初始化
│       ├── config.py               # 服务配置及套餐定义
│       ├── database.py             # 数据库初始化、迁移和备份
│       ├── models.py               # 用户与订单数据模型
│       ├── service.py              # 注册、授权、订单及统计业务
│       ├── stats_page.py           # 公开用户统计页面
│       └── wechat.py               # 微信支付请求、签名及回调校验
├── core/                           # 平台无关基础能力
│   ├── base_platform.py            # BasePlatform 抽象类、VideoInfo 和 VideoVariant 数据结构
│   ├── browser_login.py            # 扫码登录：Chrome → Edge/Selenium → 可选 Playwright
│   ├── chromedriver_util.py        # ChromeDriver 检测、下载与缓存
│   ├── cookie_manager.py           # Netscape 格式 Cookie 读写
│   ├── downloader.py               # 流式下载和进度回调
│   ├── paths.py                    # 源码运行和 exe 运行时路径
│   ├── payment_config.py           # 支付连接配置和本机凭据路径
│   └── update_checker.py           # 远程版本检查
├── gui/                            # QFluentWidgets GUI
│   ├── app.py                      # 主窗口、左侧导航和状态栏
│   ├── about_tab.py                # 说明页：版本、作者、电脑身份和教程
│   ├── authorization_tab.py        # 后台授权校验、电脑登记和下载统计，不加入导航
│   ├── download_tab.py             # 链接解析、下载队列、并发下载
│   ├── login_tab.py                # 登录状态和 Cookie 操作
│   ├── payment_dialog.py           # 到期后的套餐选择与扫码支付窗口
│   ├── settings_tab.py             # 设置页和 config.json 编辑
│   └── widgets.py                  # 进度条、下载列表组件
├── platforms/
│   └── douyin/
│       ├── platform.py             # DouyinPlatform 实现
│       ├── api.py                  # 抖音 API 参数、请求头和签名
│       └── url_parser.py           # 链接解析和文件名清理
├── cookies/                        # 登录 Cookie，公开上传前不要提交真实文件
├── downloads/                      # 下载输出目录
└── logs/                           # 程序状态与错误日志，以及 f2 或历史运行日志
```

### 各类目录是否需要保留

| 类别 | 目录或文件 | 用途与保留建议 |
| --- | --- | --- |
| 程序源码 | `core/`、`gui/`、`platforms/`、`payment_kit/` | 当前客户端使用的功能模块，不能按“README 里没提到”判断为多余文件。 |
| 服务端源码 | `服务端/` | 支付、授权、统计和更新后台；不由客户端自动启动，但属于项目，建议保留。 |
| 维护资料 | `docs/`、`tests/`、`AGENTS.md` | 分别是补充说明、自动检查和 AI 助手维护指南；日常运行不需要，后续修改或部署时有用，建议保留。 |
| 文档图片 | `images/` | README 使用的截图，删除会导致图片无法显示。 |
| 版本记录 | `.git/` | 隐藏的 Git 版本记录；软件运行不需要，删除会失去本地修改历史和仓库信息。 |
| 本机环境 | `venv/` 或 `.venv/` | Python 和已安装的依赖；使用该环境运行时需要，可以重建，通常不提交。 |
| Python 缓存 | 各目录中的 `__pycache__/` | 运行后自动生成的缓存，可重新生成，通常不提交。 |
| 运行数据 | `cookies/`、`downloads/`、`logs/`、本机 `config.json` | 登录信息、下载文件、日志和个人设置；不属于源码，目录可能在运行后生成。Cookie 含登录态。 |
| 构建产物 | `build/`、`dist/`、`release/`、生成的 `.spec` 和 zip | 打包时生成，可重新构建，通常不提交。 |

`docs/` 和 `tests/` 都与项目有关，只是不参与日常启动；`payment_kit/` 则被当前客户端直接调用。不要通过删除不认识的目录来整理项目。

## 架构说明

项目采用平台抽象加 GUI 编排的结构。客户端负责解析和下载，独立服务端负责支付、使用权限、用户统计和更新信息。

核心数据流：

```text
客户端启动
  -> 后台读取电脑身份并向服务端登记、上报启动
  -> 后台查询使用期限
  -> 独立请求远程更新信息

扫码登录
  -> core.browser_login.perform_browser_login()
  -> core.cookie_manager.save_cookies()
  -> cookies/<platform>_cookies.txt

解析链接
  -> gui.download_tab.DownloadTab._parse_url()
  -> platform.extract_video_id() 或 platform.extract_profile_id()
  -> platform.get_video_info() 或 platform.get_profile_videos()
  -> core.base_platform.VideoInfo

下载文件
  -> gui.download_tab.DownloadTab._start_all_downloads()
  -> 后台授权校验；到期时显示套餐和支付窗口
  -> 校验通过后提交下载任务
  -> core.downloader.download_video()
  -> 保存到 download_dir
  -> 成功保存的视频写入本机统计队列，后台上报服务端
```

下载统计只记录成功保存的视频，音频、图文和失败任务不计入；重复下载成功会再次计数。网络失败时队列保留并重试，服务端直接累加增量，少量重试可能重复计数。统计口径见[统计更新部署](docs/统计更新部署.md)。

`BasePlatform` 定义了平台需要实现的接口：

- `name`
- `display_name`
- `cookie_domain`
- `login_url`
- `session_cookie_names`
- `extract_video_id(url)`
- `get_video_info(video_id, cookies)`
- `sanitize_filename(title)`
- `build_download_headers(cookies)`
- `resolve_short_url(url)`

`BasePlatform` 还提供主页解析的可选接口，目前由抖音平台实现：

- `extract_profile_id(url)`
- `get_profile_videos(profile_id, cookies, progress_callback=None, *, start_time=None, end_time=None)`

抖音 Web API 通过 `f2.utils.abogus` 生成 `a_bogus` 参数，并使用本地 Cookie 的 UIFID、同源指纹及 SecSDK 签名。明确的 Argus 身份拒绝立即提示，其他临时 403 最多请求三次；这些身份头不会发送到视频下载 CDN。

## 维护与测试

### 修改客户端版本号

修改 [core/update_checker.py](core/update_checker.py) 中的 `APP_VERSION`，例如 `APP_VERSION = '20261010'`。GUI 说明页和更新请求读取同一个值。

远程发布版本号在 `服务端/videoDown.txt` 的 `version_number` 中维护。当前两者只比较是否相等，填写时应保持同一版本的字符串一致。服务端文件格式、下载地址及强制升级字段见[更新信息部署](docs/更新信息部署.md)。

### 自动测试

`tests/` 相当于项目的“自动检查员”，用于确认修改代码后旧功能仍按预期工作。普通使用者运行下载器时不需要执行测试，`python main.py` 也不会自动运行这些文件。

完整测试涉及客户端 GUI 和服务端接口，需要在同一个测试环境中安装两套依赖。激活实际使用的虚拟环境后，在项目根目录运行：

```powershell
python -m pip install -r requirements.txt -r 服务端/requirements.txt
python -m unittest discover -s tests -v
```

| 测试内容 | 检查目的 |
| --- | --- |
| Cookie、驱动和电脑身份 | 排除过期登录信息、匹配浏览器驱动、保持电脑身份稳定。 |
| 抖音签名、详情和主页分页 | 验证签名与错误处理，不把未完成分页误报为全部解析成功。 |
| 支付接口和授权窗口 | 检查订单、回调、授权期限、到期拦截、付款后继续下载及取消操作。 |
| 用户统计和数据库迁移 | 检查成功下载计数、待上报队列、自然日边界、旧数据保留和时间字段。 |
| 更新信息和本机联调 | 检查更新文件读取、错误响应、版本判断及本机 HTTP 演示流程。 |

支付相关自动测试使用临时数据库、模拟微信响应或本机演示服务，不代表已经验证线上真实支付回调。线上部署状态和历史联调记录需要另行核实。

## 添加新平台

新增平台时建议按下面的步骤：

1. 在 `platforms/<platform_name>/` 下创建：

   ```text
   platform.py
   api.py
   url_parser.py
   __init__.py
   ```

2. 在 `platform.py` 中继承并实现 `BasePlatform`。

3. 如果平台需要登录，配置：

   - `cookie_domain`
   - `login_url`
   - `session_cookie_names`

4. 在 `main.py` 中导入并注册平台：

   ```python
   from platforms.example.platform import ExamplePlatform

   platforms = [
       DouyinPlatform(),
       ExamplePlatform(),
   ]
   ```

5. 如果平台 API 需要签名或特殊请求头，把逻辑放在对应平台的 `api.py` 中，不要写进 GUI 层。

## 服务端部署与补充文档

服务端有独立的依赖和启动入口，详细安装、商户配置、本地演示及接口说明见下表。日常客户端使用项目默认配置的远程服务；维护者需要自行部署或更新服务器时再阅读部署文档。

| 文档 | 适用场景 |
| --- | --- |
| [服务端说明](服务端/README.md) | 安装和启动服务端、配置支付、修改套餐、查看接口及本机演示流程。 |
| [统计更新部署](docs/统计更新部署.md) | 升级用户统计、迁移数据库、保留电脑身份和旧授权。 |
| [更新信息部署](docs/更新信息部署.md) | 部署更新接口、维护版本号和下载地址；统计更新尚未部署时先完成统计升级。 |
| [产品计划](docs/产品计划.md) | 查看需求、已实现修改和待核实事项；其中的历史价格与联调记录不代表当前线上状态。 |

客户端配置的默认域名并不证明服务端已经部署了最新源码。升级前按相应文档保留配置并备份数据，再核对接口；商户参数只配置在服务器上。

## 打包发布

项目包含 Windows 下的 PyInstaller 打包脚本。源码调试直接运行 `python main.py` 即可，需要发布时再打包。

脚本目前固定读取项目根目录 `venv\Scripts\python.exe` 和该环境中的 PyInstaller，不会自动使用已激活的 `.venv`。如果只有 `.venv/`，需按[快速开始](#快速开始)创建 `venv/` 并安装客户端依赖后再运行脚本；不要直接重命名已有环境。

```powershell
.\build_exe.ps1
```

也可以双击或运行快捷入口：

```bat
package_release.bat
```

脚本在缺少 PyInstaller 时自动安装，执行编译检查后清理旧的 `build/` 和 `dist/`，生成 `dist/DouyinDownloader/`，默认将 zip 保存到 `release/`。本机 `payment_client.json` 存在时会复制到客户端发布目录；服务端单独部署，`docs/` 和 `tests/` 不作为客户端运行目录复制。`build/`、`dist/`、`release/` 和自动生成的 `.spec` 文件均属于本地构建产物，默认不提交。

可选参数：`-SkipZip` 跳过压缩，`-SmokeTest` 启动生成的程序做短暂检查。后者会实际启动客户端并执行正常联网流程，只在需要时使用。

## 常见问题

### 1. 提示未登录或 Cookie 无效

进入「登录」标签页重新扫码登录。如果仍失败，可以删除 `cookies/douyin_cookies.txt` 后重新登录。

### 2. 浏览器没有打开或 ChromeDriver 失败

确认已经安装 Chrome 或 Edge，并安装了依赖。GUI 登录失败时会弹出最后一条错误信息，完整运行日志位于 `logs/app.log`。

ChromeDriver 会先通过 Google 官方版本索引匹配 Chrome 的构建版本，未找到时再匹配同一主版本，避免直接使用浏览器补丁版本号导致下载地址返回 404。

```bash
python -m pip install -r requirements.txt
```

登录流程在 Chrome 失败后先尝试 Edge/Selenium。两者均失败时，可按[快速开始](#快速开始)中的命令安装可选 Playwright 兜底。

### 3. API 返回错误或视频不可用

常见原因包括：

- Cookie 失效。
- 作品已删除、私密、仅好友可见或区域受限。
- 抖音 Web API 或风控策略发生变化。
- `f2` 的 `a_bogus` 签名实现与当前接口不兼容。

### 4. 下载音频失败

音频下载依赖作品接口返回的 `music.play_url`。部分作品可能没有可用音频链接，此时应切回视频下载。

### 5. 代理设置为什么没有生效

当前设置页能保存 `proxy_enabled` 和 `proxy_url`，但 `requests.get()` 调用尚未接入 `proxies` 参数。如需代理，需要在 `platforms/douyin/platform.py` 和 `core/downloader.py` 的请求处扩展。

### 6. 作者主页只解析出第一页作品

先在「登录」页重新扫码登录，再解析主页。登录过期时，接口可能仍返回第一页公开作品，但后续页只返回 `status_code`，没有作品列表和分页字段。

程序读取 Cookie 时会排除已过期的 Cookie，并在解析前检查登录 Cookie。不完整分页响应会最多请求三次；仍不完整或分页游标异常时，会提示「解析未完成」，不会把已获取的第一页误报为全部作品。要获取全部作品，请确认未勾选「按发布日期筛选」。

### 7. 启动后提示检查更新失败并退出

这是当前程序的更新失败处理方式。检查网络能否访问 `core/update_checker.py` 中 `UPDATE_CHECK_URL` 指定的地址，以及服务端是否返回有效的更新 JSON。修改 `payment_client.json` 只改变支付授权地址，不改变更新地址。

维护者应检查更新接口是否已部署、反向代理是否正确，以及服务端 `videoDown.txt` 是否存在且格式有效。详细排查步骤见[更新信息部署](docs/更新信息部署.md)。

### 8. 下载时提示无法验证使用状态

抖音登录和软件使用授权是两套独立流程，重新扫码登录抖音不能解决授权服务连接问题。检查网络、支付 API 地址和本机连接配置；网络异常或设备禁用不会被当作到期来引导付款。电脑身份或旧凭据异常时，请保留本机数据并联系客服，不要通过删除凭据尝试恢复。

### 9. 为什么打包提示找不到 Python 环境

当前打包脚本固定使用 `venv/`。如果实际只有 `.venv/`，源码运行仍可使用它，但打包前需要创建脚本要求的 `venv/` 并安装客户端依赖。详细说明见[打包发布](#打包发布)。

## GitHub 上传前检查

公开仓库前检查本地数据，当前忽略规则以 [.gitignore](.gitignore) 为准；本机环境、缓存和构建产物的用途见[目录结构](#目录结构)。

| 内容 | 上传前处理 |
| --- | --- |
| Cookie、设备令牌、`.env`、数据库、证书私钥 | 包含登录态、商户配置、授权或订单数据，保留在本机或服务器，不公开。 |
| `config.json`、`payment_client.json` | 本机配置不提交，对外使用对应的 `.example.json` 示例。 |
| `downloads/`、`logs/` | 下载内容和运行日志不提交，日志可能含个人信息或调试内容。 |
| `.payment-test/`、`服务端/payment_reuse/` | 如本机存在，属于旧参考代码或临时联调资料，当前程序不依赖，也不提交。 |
| `服务端/支付代码.txt`、`_debug_curl.html`、`要下载的.txt`、`细节记录.txt` | 如本机存在，属于私有参考或历史调试资料，已在忽略规则中。 |
| 客户端和服务端源码、`docs/`、`tests/`、截图及配置示例 | 属于项目内容，保留；`服务端/videoDown.txt` 是公开更新信息文件，也应保留。 |

仅添加忽略规则不会停止跟踪已经提交过的文件。如检查发现私有文件已被 Git 跟踪，应按实际路径用 `git rm --cached`（目录使用 `git rm -r --cached`）停止跟踪；该操作保留本机文件，已提交的历史内容仍需另行处理。

本机授权数据位于用户目录，旧版凭据也可能保存在软件目录的 `payment_credentials/` 中。不要删除凭据来整理仓库，删除可能导致原授权身份无法恢复。

`config.example.json` 仅是示例，修改它不会自动改变程序默认值；实际默认值在 `gui/settings_tab.py` 的 `DEFAULT_CONFIG` 中。修改程序默认设置时同步更新示例与 README，不提交自己的真实配置。

## 已知限制

- 当前主要按 Windows 环境实现，ChromeDriver 自动下载路径使用的是 Windows 版本。
- 抖音接口和风控策略可能变化，项目可用性依赖当前 Web API、Cookie 和签名逻辑。
- 代理设置和封面下载开关目前已经出现在配置与 UI 中，但下载请求和封面保存逻辑尚未完整接入。
- GUI 当前只注册了抖音平台；其他平台需要按 `BasePlatform` 接口自行扩展。
- 打包脚本当前面向 Windows/PyInstaller；分发包默认使用 `undetected_chromedriver`，失败后会尝试 Edge/Selenium，安装 Playwright 时也可作为兜底方案。

## 许可证

当前仓库尚未包含 `LICENSE` 文件。上传 GitHub 前建议根据你的开源意愿选择合适的许可证，例如 MIT、Apache-2.0 或仅保留私有使用声明。
