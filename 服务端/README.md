# 青柠视频下载支付与授权服务

这是一套独立的新服务。客户端使用项目根目录 `payment_kit/`，服务端使用本目录 `payment_server/`。旧 Node.js 支付代码和 `payment_reuse/` 只在本地保留为参考，不参与运行，也不提交到 Git。

## 已实现

- 微信 APIv2 Native 扫码下单，二维码以 PNG Base64 返回。
- 套餐、订单状态、授权查询和支付回调。
- 电脑 UUID 登记和身份识别；Windows 优先使用 SMBIOS UUID，备用 MachineGuid，新版不再依赖令牌，重新下载软件不重置试用。保留旧客户端令牌接口兼容，升级见 [UUID 身份更新部署](../docs/UUID身份更新部署.md)。
- 默认 3 天试用；付费续期从当前到期时间与现在中较晚的时间开始计算。
- 支付金额、商户、AppID、币种及签名校验；回调必须明确为成功。
- 原子更新订单与授权；同一付款重复通知不重复加时，并发续费不会丢失天数。
- 主动查询微信订单补偿回调丢失。付款查询失败仍为待核实，不推断未付款。
- 同设备同套餐的未过期订单复用，重试不会创建新的可付款订单号。
- 客户端后台授权验证，使用期限到期后点击下载或失败重试才显示单个窗口，直接选套餐并扫码付款；错误和结果在窗口内显示，付费授权确认后自动继续下载，日常导航不显示授权入口。
- 独立启动上报、成功视频下载数量累加、持久化待上报队列。服务器收到下载增量后直接累加，不保存去重记录。
- 公开 `/stats` 页面和 `/api/pc/stats` 汇总接口：今日活跃、近 7 天活跃、近 30 天活跃、累计用户数，范围按北京时间自然日计算。
- 公开 `/videoDown.txt` 更新信息接口：每次读取项目根目录同名 UTF-8 JSON 文件，禁止缓存，修改保存后无需重启。可选 `announcement` 提供启动公告；旧客户端忽略该字段，缺失或类型误填不影响原有版本检查。

当前客户端仅通过 `python main.py` 做源码测试，只有用户明确要求发布时才打包。更新信息首次部署只需上传 `payment_server/app.py` 和 `videoDown.txt` 并重启项目，具体步骤见 [更新信息部署](../docs/更新信息部署.md)。

## 安装与启动

从项目根目录运行。正式部署建议为服务端创建单独虚拟环境：

```powershell
python -m venv 服务端/.venv
.\服务端\.venv\Scripts\python.exe -m pip install -r 服务端/requirements.txt
.\服务端\.venv\Scripts\python.exe 服务端/main.py
```

Linux 同样可以使用 Python 3.10+ 创建虚拟环境，在 `服务端` 目录运行 `uvicorn main:app --host 127.0.0.1 --port 3006`。默认绑定本机，配合反向代理开放 HTTPS。

当前项目 `venv` 已安装并验证所需依赖，也可直接用 `.\venv\Scripts\python.exe 服务端/main.py`。

数据库默认是本目录的 `payment.db`，采用 SQLite，支持一台服务器上的多线程和多进程。当前不连接旧 MySQL。数据库仅有 `users`（用户）和 `orders`（订单）两张业务表。升级前自动保存 SQLite 完整备份，再将旧 `payment_devices`、`payment_orders` 原地改名并移除 `payment_download_reports`；保留用户、订单、下载总数与授权，并按需补齐 `last_opened_at` 和 `downloaded_video_count`，迁移可重复执行。部署前仍应停止项目并手动备份数据库。具体升级步骤见 [统计更新部署](../docs/统计更新部署.md)。文件需要持久化并备份，不能放在容器临时目录或多机器共享网络文件系统上。

## 本地演示，不收款

```powershell
$env:PAYMENT_MODE = 'demo'
$env:PAYMENT_TRIAL_DAYS = '0'
.\venv\Scripts\python.exe 服务端/main.py
```

在另一个终端启动客户端：

```powershell
$env:QINGNING_PAYMENT_API_BASE = 'http://127.0.0.1:3006/api/pc'
.\venv\Scripts\python.exe main.py
```

演示结束后在客户端终端移除 `QINGNING_PAYMENT_API_BASE`，再启动客户端连接正式服务。

解析链接后点击下载，使用期限为 0 天的演示设备会弹出到期支付窗口，选套餐后点击“立即支付”。演示返回文字占位图，无需扫码，约 6 秒后模拟付款成功；付费授权确认后会自动关闭窗口并继续下载。演示数据库默认为 `payment-demo.db`，服务只接受本机请求，拒绝支付回调，也没有“模拟付款成功”的公网接口。不要把演示数据库用于正式服务。试用设置只影响新设备，已有有效授权的设备不会因为将试用设为 0 而到期。

原有软件更新检查仍会运行；更新服务器不通时，按项目原有逻辑提示并退出。

切换正式模式前，关闭演示服务，在新终端运行正式服务；或者移除演示环境变量：

```powershell
Remove-Item Env:PAYMENT_MODE -ErrorAction SilentlyContinue
Remove-Item Env:PAYMENT_TRIAL_DAYS -ErrorAction SilentlyContinue
```

## 正式商户配置

复制 `.env.example` 为本目录 `.env`，在服务器本机填入：

- `PAYMENT_MODE=wechat`
- `WX_APP_ID`：与商户号关联且具有对应 Native 支付权限的 AppID。
- `WX_MCH_ID`：商户号。
- `WX_API_V2_KEY`：32 字节 APIv2 密钥，APIv3 密钥不能代替。
- `WX_NOTIFY_URL`：新服务公网 HTTPS 地址，例如 `https://新支付域名/notify`，不能带查询参数。

不需要把真实密钥发到聊天中。服务端不读取 `支付代码.txt`，不会自动使用其中的旧商户配置。`.env`、参考支付代码、数据库和客户端凭据已经加入忽略规则。

通过反向代理向新服务转发 `/api/pc/*`、`/stats` 和 `/notify`，不要继续指向旧字幕软件接口。只信任实际反向代理的转发 IP，例如本机代理使用 `uvicorn main:app --host 127.0.0.1 --port 3006 --proxy-headers --forwarded-allow-ips=127.0.0.1`；对公开接口设置请求频率限制和请求体大小限制。API 文档 `/docs` 建议仅供管理网络访问。

正式客户端已默认连接 `https://douyindown.secs.cc/api/pc`，项目根目录的 `payment_client.json` 也已配置此地址。需要更换域名时修改该文件；配置示例为 `payment_client.example.json`。也可通过环境变量 `QINGNING_PAYMENT_API_BASE` 指定地址。远程服务必须使用 HTTPS；HTTP 仅用于本机调试。

打包脚本会将根目录 `payment_client.json` 复制到发布目录。正式发布前需要配置这个文件或修改 `core/payment_config.py` 中的默认地址。商户配置永远只放在服务器，不能打包进客户端。新客户端将电脑标识、支付令牌和下载上报队列保存于 `%LOCALAPPDATA%\QingningVideoDownloader\`，更换软件目录不会改变身份。首次升级会导入原软件目录 `payment_credentials/` 的旧令牌，因此升级前不要删除旧凭据；更换 API 地址会使用独立支付令牌。

## 修改价格与期限

修改 `payment_server/config.py` 的 `PLANS`，金额为整数“分”：

| 套餐编号 | 当前金额 | 当前授权天数 |
| --- | --- | --- |
| plan_30d | 1000 分（10 元） | 30 |
| plan_365d | 6800 分（68 元） | 365 |
| plan_999d | 12800 分（128 元） | 999 |

999 天按实际期限显示，不称为永久。重启服务后，新订单使用新价格；已有订单保留原始金额和天数。客户端下单仅提交套餐编号，不能提交付款金额。

第一版统一公开试用时长，不接受客户端自己声明淘宝渠道来延长试用。调整 `PAYMENT_TRIAL_DAYS` 只影响新登记设备；新 UUID 对应电脑，不能将设备数量视为真实人数。旧随机 UUID 凭有效令牌关联到电脑 UUID，原数据库 ID、首次登记时间、付费订单和到期时间保留；冲突时不自动合并授权。

## 接口约定

保留原接口路径。20261017 起新版通过 `X-Device-UUID` 识别用户，无需设备令牌；仅兼容旧客户端时使用原 Bearer 令牌。

1. `POST /api/pc/register`：新版提交电脑 UUIDv5 `deviceId`，返回 `deviceId`。同 UUID 重复登记沿用原用户，不重置试用或付费授权。可选 `legacyDeviceId` 用于关联早期随机设备编号并迁移其订单；两条已有记录冲突时返回 409，不自动合并。旧客户端仍可提交 `deviceToken` 并收到原样返回值；已有 UUID 不再校验令牌是否一致，原数据库令牌哈希保留。
2. `GET /api/pc/plans`：公开套餐数组，`price` 为人民币元的字符串。
3. `GET /api/pc/license/{deviceId}`：返回布尔值 `valid`、带 UTC 时区的 `expireTime`、`status`、`isPaid`、`demo`。`status` 为 trial/active/expired/disabled。`isPaid` 表示有成功付款记录，使用权限仍以 `valid` 为准。
4. `POST /api/pc/create-order`：提交 `deviceId`、`planId`，返回 `success`、`orderId`、二维码图片 `qrcode`、订单 `expireTime` 和 `demo`。
5. `GET /api/pc/order-status/{orderId}`：返回 PENDING/SUCCESS/CLOSED/FAILED，仅订单所属设备可查。
6. `POST /notify`：微信 V2 XML 回调，返回 XML SUCCESS/FAIL。
7. `POST /api/pc/startup`：携带 `X-Device-UUID`，提交相同的电脑 UUIDv5 `uuid`，更新 `last_opened_at`。首次联网登记时间复用 `first_seen`，对外命名 `first_opened_at`。
8. `POST /api/pc/download-count`：携带 `X-Device-UUID`，提交相同的 `uuid` 和正整数增量 `count`，原子累加成功视频数量。旧版请求附带的 `report_id` 会被忽略，每次收到有效请求都累加；网络回复丢失后的重试可能重复计数。
9. `GET /api/pc/stats`：公开汇总 `today_active`、`active_7d`、`active_30d`、`total_users`，另含 UTC `as_of` 和 `Asia/Shanghai` 时区说明。
10. `GET /stats`：无需登录的四卡片统计页面，提供刷新按钮，调用上述汇总接口。
11. `GET /videoDown.txt`：无需令牌的更新信息 JSON，保留 `version_number`、`百度`、`夸克`、`info`、`forced_upgrade`，新增可选纯文本 `announcement`。可选公告缺失或误填时忽略；原有必要字段缺失、文件不存在或格式错误返回 503。已部署接口的公告升级只需覆盖 `app.py`，重启后在原线上 JSON 中追加公告字段，保留线上版本号和链接。

3–5 和 7–8 携带 `X-Device-UUID: <电脑 UUIDv5>`，接口中的设备编号必须与请求身份一致。缺少 UUID 请求头时兼容原 `Authorization: Bearer <deviceToken>`。有效旧令牌可继续完成随机编号到电脑 UUID 的首次关联。`payment_kit/api.py` 自动完成登记及启动上报，新版不发送令牌。普通授权查询、下载上报和付款轮询不改变 `last_opened_at`。

`users` 用户表保留 `id`、`device_id`（业务 `uuid`）、`first_seen`（业务 `first_opened_at`）、`last_opened_at`、`downloaded_video_count` 和 `expires_at`，以及现有内部支付验证字段。尚无统计字段的旧记录，最近打开时间留空、下载总数初始化为 0；已有统计字段及累计数量保留。累计用户数包含旧记录及测试记录；活跃统计从新版启动上报开始，不补造历史数据。近 7／30 天包含今天及前 6／29 个自然日，只靠最近打开时间计算当前范围，不保存历史日活表。

客户端默认在线验证，不在离线时自动放行。订单成功后再次查询授权；接口失败不引导重复付款。已运行的下载继续完成，新的下载批次和失败重试需要重新检查。

付款接口出现超时可能意味着微信已受理订单，服务会保留订单用于补查，不能直接称其失败。客户端关闭购买窗口或停止等待不会撤销微信订单。二维码有效期默认 30 分钟；延迟到达且验证正确的成功回调仍可开通。授权刷新从最近 5 笔待核实订单中补查 1 笔，优先从未检查或最久未检查的订单，避免多个请求拖慢验证；常规查单每笔间隔至少 15 秒。

## 验证与后续

客户端已配置线上服务。2026-10-08 联调确认新安装领取 3 天试用、套餐按服务端实际天数显示、关闭购买窗口恢复操作、复用已付款测试订单完成轮询及付费授权刷新。软件自身的新安装身份与已付测试身份分别保存，没有再次创建微信订单。自动检查覆盖未授权下载及失败重试拦截、禁用设备、网络异常和试用状态不能冒充付款开通成功。

联调期间线上 `plan_30d` 为 0.01 元、1 天。正式发布前在服务器恢复为 10 元、30 天并重启服务；客户端从接口读取价格，无需重新编译以调整套餐。真实微信通知是否直接成功还需核对服务器支付回调日志；订单查询补偿成功不能单独证明回调已通过。

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

自动验证使用临时数据库和模拟微信响应。96 项测试覆盖支付回归、电脑 UUID 稳定性、旧身份迁移及订单保留、下载成功计数及失败排除、持久化重试、直接累加、并发累计、两表及三表数据库升级、北京时间边界、统计公开字段、增量迁移备份、更新文件实时读取及错误处理，以及隐藏授权入口、单窗口套餐和二维码、关闭拦截、付款后自动下载及非法日期只读检查。另用本机演示服务验证了真实 Qt 后台初始化、重复启动和上报、浏览器页面显示与刷新，以及 `main.py` 源码启动读取更新信息的流程。服务端更新需上传并重启后生效。微信 V2 官方公开签名示例用于协议测试。

已完成公网 HTTPS 接口、0.01 元真实付款、订单成功和授权准确增加 1 天的验证，也已验证打包后的客户端能启动并领取试用。真实微信回调是否直接成功仍需查看服务器日志。退款、订单历史、管理后台、跨设备授权、账号系统和抵抗修改客户端的强制许可机制不包含在这一版。

官方协议参考：[V2 统一下单](https://pay.wechatpay.cn/doc/v2/merchant/4011935214)、[V2 支付通知](https://pay.wechatpay.cn/doc/v2/merchant/4011935221)、[V2 安全规范](https://pay.wechatpay.cn/doc/v2/institution/4011985775)。
