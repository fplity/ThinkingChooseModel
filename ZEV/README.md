# ZEV / Jev 独立 API 示例

本目录提供 Jev 结构化决策接口的轻量客户端。目录名保留为 `ZEV`，实际客户端和默认模型标识使用 Jev；源码通过 TypeSafe API 处理请求。

它独立于 LAYA：不导入 LAYA、不安装 LAYA 依赖、不读取微信窗口，也不复用本地模型或会话正文。Python 客户端只使用标准库。

## 运行条件

- Python 3.14；启动脚本使用 `py -3.14`。
- PowerShell，用于隐藏输入 API key 及运行清理。
- 能访问 `https://api.typesafe.ai/v1` 的网络。
- 对应 TypeSafe 服务的有效 API key，可从 [TypeSafe 控制台](https://console.typesafe.ai) 管理。

当前客户端使用 TypeSafe Bearer 认证，不能直接用 OpenAI key 替换。账户可用模型和权限以实际服务响应为准；本次同步没有执行在线推理请求。

## 运行示例与密钥

在本目录执行：

```powershell
.\Run-Demo.ps1
.\Run-Demo.ps1 -InputFile '.\demo-request.json'
.\Run-Demo.ps1 -ListModels
```

脚本读取当前进程已有的 `TYPESAFE_API_KEY`。未设置时，使用 `Read-Host -AsSecureString` 提示输入，密钥不会回显。脚本临时输入的环境变量在结束时清除，密钥不写入项目文件；由调用者提前设置的环境变量保留原配置。

## 请求与结果

输入 JSON 需要是对象，且包含非空 `state` 和 `questions`。未提供 `model` 时，客户端使用 `jev-latest`。

```json
{
  "model": "jev-latest",
  "state": {
    "source": "A synthetic source statement.",
    "candidate_answer": "A statement to evaluate."
  },
  "questions": {
    "verdict": {
      "type": "choice",
      "instructions": "Choose the judgment supported by the source.",
      "criteria": {
        "supported": "The source supports the statement.",
        "uncertain": "The source is insufficient."
      }
    }
  }
}
```

现有示例包含 `choice` 和 `noul` 类型。客户端提交到 `/systemone`，并格式化输出服务返回的 JSON；判断结构与用量字段以实际响应为准。接口背景见 [TypeSafe API 文档](https://api.typesafe.ai/docs)。

## 数据路径

| 操作 | HTTP 方法与路径 | 提交内容 |
| --- | --- | --- |
| 查询模型 | `GET /v1/models` | 认证信息，不提交示例状态 |
| 结构化判断 | `POST /v1/systemone` | 请求文件中的 JSON 状态、问题及模型标识 |

每次请求设置 90 秒超时，没有自动重试或批量调用逻辑。这个模块把请求发送给远程服务，不属于本地离线处理。

## 常见错误

| 情况 | 处理方式 |
| --- | --- |
| 未设置 `TYPESAFE_API_KEY` | 通过启动脚本隐藏输入，或由调用者安全设置进程环境变量 |
| JSON 文件不存在 / 无效 | 核对 `-InputFile` 路径和 JSON 语法 |
| `state` / `questions` 为空 | 按示例补齐非空对象 |
| HTTP 错误 | 核对状态码、服务响应、账号权限、额度、模型和请求格式 |
| 网络错误 | 核对网络、DNS 和服务可达性 |

客户端会报告服务返回的错误详情；分享诊断结果前需核对内容。不要将 key 放进请求 JSON、源码或提交记录。

## 文件与验证状态

- `jev_client.py`：API 请求、输入检查和结果输出。
- `Run-Demo.ps1`：密钥输入、Python 调用和临时密钥清理。
- `demo-request.json`：可修改的合成演示请求。

本次同步检查源码语法、脚本解析和 JSON 示例格式，未使用真实 key 测试认证、模型列表、服务响应或费用。LAYA 的 24 项测试不覆盖本模块的在线行为。
