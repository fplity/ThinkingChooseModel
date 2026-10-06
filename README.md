# ThinkingChooseModel

Windows 上的决策模型实验项目，包含两个独立入口：**LAYA 本地微信会话助手**与 **ZEV / Jev 在线结构化决策示例**。项目用于体验“把上下文转换为有限选项判断”的模型能力。

LAYA 提供 PowerShell + WPF 桌面悬浮窗，通过前台微信窗口截图和 OCR 获取可见文本，再由本地模型为固定回复模板排序。ZEV 则读取独立 JSON 请求，通过 TypeSafe API 返回结构化判断。两个目录不互相调用，也不共用会话处理流程。

> 当前状态：原型。真实聊天读取已接入代码，可通过启动参数配置目标联系人；自动化验证使用合成输入，尚未完成真实微信窗口的端到端验收。候选回复来自本地固定模板，不能视为根据聊天自由生成的回复。

## 功能概览

| 模块 | 输入 | 处理方式 | 输出 | 运行条件 |
| --- | --- | --- | --- | --- |
| LAYA 悬浮助手 | 指定微信前台窗口中的可见文本 | RapidOCR + 本地 Laya English 分类 | 三条排序后的固定回复模板，可复制或尝试填入草稿 | Windows、Windows PowerShell、Python 3.14、兼容 CUDA 的 NVIDIA 环境 |
| LAYA 单次示例 | `LAYA/demo-request.json` 或指定 JSON 文件 | 本地 Laya Router | JSON 结构化判断 | LAYA Python 环境与模型缓存 |
| ZEV / Jev 示例 | `ZEV/demo-request.json` 或指定 JSON 文件 | TypeSafe `/v1/systemone` API | API 返回的 JSON，包括服务实际提供的判断与用量字段 | Python 3.14、网络、TypeSafe API key |

## LAYA 的会话处理

```text
确认前台为可见、未最小化的 Weixin.exe
    → OCR 核对配置的会话标题
    → 再次检查窗口与标题
    → OCR 读取可见内容
    → 读取后再次核对会话
    → 本地模型分类
    → 固定模板排序
    → WPF 展示三条候选
```

- **可配置会话**：`-TargetContact` 指定要识别的联系人标题，WPF 与 Python worker 使用同一配置。
- **前台与标题检查**：标题不匹配时不进入正文读取；绑定后标题变化会锁定并暂停。
- **本地分类**：正文在 Python worker 内用于分类；进程间 JSON Lines 仅传递状态、候选 ID、固定模板和分数。
- **逐条操作**：候选可复制；填入前重新核对会话与输入框光标，无光标时只复制。
- **草稿流程**：粘贴使用 Ctrl+V，不按 Enter，发送由用户操作。
- **独立预览**：`-Preview` 展示合成会话和样例，便于查看界面，不启动真实识别链路。

当前模型判断包括是否适合立即回复、可见信息支持的兴趣信号，以及接话、询问、拒绝或等待等下一步选项。实际展示的文字从四条固定模板中选取前三条；排序分数不等于回复文案正确率。

## 环境与依赖

LAYA 的当前安装脚本使用 Python 3.14，并按以下仓库声明安装依赖：

| 依赖 | 仓库中的版本声明 | 用途 |
| --- | --- | --- |
| `laya` | `0.3.6` | 决策分类 Router |
| `torch` | `2.14.0+cu132` | CUDA 推理 |
| `transformers` | `5.17.0` | 模型加载 |
| `Jinja2` / `MarkupSafe` | `3.1.6` / `3.0.3` | 模板依赖 |
| `rapidocr` | `>=3.9,<4` | 屏幕文本识别 |
| `onnxruntime` | `>=1.30,<2` | OCR 推理 |
| `mss` | `>=10,<11` | 屏幕截图 |

这些是现有源码的依赖声明。运行时明确配置 `device="cuda"`，没有 CPU 回退入口；全新机器上的依赖下载、驱动兼容性和模型加载需实际验证。模型权重、OCR 缓存与虚拟环境不随 Git 仓库分发。

ZEV 仅使用 Python 标准库，不依赖 LAYA 的虚拟环境或本地模型。

## 快速开始

### 1. 获取源码

```powershell
git clone https://github.com/fplity/ThinkingChooseModel.git
cd ThinkingChooseModel
```

私有仓库需要先使用有仓库访问权限的 GitHub 账号登录。

### 2. 只查看 LAYA 界面

在 Windows PowerShell 中运行：

```powershell
cd LAYA
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\monitor_app.ps1 -Preview -Scale 1.0
```

支持 `-Scale 1.0`、`1.25`、`1.5`。预览无需 Python 环境、模型权重或 API key。

### 3. 安装 LAYA 环境

在 `LAYA` 目录中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

安装脚本创建 `.venv`，安装固定依赖，并设置项目内模型缓存目录。安装和首次模型加载可能需要网络，运行中的聊天处理代码不向远程 API 提交聊天文本。准备好依赖与完整模型缓存后，仍需在实际环境核验离线运行。

### 4. 读取指定微信会话

打开微信桌面客户端，将目标会话保持在前台，然后在 Windows PowerShell 中执行：

```powershell
.\Run-Chat-Overlay.ps1 -TargetContact '实际联系人名称'
```

参数应与微信聊天区域显示的标题一致。启动后切回目标微信窗口，并避免悬浮窗遮挡聊天标题；程序只识别当前屏幕可见内容。联系人改变后，请关闭并使用新的参数重新启动。

停止悬浮助手：

```powershell
.\Stop-Chat-Overlay.ps1
```

更多状态说明与排错步骤见 [LAYA 使用说明](LAYA/README.md)。

### 5. 运行独立模型示例

LAYA 单次本地分类，在 `LAYA` 目录运行：

```powershell
.\Run-Demo.ps1
.\Run-Demo.ps1 -InputFile '.\demo-request.json'
```

ZEV / Jev 在线示例，在 `ZEV` 目录运行：

```powershell
.\Run-Demo.ps1
.\Run-Demo.ps1 -ListModels
```

ZEV 脚本读取已有 `TYPESAFE_API_KEY`，或以隐藏输入提示获取密钥；示例请求会发送给 TypeSafe 服务。详情见 [ZEV 使用说明](ZEV/README.md)。

## 文件结构

```text
ThinkingChooseModel/
├── README.md                       # 项目总说明
├── CHANGELOG.md                    # 本次同步的功能记录
├── docs/
│   └── VERIFICATION.md             # 验证范围与复现命令
├── LAYA/
│   ├── monitor_app.ps1             # PowerShell STA / WPF 悬浮窗
│   ├── laya_runtime.py             # Win32、截图、OCR、会话校验与分类 worker
│   ├── laya_client.py              # 单次本地分类入口
│   ├── Run-Chat-Overlay.ps1         # 正式悬浮窗启动脚本
│   ├── Stop-Chat-Overlay.ps1        # 请求本项目窗口关闭
│   ├── Run-Demo.ps1                # 本地 JSON 示例启动脚本
│   ├── setup.ps1                   # Python 环境与依赖安装
│   ├── requirements*.txt           # 模型与 OCR 依赖
│   ├── demo-request.json           # 合成示例输入
│   └── tests/test_monitor_app.py    # 会话、协议与界面契约测试
└── ZEV/
    ├── jev_client.py               # TypeSafe API 标准库客户端
    ├── Run-Demo.ps1                # 密钥输入与 API 示例启动脚本
    ├── demo-request.json           # 独立合成示例
    └── README.md
```

本地 `models/`、`packages/`、`.venv/`、`design/`、`.codex/` 以及系统或浏览器缓存不属于本次源码交付。

## 验证状态

2026-10-06 在现有本机 LAYA 环境运行 **24 项自动化测试，全部通过**。测试覆盖协议校验、目标联系人配置、正文进入本地分类器、标题变化、暂停取消、草稿填入保护、固定模板候选、预览隔离和启动停止脚本契约。

这些测试使用合成文本及模拟适配器，不代表真实微信截图/OCR、CUDA 模型加载、粘贴操作或新机器安装已通过。完整复现命令与范围见 [验证记录](docs/VERIFICATION.md)。

## 已知限制

- 只处理 `Weixin.exe` 的前台可见窗口，不支持后台聊天抓取、聊天列表批量扫描或微信数据库历史记录读取。
- OCR 使用固定窗口区域裁剪；微信布局、缩放、遮挡以及其他界面文字可能影响标题匹配和正文质量。读取结果仍需实际核对。
- 联系人采用严格标题匹配，OCR 错字或附加字符可能使读取被拒绝；这不是已适配所有微信版本的通用接口。
- Laya 使用 English checkpoint，中文会话的分类效果没有真实场景基准。
- 候选回复是固定模板；没有接入通用聊天生成模型。
- 真实微信端到端、全新环境安装和 ZEV 在线调用均未在本次上传验证中执行。

## 数据与仓库范围

LAYA 的应用代码将聊天截图、OCR 文本和分类输入保留在本地内存中，不把正文写入项目文件或 worker 的 JSON Lines。ZEV 的 JSON 请求则会发送到配置的 TypeSafe API；两个数据路径相互独立。

Git 仓库包含源码、合成示例、依赖声明、测试与使用文档。认证文件、API key、运行日志、模型权重、浏览器用户资料和聊天截图不应提交。根目录 `.gitignore` 已覆盖这些本地目录及常见运行产物。

第三方依赖和模型遵循各自许可证；本仓库未新增开源许可证授权。
