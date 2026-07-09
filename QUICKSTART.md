# On-the-Fly MCP 快速入门

## 是什么

On-the-Fly MCP Server 是一个**能力构建编排器（Capability Construction Orchestrator）**。它不做业务执行，也不直接生成代码。它的职责是：

- 管理 `otf_tools/` 下各系统的状态
- 根据 OpenAPI 规范生成结构化的 **Capability Construction Workflow (CCW)**
- 让 Code Agent 按照 CCW 的合约约束来编写和注册 CLI 工具

## 前提条件

- Python 3.10+
- `uv`（推荐）或 `pip`

## 安装

### 使用 uv（推荐）

```bash
cd onthefly
uv sync
```

### 使用 pip

```bash
pip install -r requirements.txt
pip install -e .
```

## 配置 MCP 客户端

在 MCP 客户端配置文件中添加（以 CodeBuddy 为例，路径替换为实际项目目录）：

```json
{
  "mcpServers": {
    "onthefly": {
      "command": "uv",
      "args": [
        "--directory",
        "<onthefly-local>",
        "run",
        "python",
        "-m",
        "onthefly_mcp.server"
      ]
    }
  }
}
```

> **注意**：必须以 `python -m onthefly_mcp.server` 方式启动，不能直接用 `python onthefly_mcp/server.py`。

## 核心概念

```
otf_tools/          ← 工作区根目录
├── .otf/           ← 内部锁文件
└── track/          ← 一个系统（可多个）
    ├── onthefly.md ← 系统摘要（name / tags / api_version / description）
    ├── swagger.json← OpenAPI 规范
    ├── cli.md      ← CLI 注册表（记录每个命令的状态）
    └── cli/        ← 生成的 CLI 脚本目录
```

## 7 个工具

| 工具 | 作用 |
|---|---|
| `init_system` | 从 `swagger.json` 创建一个新系统 |
| `discover_system` | 发现工作区中所有已注册系统 |
| `get_system_context` | 获取系统的完整上下文 |
| `construct_workflow` | 为指定命令生成构建/复用工作流 |
| `validate_workflow` | 校验工作流是否符合合约 |
| `register_cli` | 注册 CLI 验证结果，更新状态 |
| `resolve_escalation` | 人工审核解决阻塞，恢复 stable 状态 |

## 典型使用流程

以下是创建一个新 CLI 工具并注册的完整链路。

### 第一步：准备 swagger.json

在项目根目录放一个 OpenAPI 规范的 swagger.json 文件（须包含 `info.version`）。

### 第二步：初始化系统

```
init_system { system: "track", swagger_path: "swagger.json", tags: ["测试平台"], description: "测试平台 API" }
```

完成后会在 `otf_tools/track/` 下写入：

- `onthefly.md` — 系统摘要（自动从 swagger.json 提取 api_version）
- `swagger.json` — 拷贝的规范文件
- `cli.md` — 空注册表 `{commands: []}`

### 第三步：发现系统

```
discover_system {}
```

验证系统已成功注册，返回系统中已注册的 name、tags、api_version。

### 第四步：获取上下文

```
get_system_context { system: "track" }
```

返回 onthefly.md、cli.md、swagger.json 的原始内容及实时状态。

### 第五步：构造工作流

```
construct_workflow { system: "track", command: "list_issues" }
```

返回一个完整的 CCW，包含：

- `goal.mode`: `"build"`（新命令初次创建）或 `"reuse"`（已有且未过期）
- `auth`: 环境变量 `OTF_TRACK`，JSON 格式，凭证读取模板
- `constraints`: 8 项约束（如 argsparse、stdout JSON、exit code 合约等）
- `validation_contract`: `--help` + `--dry-run` 两步验证步骤
- `artifact.create`: 需创建的文件列表

Code Agent 收到 CCW 后，应按照约束编写 CLI 脚本。

### 第六步：注册验证结果

CLI 脚本编写并验证通过后：

```
register_cli {
  system: "track",
  command: "list_issues",
  outcome: "success",
  attempts: 1,
  api_version: "2.0"
}
```

- `outcome: "success"` → 状态设为 `stable`
- `outcome: "failure"` 且 attempts < 3 → 状态设为 `draft`，允许重试
- `outcome: "failure"` 且 attempts ≥ 3 → 状态设为 `draft` + `needs_human_review: true`，需人工介入

### 第七步：人工审核（如需要）

如果命令被阻塞等待人工审核：

```
resolve_escalation {
  system: "track",
  command: "list_issues",
  resolution_note: "手动验证通过，参数名与 API 文档一致"
}
```

状态恢复为 `stable`。

## 命令行验证

```bash
# 编译检查
python -m compileall onthefly_mcp

# 直接启动 MCP Server
uv run python -m onthefly_mcp.server
```

## 常见问题

### init_system 报 "system_already_exists"

目标系统目录已存在。需要先手动删除 `otf_tools/{system}/` 目录再重试。

### CLI 状态变成 "draft" + needs_human_review

表示 3 次自动重试均失败。需要用 `resolve_escalation` 人工审核后恢复。

### swagger.json 必须有 info.version

OpenAPI 规范在 `info.version` 中定义版本号，如果缺失初始化会失败。
