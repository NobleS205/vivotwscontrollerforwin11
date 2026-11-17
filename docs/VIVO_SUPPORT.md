# vivo / iQOO 型号支持自查表

> 本文件由 `python tools/vivo_audit.py --doc` 生成，数据直接取自代码里的
> `vivo/vivo_models.MODELS`（41 个型号），因此**不会出现文档与程序不一致**。
> 能力声明 ≠ 命令字，命令是否可用见文末。

> v3.35 有两处按官方规格补正：TWS 5e 增加空间音频（上游漏标 FeatureID 12）；
> `vivo 耳机` / `iQOO 耳机` 对应 2026 年头戴新品（系统名 `vivo Headphones` /
> `iQOO Headphones`），协议走独立的 `HEADPHONES_V4` 实验分支（套用 TWS 5 v4）。

## 1. 全部型号能力表

图例：`✓` 支持 / `常开` 硬件常开、无可切换控件 / `—` 明确不支持。

协议列带 `*` 的型号使用单独的 wire profile，其余为家族默认 v4。

| 型号 | 协议 | 降噪/通透 | 空间音频 | 低延迟 | 双连 | 查找 | 佩戴 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| iQOO TWS 1 | v4 | ✓ | — | — | — | ✓ | — |
| iQOO TWS 1e | v4 | ✓ | — | — | — | ✓ | — |
| iQOO TWS 1i | v4 | — | — | — | — | ✓ | — |
| iQOO TWS 2 | v4 | ✓ | — | ✓ | — | ✓ | — |
| iQOO TWS 5 | v4 | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| iQOO TWS 5e | v4 | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| iQOO TWS 5i | v4 | — | — | ✓ | ✓ | ✓ | — |
| iQOO TWS Air | v4 | — | — | — | — | ✓ | — |
| iQOO TWS Air Pro | v4 | ✓ | — | — | — | ✓ | — |
| iQOO TWS Air2 | v4 | — | — | — | — | ✓ | — |
| iQOO TWS Air3 | v4 | — | — | ✓ | — | ✓ | — |
| iQOO TWS Air3 Pro | v4 | ✓ | — | ✓ | — | ✓ | — |
| iQOO 耳机 | v4* | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| vivo TWS 1 | v4 | — | — | — | — | — | — |
| vivo TWS 2 | v4 | ✓ | — | — | — | ✓ | — |
| vivo TWS 2e | v4 | — | — | — | — | ✓ | — |
| vivo TWS 3 | v4 | ✓ | — | — | — | ✓ | — |
| vivo TWS 3 Pro | v4 | ✓ | ✓ | — | — | ✓ | — |
| vivo TWS 3e | v3* | ✓ | — | — | 常开 | ✓ | ✓ |
| vivo TWS 3i | v4 | — | — | — | — | ✓ | — |
| vivo TWS 4 | v4 | ✓ | ✓ | ✓ | — | ✓ | — |
| vivo TWS 4 Hi-Fi | v4 | ✓ | ✓ | ✓ | — | ✓ | — |
| vivo TWS 5 | v4 | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| vivo TWS 5 Hi-Fi | v4 | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| vivo TWS 5 Pro | v4 | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| vivo TWS 5e | v4 | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| vivo TWS 5i | v4 | — | — | ✓ | ✓ | ✓ | — |
| vivo TWS A1 | v4 | — | — | — | — | ✓ | — |
| vivo TWS A1 Pro | v4 | ✓ | — | — | — | ✓ | — |
| vivo TWS A2 | v4 | — | — | — | — | ✓ | — |
| vivo TWS A3 | v4 | — | — | — | — | ✓ | — |
| vivo TWS A4 | v4 | — | — | ✓ | — | ✓ | — |
| vivo TWS A5 | v4 | — | — | ✓ | ✓ | ✓ | — |
| vivo TWS Air | v4 | — | — | — | — | ✓ | — |
| vivo TWS Air Pro | v4 | ✓ | — | — | — | ✓ | — |
| vivo TWS Air2 | v4 | — | — | — | — | ✓ | — |
| vivo TWS Air3 | v4 | — | — | ✓ | — | ✓ | — |
| vivo TWS Air3 Pro | v3* | ✓ | — | ✓ | — | ✓ | — |
| vivo TWS Neo | v4 | — | — | — | — | ✓ | — |
| vivo TWS X1 | v4 | ✓ | — | — | — | ✓ | — |
| vivo 耳机 | v4* | ✓ | ✓ | ✓ | ✓ | ✓ | — |

## 2. 明确不支持的功能（按功能列）

### 降噪/通透（18 / 41 个型号不支持）

iQOO TWS 1i、iQOO TWS 5i、iQOO TWS Air、iQOO TWS Air2、iQOO TWS Air3、vivo TWS 1、vivo TWS 2e、vivo TWS 3i、vivo TWS 5i、vivo TWS A1、vivo TWS A2、vivo TWS A3、vivo TWS A4、vivo TWS A5、vivo TWS Air、vivo TWS Air2、vivo TWS Air3、vivo TWS Neo

### 空间音频（30 / 41 个型号不支持）

iQOO TWS 1、iQOO TWS 1e、iQOO TWS 1i、iQOO TWS 2、iQOO TWS 5i、iQOO TWS Air、iQOO TWS Air Pro、iQOO TWS Air2、iQOO TWS Air3、iQOO TWS Air3 Pro、vivo TWS 1、vivo TWS 2、vivo TWS 2e、vivo TWS 3、vivo TWS 3e、vivo TWS 3i、vivo TWS 5i、vivo TWS A1、vivo TWS A1 Pro、vivo TWS A2、vivo TWS A3、vivo TWS A4、vivo TWS A5、vivo TWS Air、vivo TWS Air Pro、vivo TWS Air2、vivo TWS Air3、vivo TWS Air3 Pro、vivo TWS Neo、vivo TWS X1

### 低延迟（22 / 41 个型号不支持）

iQOO TWS 1、iQOO TWS 1e、iQOO TWS 1i、iQOO TWS Air、iQOO TWS Air Pro、iQOO TWS Air2、vivo TWS 1、vivo TWS 2、vivo TWS 2e、vivo TWS 3、vivo TWS 3 Pro、vivo TWS 3e、vivo TWS 3i、vivo TWS A1、vivo TWS A1 Pro、vivo TWS A2、vivo TWS A3、vivo TWS Air、vivo TWS Air Pro、vivo TWS Air2、vivo TWS Neo、vivo TWS X1

### 双连（29 / 41 个型号不支持）

iQOO TWS 1、iQOO TWS 1e、iQOO TWS 1i、iQOO TWS 2、iQOO TWS Air、iQOO TWS Air Pro、iQOO TWS Air2、iQOO TWS Air3、iQOO TWS Air3 Pro、vivo TWS 1、vivo TWS 2、vivo TWS 2e、vivo TWS 3、vivo TWS 3 Pro、vivo TWS 3i、vivo TWS 4、vivo TWS 4 Hi-Fi、vivo TWS A1、vivo TWS A1 Pro、vivo TWS A2、vivo TWS A3、vivo TWS A4、vivo TWS Air、vivo TWS Air Pro、vivo TWS Air2、vivo TWS Air3、vivo TWS Air3 Pro、vivo TWS Neo、vivo TWS X1

### 查找（1 / 41 个型号不支持）

vivo TWS 1

### 佩戴（40 / 41 个型号不支持）

iQOO TWS 1、iQOO TWS 1e、iQOO TWS 1i、iQOO TWS 2、iQOO TWS 5、iQOO TWS 5e、iQOO TWS 5i、iQOO TWS Air、iQOO TWS Air Pro、iQOO TWS Air2、iQOO TWS Air3、iQOO TWS Air3 Pro、iQOO 耳机、vivo TWS 1、vivo TWS 2、vivo TWS 2e、vivo TWS 3、vivo TWS 3 Pro、vivo TWS 3i、vivo TWS 4、vivo TWS 4 Hi-Fi、vivo TWS 5、vivo TWS 5 Hi-Fi、vivo TWS 5 Pro、vivo TWS 5e、vivo TWS 5i、vivo TWS A1、vivo TWS A1 Pro、vivo TWS A2、vivo TWS A3、vivo TWS A4、vivo TWS A5、vivo TWS Air、vivo TWS Air Pro、vivo TWS Air2、vivo TWS Air3、vivo TWS Air3 Pro、vivo TWS Neo、vivo TWS X1、vivo 耳机

## 3. 功能实现状态

| 功能 | 命令 | 本程序 | 上游依据 | 风险 |
| --- | --- | --- | --- | --- |
| 降噪 / 通透 | 0x0130 / 0x0230 | 已实现：设置 + 查询 + 回包解析 | 真机实测（TWS 3e / TWS 5） | 低 |
| 空间音频 | 0x0139 / 0x0239 | 已实现：设置 + 查询 + 回包解析 | 仅反编译推断，未真机验证 | 中（界面标「实验」） |
| 低延迟 | 0x0151 / 0x0251 | 已实现：设置 + 查询 + 回包解析 | 反编译推断；TWS 3e 实测查询超时 | 中（界面标「实验」） |
| 双连 | 0x0249 / 0x014A / 0x014C / 0x014D | 已实现：列表查询 + 全量列表下发（断开 / 切回）；0x014C 开关与 0x014D 移除未接 UI | TWS 5 真机验证；TWS 3e 三项均超时（强制常开） | 中（界面标「实验」） |
| 查找耳机 | 0x0120 | 已实现：开始 / 停止响铃（该命令无查询） | 真机实测 | 低 |
| 佩戴 | 0x020D / 0x0103 | 已实现：实时状态解析 + 检测开关 | 真机实测（位段经 APK 交叉确认） | 低 |
| 音效 EQ | 0x0118 / 0x0218 | 已实现：5 档预设切换 + 回包同步 | 真机实测；官方配置表里没有这一位，故不按型号隐藏 | 低 |
| 固件 / 型号 / 序列号 | 0x021C / 0x021B / 0x0215 | 固件已接入界面；型号 / 序列号仅解析，暂未显示 | 真机实测 | 低 |

### 官方协议里有、本程序与上游都尚未实现

| 功能 | 命令 |
| --- | --- |
| 抗风噪 | `0x015E / 0x025E` |
| 听力保护 | `0x0152 / 0x0252` |
| 麦克风模式 | `0x0104` |
| AOV 模式 | `0x0105` |
| 音量调整 | `0x0106` |
| 自动播放 | `0x010B / 0x020B` |
| 高清编解码器 | `0x0157 / 0x0257` |
| 空间音频预览 | `0x013E / 0x023E` |
| 环境光效 | `0x017B / 0x027B` |
| 自定义音效 | `0x016A` |
| 快速语音切换 | `0x0145` |
| 音量自动适配 | `0x016C` |

## 4. 已知风险 / 待真机确认

- **TWS 4 系的协议档位**：上游 PROTOCOL.md 提到「TWS 4 系 / Air3 Pro / TWS 3e 走注册链（v3）」分支，但上游代码 `MODEL_PROFILES` 里只给了 Air3 Pro 与 TWS 3e，TWS 4 实际落到家族默认 v4。本程序与上游代码一致；若真机上 TWS 4 不推状态，优先怀疑这里。
- **空间音频命令**：0x0139 / 0x0239 只有反编译依据，没有公开的真机抓包；TWS 3 Pro / TWS 4 / TWS 5e 的额外声明来自官方产品页，不是 tws_config。
- **头戴实验协议**：vivo Headphones（XE W42）/ iQOO 头戴电竞耳机在上游只有能力表，没有专门驱动，也没有抓包。本程序按 TWS 5 的 v4 帧层尝试（HEADPHONES_V4），降噪 / 空间音频 / 双连若有命令超时，优先从这几条开始抓包。
- **低延迟命令**：0x0151 / 0x0251 同样是推断；TWS 3e 实测查询直接超时。
- **双连开关**：0x014C（总开关）与 0x014D（移除配对）未接 UI，因为会改耳机里的配对记录，误触代价高；目前只做「列表 + 断开 / 切回」。
