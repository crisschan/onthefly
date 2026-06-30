## 🎯 目标
实现一个 **Python stdio MCP Server**，作为：

Capability Construction Orchestrator（能力构建编排器）

它不执行业务、不生成代码、不写文件，只负责生成 **Capability Construction Workflow（CCW）**，并为 Code Agent 提供构建契约。

# 1. 总体架构
## 1.1 系统结构
```plain
Codex / Claude Code
        │
        │ MCP stdio
        ▼
+--------------------------------------------------+
| On-the-Fly MCP Server (Python)                   |
|                                                  |
|  Stateless Control Plane                        |
|                                                  |
|  Tools:                                          |
|    - discover_system                             |
|    - get_system_context                          |
|    - construct_workflow                         |
|    - validate_contract                          |
|                                                  |
|  ❌ no execution                                 |
|  ❌ no file write                                |
|  ❌ no LLM calls                                 |
+---------------------┬----------------------------+
                      │
                      ▼
           Project Working Directory (cwd)
                └── otf_tools/
```

---

# 2. 工作目录模型（强约束）
## 2.1 cwd 即工程边界
MCP Server 永远使用：

```plain
os.getcwd()
```

作为唯一工程上下文。

## 2.2 自动初始化规则
首次任何 tool 调用时：

```plain
if not exists("./otf_tools"):
    create_dir("./otf_tools")
    create_dir("./otf_tools/.otf")
```

---

## 2.3 目录结构
```plain
otf_tools/
├── .otf/                      # runtime hidden state
│   ├── runtime.yaml
│   ├── registry_cache.json
│   └── swagger_hashes/
│
└── ci/ (example system)
    ├── onthefly.md
    ├── swagger.json
    ├── cli.md
    └── cli/*
```

---

# 3. System Model（单系统隔离）
每个 system：

+  独立 onthefly.md 
+  独立 swagger.json 
+  独立 cli.md 
+  独立 cli/ 

❗禁止跨 system workflow

# 4. MCP Tools 规范
## 4.1 discover_system
### 功能
扫描 `otf_tools/*/onthefly.md`

### 输入
```plain
{}
```

### 输出
```plain
{
  "systems": [
    {
      "system": "ci",
      "name": "CI Pipeline",
      "tags": ["构建", "部署"],
      "api_version": "2.0.0"
    }
  ]
}
```

---

## 4.2 get_system_context
### 输入
```plain
{
  "system": "ci"
}
```

### 输出
```plain
{
  "system": "ci",
  "onthefly": "...yaml...",
  "cli_registry": "...yaml...",
  "swagger": "...json...",
  "status": {
    "cli_count": 3,
    "stale_count": 0
  }
}
```

---

## 4.3 construct_workflow （核心）
### 输入
```plain
{
  "system": "ci",
  "command": "create_pipeline"
}
```

---

### 输出（CCW Workflow）
```plain
workflow_type: capability_construction
version: 1

system: ci

goal:
  artifact: cli
  command: create_pipeline

context:
  api_version: "2.0.0"

inputs:
  - read: otf_tools/ci/onthefly.md
  - read: otf_tools/ci/swagger.json
  - read: otf_tools/ci/cli.md

constraints:
  - cli_spec_v1
  - argparse
  - stdout_json
  - stderr_logs
  - exit_code_contract

artifact:
  create:
    - otf_tools/ci/cli/create_pipeline.py

  update:
    - otf_tools/ci/cli.md

validation_contract:
  type: code_agent_execution
  steps:
    - run: "python cli/create_pipeline.py --help"
      expect:
        exit_code: 0

    - run: "python cli/create_pipeline.py --dry-run"
      expect:
        exit_code: 0
        stdout_json: true

retry_policy:
  max_attempts: 3

failure_model:
  categories:
    - param_error
    - contract_violation
    - api_mismatch
    - runtime_error
```

---

## 约束（非常重要）
+  不包含自然语言执行指令 
+  不包含 prompt 
+  不包含步骤解释 
+  不包含执行顺序依赖 
+  纯结构化 contract 

---

## 4.4 validate_contract
### 功能
只验证：

+  workflow schema 正确性 
+  cli.md version 是否 stale 
+  swagger version 是否匹配 
+  CLI contract 是否完整 

---

### 输入
```plain
{
  "system": "ci",
  "workflow": {...}
}
```

---

### 输出
```plain
{
  "valid": true,
  "warnings": [],
  "stale_cli": [],
  "missing_fields": []
}
```

---

# 5. CLI 生成约束（强制规则）
MCP Server 不生成代码，但必须在 workflow 中约束：

---

## CLI Spec v1（必须遵守）
每个 CLI：

### 必须
+  argparse 
+  stdout JSON 
+  stderr logs 
+  exit code: 

| code | meaning |
| --- | --- |
| 0 | success |
| 1 | business error |
| 2 | parameter error |
| 3 | system error |


+  support `--help`
+  support `--dry-run`

---

### 禁止
+  print debug to stdout 
+  hardcoded token 
+  global state 

---

# 6. CLI 生命周期（cli.md）
```plain
system: ci

commands:

  - name: create_pipeline
    cli: cli/create_pipeline.py
    status: stable | draft | stale | deprecated
    generated_from_api_version: "2.0.0"
```

---

## 状态规则
| 状态 | 说明 |
| --- | --- |
| draft | 生成未验证 |
| stable | 验证通过 |
| stale | swagger version mismatch |
| deprecated | 废弃 |


---

# 7. On-the-Fly 初始化逻辑
## 在 MCP server 启动后：
不做任何扫描。

## 在第一次 tool call：
```plain
ensure_otf_tools():
    if not exists("otf_tools"):
        mkdir("otf_tools")
        mkdir("otf_tools/.otf")
```

---

# 8. MCP Server 实现要求（Python）
## 8.1 技术栈
+  Python 3.10+ 
+  stdio MCP protocol 
+  json-based tool I/O 
+  pathlib 
+  pyyaml 

---

## 8.2 server loop（必须实现）
```plain
while True:
    request = read_stdin_json()

    tool = request["tool"]
    args = request["args"]

    result = dispatch(tool, args)

    write_stdout_json(result)
```

---

## 8.3 tool router
```plain
def dispatch(tool, args):
    if tool == "discover_system":
        return discover_system()

    if tool == "get_system_context":
        return get_system_context(args)

    if tool == "construct_workflow":
        return construct_workflow(args)

    if tool == "validate_contract":
        return validate_contract(args)
```

---

# 9. 安全与边界
## MCP Server 不允许：
+  文件写入 
+  subprocess 
+  network call 
+  LLM call 

---

# 10. Code Agent 执行闭环（关键设计）
```plain
1. MCP → construct_workflow
2. Code Agent reads workflow
3. Code Agent writes otl_tools/ci/cli/*.py
4. Code Agent executes CLI
5. Code Agent evaluates result
6. Code Agent updates otl_tools/ci/cli.md
7. loop if needed
```

---

# 11. 系统本质（最终抽象）
## On-the-Fly = Capability Compiler
| 层 | 类比 |
| --- | --- |
| onthefly.md | Source of truth |
| swagger.json | Interface spec |
| workflow (CCW) | IR / compilation plan |
| cli/*.py | Binary artifact |
| Code Agent | Compiler + executor |
| MCP Server | Front-end compiler (planner) |


---

# 12 CCW（Workflow）JSON Schema（机器可校验版本）
目标：让 MCP Server 输出的 Workflow 100% 可 JSON Schema validate，而不是“约定格式”。

---

## CCW Schema（v1）
```plain
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Capability Construction Workflow (CCW)",
  "type": "object",
  "required": [
    "workflow_type",
    "version",
    "system",
    "goal",
    "context",
    "inputs",
    "constraints",
    "artifact",
    "validation_contract",
    "retry_policy",
    "failure_model"
  ],
  "properties": {
    "workflow_type": {
      "type": "string",
      "const": "capability_construction"
    },
    "version": {
      "type": "integer",
      "const": 1
    },
    "system": {
      "type": "string"
    },
    "goal": {
      "type": "object",
      "required": ["artifact", "command"],
      "properties": {
        "artifact": { "type": "string", "enum": ["cli"] },
        "command": { "type": "string" }
      }
    },
    "context": {
      "type": "object",
      "required": ["api_version"],
      "properties": {
        "api_version": { "type": "string" }
      }
    },
    "inputs": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["read"],
        "properties": {
          "read": { "type": "string" }
        }
      }
    },
    "constraints": {
      "type": "array",
      "items": { "type": "string" }
    },
    "artifact": {
      "type": "object",
      "required": ["create"],
      "properties": {
        "create": {
          "type": "array",
          "items": { "type": "string" }
        },
        "update": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    },
    "validation_contract": {
      "type": "object",
      "required": ["type", "steps"],
      "properties": {
        "type": {
          "type": "string",
          "enum": ["code_agent_execution"]
        },
        "steps": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["run", "expect"],
            "properties": {
              "run": { "type": "string" },
              "expect": {
                "type": "object",
                "properties": {
                  "exit_code": { "type": "integer" },
                  "stdout_json": { "type": "boolean" }
                }
              }
            }
          }
        }
      }
    },
    "retry_policy": {
      "type": "object",
      "required": ["max_attempts"],
      "properties": {
        "max_attempts": { "type": "integer" }
      }
    },
    "failure_model": {
      "type": "object",
      "required": ["categories"],
      "properties": {
        "categories": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    }
  }
}
```

# 13 Code Agent 执行 Loop Protocol（核心运行协议）
目标：让 Code Agent（Codex、Claude Code 等）“按统一循环执行 MCP Workflow”，否则整个系统无法收敛。

---

## 标准执行循环（必须实现）
```plain
1. Call MCP: construct_workflow(system, command)

2. Receive CCW

3. Validate CCW schema (local)

4. FOR each artifact.create:
      write_file(path)

5. FOR each validation step:
      run subprocess

6. IF all success:
      mark cli.md = stable

7. ELSE:
      analyze failure

      IF retry < max_attempts:
          modify cli code
          re-run validation
      ELSE:
          mark cli.md = draft
          stop + report
```

---

## Agent 决策规则（强约束）
### 禁止
+  不允许跳过 validation 
+  不允许直接 assume CLI correctness 
+  不允许修改 workflow 

### 必须
+  workflow 是唯一 truth 
+  CLI 只能从 workflow 推导 
+  所有失败必须基于 exit code + stdout 

---

##  CLI 执行标准（Agent side）
```plain
python otl_tools/ci/cli/<command>.py --help
python otl_tools/ci/cli/<command>.py --dry-run
```

---

## 成功判定
```plain
exit_code == 0
AND
stdout valid JSON (if required)
```

---

## 失败分类（必须归因）
| 类型 | 含义 |
| --- | --- |
| param_error | argparse / 参数错误 |
| contract_violation | stdout/exit code 不符合 CLI spec |
| api_mismatch | swagger / business rule 不一致 |
| runtime_error | Python exception |


# 14 CLI Template Generator
目标：避免 Codex 每次生成 CLI 风格不一致

所有 CLI 必须基于以下模板生成：

## 文件结构
```plain
cli/
  create_xxx.py
  query_xxx.py
```

---

## 🧠 标准 CLI 模板
```plain
import argparse
import json
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="CLI command")
    
    parser.add_argument("--dry-run", action="store_true")

    # TODO: add command-specific args

    return parser.parse_args()


def main():
    args = parse_args()

    try:

        if args.dry_run:
            print(json.dumps({
                "dry_run": True,
                "message": "preview only"
            }))
            return 0

        # =========================
        # BUSINESS LOGIC HERE
        # =========================

        result = {
            "success": True
        }

        print(json.dumps(result))
        return 0

    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    except Exception as e:
        print(str(e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
```

---

## 强制规范（Codex 必须遵守）
### stdout
+  必须 JSON 
+  不能有 print debug 

### stderr
+  所有 log / error 

### exit code
| code | meaning |
| --- | --- |
| 0 | success |
| 1 | business error |
| 2 | param error |
| 3 | system error |


---

## auth 规则
```plain
env var only
NO CLI argument tokens allowed
```

---

## dry-run 标准
```plain
{
  "dry_run": true,
  "preview": "request payload"
}
```


