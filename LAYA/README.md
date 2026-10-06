# LAYA 会话助手

LAYA 是 Windows 上的本地决策分类实验：PowerShell STA 进程负责 WPF 悬浮窗，Python worker 负责前台窗口检查、截图、RapidOCR 和本地 Laya 分类。界面提供三条候选回复及独立的复制、填入操作。

真实会话读取已经接入源码，但尚未完成真实微信窗口的端到端验收。回复文字为本地固定模板，模型负责排序，并不自由生成聊天回复。

## 运行条件与安装

需要 Windows 图形桌面、Windows PowerShell 和 Python 3.14。Router 配置为 `device="cuda"`，需要兼容当前 PyTorch CUDA 依赖的 NVIDIA 环境，没有 CPU 回退入口。完整模型和 OCR 缓存需另行准备。

在 Windows PowerShell 中进入本目录：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

脚本创建项目内 `.venv`、安装 [模型依赖](requirements.txt) 与 [OCR 依赖](requirements-monitor.txt)，并设置 `models/huggingface` 和 `models/ocr` 缓存目录。安装、首次加载可能需要网络。虚拟环境目录存在不等于依赖或模型安装完成；全新机器的安装流程尚未在本次同步中验收。

## 合成界面预览

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\monitor_app.ps1 -Preview -Scale 1.0
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\monitor_app.ps1 -Preview -Scale 1.25
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\monitor_app.ps1 -Preview -Scale 1.5
```

预览显示“演示会话”和合成模板。它不启动 Python、OCR 或 Router，不读取前台窗口、不截图、不访问系统剪贴板或键盘，也不调用网络；按钮只更新窗口内的模拟状态。预览就绪不等于真实识别已经可用。

## 启动真实会话读取

先打开微信并进入目标会话，在本目录执行：

```powershell
.\Run-Chat-Overlay.ps1 -TargetContact '实际联系人名称'
```

随后切回目标微信窗口，保持标题和正文可见。悬浮窗置顶，并通过窗口样式避免激活时抢走微信焦点。

| 参数 | 含义 | 要求 |
| --- | --- | --- |
| `-TargetContact` | 微信聊天区域显示的目标名称 | 使用实际标题，最长 80 字符；启动器拒绝引号和特定控制字符 |
| WPF 的 `-Preview` | 合成界面预览 | 不进行真实读取 |
| WPF 的 `-Scale` | 界面尺寸缩放 | `1.0`、`1.25`、`1.5` |

不传 `-TargetContact` 时沿用源码内的默认目标，实际使用建议总是显式指定。WPF 显示配置值，并通过 `LAYA_TARGET_CONTACT` 传递给 worker。标题比较会去除空白，保留其他字符的严格匹配；OCR 错字或附加数字、字母仍可能导致拒绝读取。

启动脚本检查是否已有同一入口的 PowerShell 进程，避免重复开启。联系人发生变化后，需要关闭并带新参数重新启动。

## 读取与分类流程

1. 确认前台进程为可见、未最小化的 `Weixin.exe`。
2. OCR 核对标题，在正文读取前后重复检查窗口和标题。
3. 读取可见内容并归一化，将最多 5,000 字符作为本地分类输入；重复内容不重新分类。
4. 在固定选项中判断下一步，将四条模板排序后显示前三条。

模型选项对应接话、询问、拒绝和等待。排序分数不是候选文案的正确率。更改 `demo-request.json` 不会改变悬浮助手的真实会话输入。

Python 与 WPF 的版本化 JSON Lines 只传递状态码、候选 ID、固定模板与分数。OCR 正文和分类输入留在 worker 内，不通过该协议传给界面，不由应用代码写入文件或发送给远程 API。

## 复制、填入与退出

- **复制**：选定模板放入系统剪贴板。
- **填入**：重新核对绑定会话和输入框光标，条件满足时使用 Ctrl+V。
- **无光标**：只复制，不尝试粘贴。
- **会话变化**：锁定并暂停，拒绝后续填入。
- **发送**：程序不按 Enter，实际发送由用户操作。

填入前应先将光标放在目标会话输入框内。这一流程受窗口和剪贴板状态影响，尚未完成真实系统交互验收。

监测按钮可暂停读取；关闭窗口会请求 worker 退出，也可以执行：

```powershell
.\Stop-Chat-Overlay.ps1
```

停止脚本根据可执行文件与命令行核对本项目的 WPF 入口，再请求窗口关闭；不会依据过期 PID 猜测进程或强制终止其他程序。

## 单次本地模型示例

```powershell
.\Run-Demo.ps1
.\Run-Demo.ps1 -InputFile '.\demo-request.json'
```

`laya_client.py` 从 JSON 文件读取非空 `state` 和 `questions`，调用本地 English Router，并打印结果。该示例与截图 worker 是两个独立入口。

## 常见状态与排错

| 状态 | 含义与处理 |
| --- | --- |
| 正在启动本机 OCR 和 Laya | 正在初始化；首次加载可能较慢 |
| 请将指定微信聊天置于前台 | 当前前台不是有效目标窗口；切回目标会话 |
| 当前标题未确认 | 尚未进入正文读取；核对联系人参数、缩放、遮挡和布局 |
| 检测到会话变化 | 绑定标题改变，已锁定并暂停；核对目标后重新启动 |
| 等待可读内容 | 正文过短或不可识别；核对窗口中的可见内容 |
| 输入框无光标，未填入 | 模板已复制；可手动粘贴或重新聚焦输入框 |
| 本地识别初始化失败 / 暂时不可用 | 检查依赖、模型缓存与 CUDA；界面目前使用通用错误状态 |

OCR 使用固定区域裁剪；侧栏、输入框文字、遮挡和不同 DPI 可能影响结果。项目不提供后台聊天抓取或微信数据库历史记录读取，中文分类效果也没有真实场景基准。

## 验证

```powershell
& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

2026-10-06 在现有环境中，24 项合成测试全部通过。真实微信 OCR、实际 CUDA 推理、WPF 渲染与草稿粘贴、新机器安装尚未在本次同步中验收。详见 [验证记录](../docs/VERIFICATION.md)。
