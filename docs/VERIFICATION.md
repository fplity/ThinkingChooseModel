# 验证记录与复现方式

日期：2026-10-06。本文记录本次 GitHub 同步前针对当前文件执行的检查；后续源码改变后需重新验证。

## 本机自动化测试

从 `LAYA` 目录运行：

```powershell
& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

结果：**24 项测试全部通过，进程退出码 0**。

| 范围 | 验证内容 | 方法 |
| --- | --- | --- |
| 本地协议 | 命令版本、字段白名单、无效候选、状态输出 | Python 单元测试 |
| 联系人参数 | 配置值归一化、目标标题匹配、启动参数连接 | 模拟适配器和源代码契约 |
| 分类输入 | 捕获的正文传入本地 Router，不使用演示请求文件 | 合成正文与模拟 Router |
| 会话校验 | 标题优先、同窗口切换会话、焦点变化、读取后复核 | 模拟窗口/标题序列 |
| 取消处理 | 标题识别或正文读取时暂停，跳过后续分类 | 模拟阻塞与线程同步 |
| 固定模板 | 得分范围、候选排序、WPF 模板白名单 | 模拟模型输出 |
| 草稿操作 | 标题与光标复核、无光标只复制、会话变化拒绝粘贴 | 模拟剪贴板与键盘 |
| 界面契约 | STA / WPF 入口、预览隔离、停止脚本精确入口匹配 | 源代码静态断言 |

部分测试检查源码契约，不能替代运行中的 WPF 行为和真实系统调用。线程测试及分类链路也使用合成数据，不读取实际聊天。

## Python 源码与 PowerShell 语法

Python 语法可在不产生编译缓存的情况下检查：

```powershell
@'
import ast
from pathlib import Path

for folder in ('LAYA', 'ZEV'):
    for path in Path(folder).rglob('*.py'):
        if '.venv' in path.parts or 'models' in path.parts:
            continue
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        print(f'PASS {path}')
'@ | py -3.14 -B -
```

从项目根目录检查 PowerShell 脚本：

```powershell
$scripts = Get-ChildItem -Path LAYA,ZEV -Filter '*.ps1' -File
foreach ($file in $scripts) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName, [ref]$tokens, [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors.Count -gt 0) { throw "$($file.Name): parse failed" }
    Write-Output "$($file.Name): PASS"
}
```

语法检查只证明文件可解析，不证明第三方依赖、Windows 图形环境或 API 可用。

## 仍需真实环境验收

- **微信窗口读取**：目标标题识别、正文裁剪准确性、侧栏与输入区干扰、缩放和遮挡。
- **模型加载**：CUDA 驱动、当前固定依赖、完整模型缓存、首次下载及断网运行。
- **聊天分类质量**：中文对话、有限信息和多种话题下的判断质量。
- **实际界面与草稿填入**：WPF 各缩放级别的显示、焦点、光标检测与 Ctrl+V。
- **新机器安装**：从干净克隆执行 `setup.ps1` 并完成模型加载。
- **ZEV 服务请求**：有效 TypeSafe key、模型列表、结构化响应和服务实际用量。

本次没有读取真实聊天内容，也没有使用 ZEV 密钥进行在线调用。不能把自动化测试通过表述为上述场景均已验收。
