# 京东小家 for Home Assistant

这是一个非官方 Home Assistant 自定义集成，用于接入京东小家 App 中的空调与智能插座设备。

本仓库的目标是把京东小家设备控制 API 封装成 Home Assistant custom integration。它不隶属于京东、京东小家或 Home Assistant。

[English README](README.md)

## 用途

这个仓库适合已经拥有通过京东小家操控的设备，并且可以从 App 本地抓包中提取会话信息的用户。这里不限定京东品牌，而是指接入京东小家的设备，例如 KFR 35GW 这类壁挂式空调，或京东智能插座。

## 功能

- 空调实体：开关、模式、目标温度、风速、上下风、睡眠模式。
- 开关实体：背光灯、屏显、强力。
- 选择实体：左右风。
- 传感器：当前温度、当前湿度、实时功率（插座）和若干诊断值。
- 智能插座：电源开关实体 + 实时功率传感器。
- 支持 UI 配置流程。
- 支持 `tgt` token 自动刷新、退避重试和恢复通知。

## 安装

### HACS

在 HACS 中添加自定义仓库：

```text
https://github.com/orangeboyChen/ha-jd-smart
```

类型选择：

```text
Integration
```

安装后重启 Home Assistant，然后进入：

```text
设置 -> 设备与服务 -> 添加集成 -> 京东小家
```

如果已经配置过京东小家，再次添加集成时会先让你选择：手动填写鉴权信息、刷新鉴权信息，或添加更多设备。

### 手动安装

把本仓库中的集成复制到 Home Assistant 配置目录：

```text
config/custom_components/jd_smart/
```

重启 Home Assistant，然后进入：

```text
设置 -> 设备与服务 -> 添加集成 -> 京东小家
```

## 配置

需要从一个可正常使用的京东小家 App 会话中获取请求参数。可以使用 Stream、Proxyman、Charles、HTTP Toolkit 或 mitmproxy 等工具抓取 HTTPS 请求。

请打开空调页面并抓取成功调用：

```text
https://api.smart.jd.com/c/service/integration/v1/getDeviceSnapshot_v1
```

尽量从同一次请求中复制所有字段。不需要手动填写 `feed_id`。认证通过后，集成会自动拉取设备列表，并允许一次选择一个或多个空调设备；已经配置过的设备不会再出现在选择列表中。暂未支持的设备品类会显示通知，其中包含提交 Pull Request 的链接。

`cookie`

抓包中的完整 `Cookie` 请求头。

`tgt`

抓包中的 `tgt` 请求头。

`pin`

可选京东账号 PIN，用于 token 刷新。

`sgm_context`

抓包中的 `Sgm-Context` 请求头。UI 中是可选项，如果抓包里有，建议填写。

`device_id`

请求 URL 中的 `device_id` 参数。留空时集成会自动生成，建议使用抓包值。

`platform`

请求 URL 中的 `plat` 参数原值。不要猜这个字段，应直接复制抓包中的值。当前已确认的 iOS 抓包值是 `iPhone`；其他平台以实际抓包为准。

`app_version`

请求 URL 中的 `app_version` 参数，同时也对应 `appversion` 请求头。

`device_model`

请求 URL 中的 `hard_platform` 参数，同时也对应 `appplatform` 请求头。

`platform_version`

请求 URL 中的 `plat_version` 参数，同时也对应 `appplatformversion` 请求头。

`channel`

抓包请求 URL 中的 `channel` 参数，例如 `channel=76161171`。请使用可用抓包中的原值。

`user_agent`

请求中的 `User-Agent`。

## 最省事的配置方式：直接粘贴 curl 命令

不必逐项抄写，表单顶部的 `curl` 输入框可以直接粘贴 App 抓包得到的整条请求。
集成会自动解析并填入 `Cookie`、`tgt`、`Sgm-Context`、`User-Agent`、
`platform`、`app_version`、`device_model`、`platform_version`、`device_id`
和 `channel`。账号 PIN 会从 Cookie 里的 `pt_pin` 自动提取，
因为刷新 token 时必须用到它。

手动填写的字段优先级高于 curl 中的值，因此可以先粘贴再单独修正某一项。
若文本无法解析，表单会提示错误并保留你已输入的内容。

## 智能插座支持

智能插座（服务端 `category_id` 为 `102010`）会创建两个实体：

- `switch` 实体：控制插座通断电；
- `sensor` 实体：显示**实时功率（单位 W）**。

插座用 `Power` 流上报开关状态（`0` 关、`1` 开），用 `CurrentPowerSum`
流上报瞬时有功功率。虽然名字里带 `Sum`，`CurrentPowerSum`
**不是累计电量**：它会随负载波动（空载为 `0`，接小功率用电器时为几瓦），
且数值本身就以瓦为单位。这类插座只上报 4 个流
（`Power`、`Mode`、`ScreenDisplay`、`CurrentPowerSum`），
因此不会创建电压或电流实体。

插座没有累计电量流，所以不会创建 `kWh` 电量实体。

控制命令与空调走同一个 `controlDevice_v1` 接口；流量名匹配不区分大小写，
因为插座用首字母大写（`Power`），而空调用小写（`power`）。

## 实体

空调实体支持电源、模式、目标温度、当前温度、当前湿度、风速、上下风和睡眠模式。目标温度范围为 18-32 摄氏度，步进 1 摄氏度。

`climate.turn_on` 使用京东设备最近一次快照中的模式，并与开机指令一起发送。如果设备没有返回可识别的模式，则使用自动模式。`climate.turn_off` 只发送关机指令。

开关实体包括背光灯、屏显和强力。

选择实体用于左右风。

传感器实体包括当前温度、当前湿度，以及 TVOC、运行时间、蜂鸣器原始值、MDP 模式、保护状态等诊断值。

## 开发

使用 `uv` 安装测试环境并运行测试：

```bash
uv sync --group test
uv run --group test pytest
```

## 免责声明

本项目是非官方集成，与京东、京东小家、Home Assistant 均无任何关联。使用本集成
可能违反京东小家的服务条款，请自行评估风险并自行承担。完整免责声明见
[DISCLAIMER.md](DISCLAIMER.md)。
