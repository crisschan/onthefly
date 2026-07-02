## 🎯 目标
实现一个 **Python stdio MCP Server**，作为：

Capability Construction Orchestrator（能力构建编排器）

它不执行业务、不生成业务代码、只负责：

1. 生成 **Capability Construction Workflow（CCW）**，为 Code Agent 提供构建契约；
2. 维护自身的状态账本（`cli.md`），保证 `discover_system` / `get_system_context` 对外报告的状态权威可信。

> **v2 变更说明**：v1 中"MCP Server 绝对不允许文件写入"的表述已废弃。v2 明确区分"状态账本写入"（Server 职责）与"生成产物写入"（Code Agent 职责），详见第 9 节。
>

---

# 0. 架构分期：构建期 vs 运行期
这是理解整个系统最重要的前提，v1 文档中缺失，v2 补齐。

| 阶段 | 参与方 | 说明 |
| --- | --- | --- |
| **构建期** | Code Agent + On-The-Fly MCP Server | Agent 调用 `construct_workflow` 获取 CCW，生成/复用 CLI，验证后调用 `register_cli` 登记状态。**On-The-Fly 只在这个阶段被调用。** |
| **运行期（近期）** | Skill + 已生成 CLI | Skill 层通过 subprocess 直接调用已落盘且状态为 `stable` 的 CLI 脚本，完全不经过 MCP Server。 |
| **运行期（远期，可选，架构预留）** | Skill + MCP（CLI-as-tool） | 已生成的 CLI 未来可能被一层 adapter 包装为 MCP tools，Skill 通过标准 MCP 协议调用。这条路径之所以"免费"可行，是因为 CLI Spec v1（第 5 节）强制要求的 argparse 参数边界 + stdout JSON 契约，天然可以无损映射为 MCP tool schema。**本 spec 不实现这层 adapter，仅作架构预留说明。** |


On-The-Fly 的定位始终是"构建期编译器（Front-end Compiler / Planner）"，不参与运行期的业务调度。跨 system 的业务编排（例如"创建 pipeline 后发通知"）属于 Skill 层职责，不属于 CLI 或 MCP Server 职责（详见第 3.1 节）。

---

# 1. 总体架构
## 1.1 系统结构
```plain
Codex / Claude Code (构建期 Client)
        │
        │ MCP stdio
        ▼
+--------------------------------------------------+
| On-the-Fly MCP Server (Python)                   |
|                                                  |
|  Capability Construction Orchestrator            |
|                                                  |
|  Tools:                                          |
|    - discover_system                             |
|    - get_system_context                          |
|    - construct_workflow                         |
|    - validate_workflow      (原 validate_contract) |
|    - register_cli           (新增)                |
|    - resolve_escalation     (新增)                |
|                                                  |
|  ✅ 允许写入自身状态账本 (cli.md)                    |
|  ❌ 不写入业务代码 / 生成产物                        |
|  ❌ no subprocess                                |
|  ❌ no network call                              |
|  ❌ no LLM calls                                 |
+---------------------┬----------------------------+
                      │
                      ▼
           Project Working Directory (cwd)
                └── otf_tools/
```

---

# 2. 工作目录模型（强约束）
## 2.1 MCP Server 作用范围是 workspace
MCP Server 的操作目录永远在 workspace 的目录下，不在 MCP Server 的存储目录下。

## 2.2 自动初始化规则
首次任何 MCP tool 调用时，在 workspace 中运行如下逻辑：

```plain
if not exists("./otf_tools"):
    create_dir("./otf_tools")
    create_dir("./otf_tools/.otf")
```

初始化过程本身需要做**互斥保护**（见第 9.2 节并发处理），避免多进程同时触发初始化导致目录状态不一致。

---

## 2.3 目录结构
otf_tools 在 workspace 目录下，不在 On-The-Fly MCP Server 的目录下。

```plain
otf_tools/
├── .otf/                      # runtime hidden state
│   └── runtime.yaml
│
└── ci/ (example system)
    ├── onthefly.md
    ├── swagger.json
    ├── cli.md
    └── cli/*
```

> **v2 变更**：移除 `registry_cache.json`（v1 中声明但从未被任何工具读写，属死文件）；移除 `swagger_hashes/`（stale 判定改为基于 `api_version` 版本号比较，见第 6.2 节，不再需要 hash 存储）。
>

---

# 3. System Model（单系统隔离）
每个 system：

+ 独立 `onthefly.md`
+ 独立 `swagger.json`
+ 独立 `cli.md`
+ 独立 `cli/`

❗**禁止跨 system workflow**

## 3.1 为什么坚持禁止跨 system（重要，避免误解为遗漏）
真实场景中，一个 command 大概率需要依赖其他 system 的能力（例如 CI 的 `create_pipeline` 成功后需要调用 Notification 发送通知）。v2 明确评估过以下两种"开放跨 system 依赖"的方案，并**主动放弃**：

+ 允许 `onthefly.md` 声明 `depends_on` 并在 CLI 中直接调用其他 system 的 API；
+ 允许 CLI 对其他 system 状态做只读感知。

放弃原因：本系统已经确立 **CLI = 无状态原子执行层，Skill = 编排/判断层** 的分工。跨 system 的组合行为（"创建成功后再发通知"）本质上是**业务编排**，天然属于 Skill 层职责——由 Skill 分别调用 `ci.create_pipeline` 与 `notification.send` 两个独立、纯净的 CLI 完成组合。

如果放开 CLI 层的跨 system 调用，CLI 会退化成一个隐式的跨系统胶水层，`construct_workflow` 生成的 workflow 也会失去"单一 system 契约"这个可验证边界。因此本条为**架构决策**，不是待办缺口，后续不应在 CLI/MCP 层引入跨 system 依赖。

---

# 4. onthefly.md Schema（v2 新增）
`onthefly.md` 为纯 YAML 结构，`discover_system` 从中解析 `name` / `tags` / `api_version`：

```yaml
system: ci
name: CI Pipeline
tags:
  - 构建
  - 部署
api_version: "2.0.0"
description: |
  该系统负责流水线的创建、查询与状态管理...
```

> **注意（人工维护一致性）**：`onthefly.md.api_version` 与 `swagger.json` 顶层 `version` 是两个**独立字段**，语义上应保持一致，但系统**不做自动同步或校验**，一致性由人工维护。这是已知的人工同步点，不属于系统缺陷。
>

---

# 5. MCP Tools 规范
## 5.1 discover_system
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

失败时返回统一错误结构（见第 10 节）。

---

## 5.2 get_system_context
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
    "stale_count": 0,
    "needs_human_review_count": 0
  }
}
```

`stale_count` 的判定逻辑见第 6.2 节，每次调用本工具时**实时计算**，不使用缓存。

---

## 5.3 construct_workflow （核心）
### 功能变更说明（v2）
v1 中本工具只有"从零构建"一种行为。v2 明确加入**复用判断**：调用时 Server 内部先查询 `cli.md` 中该 command 的当前状态，据此决定返回"复用 workflow"还是"构建 workflow"，均以统一的 CCW 结构返回，Agent 执行循环无需分支处理（见第 5.3.3 节）。

### 输入
```plain
{
  "system": "ci",
  "command": "create_pipeline"
}
```

### 5.3.1 前置判断逻辑（Server 内部）
```plain
1. 若该 command 在 cli.md 中标记 needs_human_review == true：
     → 拒绝请求，返回 error: blocked_pending_human_review

2. 若该 command 在 cli.md 中状态为 stable
   且 generated_from_api_version == swagger.json 当前 version：
     → 返回 mode: reuse 的 CCW

3. 否则（不存在 / draft / stale / api_version 不匹配）：
     → 返回 mode: build 的 CCW
```

### 5.3.2 输出（CCW Workflow，build 模式示例）
```plain
workflow_type: capability_construction
version: 1

system: ci

goal:
  artifact: cli
  command: create_pipeline
  mode: build              # build | reuse

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
    - contract_violation
    - api_mismatch
    - runtime_error
```

### 5.3.3 输出（CCW Workflow，reuse 模式示例）
复用模式沿用完全相同的 CCW 结构，仅以下字段不同：

```plain
goal:
  artifact: cli
  command: create_pipeline
  mode: reuse

artifact:
  create: []               # 复用模式固定为空，Agent 据此跳过 write_file 步骤
  update: []

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
```

reuse 模式的 `validation_contract` 用于确认已有 CLI 未发生环境漂移；若验证失败，Agent 应调用 `register_cli` 上报 `outcome: failure`，下一次调用 `construct_workflow` 时该 command 状态已不再是 stable，将自动转入 build 模式，实现自我纠错。

---

## 约束（非常重要，v1 沿用）
+ 不包含自然语言执行指令
+ 不包含 prompt
+ 不包含步骤解释
+ 不包含执行顺序依赖
+ 纯结构化 contract

---

## 5.4 validate_workflow（原 v1 中的 validate_contract，v2 更名）
> **更名原因**：v1 中工具名 `validate_contract` 与 CCW 字段 `validation_contract` 名称高度相似但语义完全不同（前者验证 workflow 本身是否合法，后者是 CLI 产物的运行时验证步骤），容易混淆。v2 将工具更名为 `validate_workflow`，字段名 `validation_contract` 保持不变。
>

### 功能
只验证：

+ workflow schema 正确性
+ cli.md version 是否 stale
+ swagger version 是否匹配
+ CLI contract 是否完整

### 输入
```plain
{
  "system": "ci",
  "workflow": {...}
}
```

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

## 5.5 register_cli（v2 新增）
### 功能
Code Agent 完成 `validation_contract` 中的验证步骤后，调用本工具登记结果。**Agent 只上报客观事实，最终状态由 Server 推导**，防止 Agent 自行判定或误标 `stable`。

### 输入
```plain
{
  "system": "ci",
  "command": "create_pipeline",
  "outcome": "success" | "failure",
  "attempts": 2,
  "failure_category": "contract_violation" | "api_mismatch" | "runtime_error" | null,
  "api_version": "2.0.0"
}
```

### 状态推导规则（Server 内部）
```plain
if outcome == "success":
    status = "stable"
    needs_human_review = false

elif outcome == "failure" and attempts < max_attempts:
    status = "draft"
    needs_human_review = false
    → 允许 Agent 下次调用 construct_workflow 重新进入 build 模式

elif outcome == "failure" and attempts >= max_attempts:
    status = "draft"
    needs_human_review = true
    → construct_workflow 对该 command 后续请求返回 blocked_pending_human_review
```

### 输出
```plain
{
  "system": "ci",
  "command": "create_pipeline",
  "status": "stable",
  "needs_human_review": false
}
```

写入 `cli.md` 时需做互斥保护（见第 9.2 节）。

---

## 5.6 resolve_escalation（v2 新增）
### 功能
用于解除 `needs_human_review` 锁定状态。**v2 设计为轻量确认模式**：不要求重新跑机器验证，人工确认即视为可信，直接恢复为 `stable`。

> 这是一个有意的信任让渡：一旦调用本工具，系统默认这次人工介入可靠，不做事后校验。若未来发现人工判断也存在误差，可在后续版本中加入复验环节，v1/v2 阶段按轻量模式实现。
>

### 输入
```plain
{
  "system": "ci",
  "command": "create_pipeline",
  "resolution_note": "人工修复了 xxx 问题，已本地验证通过"
}
```

### 处理逻辑
```plain
status = "stable"
needs_human_review = false
→ 记录 resolution_note 与时间戳到 cli.md 的历史字段，
  标注该次 stable 状态来源为「人工强制标记」而非「机器验证」，
  用于后续审计溯源。
```

### 输出
```plain
{
  "system": "ci",
  "command": "create_pipeline",
  "status": "stable",
  "resolved_by": "human",
  "resolved_at": "2026-07-01T12:00:00Z"
}
```

---

# 6. CLI 生成约束（强制规则）
MCP Server 不生成业务代码，但必须在 workflow 中约束：

## 6.1 CLI Spec v1（必须遵守）
每个 CLI：

### 必须
+ argparse
+ stdout JSON
+ stderr logs
+ exit code（**v2 简化，见下）
+ support `--help`
+ support `--dry-run`

### exit code 规则（v2 变更）
> **v1→v2 变更说明**：v1 定义了 4 种 exit code（0/1/2/3，1 代表"业务错误"），但标准模板从未实现 `return 1` 的路径，模板与规范矛盾。v2 明确 **exit code 只反映"进程是否正常执行完"，不反映业务成功与否**。业务成功/失败通过 stdout JSON 中的 `success` 字段表达，与 exit code 分开判断，这也更贴近通用 CLI 惯例。
>

| code | meaning |
| --- | --- |
| 0 | 进程正常执行完毕（不代表业务一定成功，需看 stdout 的 `success` 字段） |
| 2 | 参数错误（argparse / 校验失败） |
| 3 | 系统 / 运行时错误（未捕获异常） |


### 禁止
+ print debug 到 stdout
+ hardcoded token
+ global state

---

## 6.2 stale 判定算法（v2 新增，v1 缺失）
+ **判定依据**：比较 `cli.md` 中该 command 记录的 `generated_from_api_version`，与该 system 当前 `swagger.json` 顶层 `version` 字段是否一致（字符串精确匹配）。不一致即判定为 `stale`。
+ **判定粒度**：v2 采用**全量粒度**——只要 swagger 顶层 version 变化，该 system 下所有 CLI 均标记 stale，不做"只对比该 command 依赖的 API 片段"的精细化判定（后者需要额外维护 CLI ↔ API 片段的依赖映射，v2 暂不引入，作为已知的未来优化方向）。
+ **触发时机**：不做缓存，`discover_system` / `get_system_context` / `construct_workflow` 每次调用时**实时计算**。

---

# 7. CLI 生命周期（cli.md）
```plain
system: ci

commands:

  - name: create_pipeline
    cli: cli/create_pipeline.py
    status: stable | draft | stale | deprecated
    generated_from_api_version: "2.0.0"
    needs_human_review: false
    history:
      - timestamp: "2026-06-28T10:00:00Z"
        outcome: failure
        attempts: 3
        failure_category: contract_violation
        source: machine
      - timestamp: "2026-06-28T15:00:00Z"
        outcome: success
        resolution_note: "人工修复了 xxx 问题"
        source: human            # human | machine，标注该状态变更的可信来源
```

## 状态规则
| 状态 | 说明 |
| --- | --- |
| draft | 生成未验证，或验证失败但未达重试上限 |
| stable | 验证通过（机器验证或人工确认，见 history.source） |
| stale | swagger version mismatch（实时判定，非持久状态） |
| deprecated | 废弃 |


`needs_human_review` 与 `status` 是两个独立字段：`status=draft` 时可能仍在自动重试范围内，只有 `needs_human_review=true` 时才会阻断 `construct_workflow` 的自动构建。

---

# 8. On-the-Fly 初始化逻辑
## 在 MCP server 启动后
不做任何扫描。

## 在第一次 tool call
```plain
ensure_otf_tools():
  with file_lock(".otf/.init.lock"):
    if not exists("./otf_tools"):
      create_dir("./otf_tools")
      create_dir("./otf_tools/.otf")
```

---

# 9. MCP Server 实现要求（Python）
## 9.1 技术栈
+ Python 3.10+
+ stdio MCP protocol
+ json-based tool I/O
+ pathlib
+ pyyaml

## 9.2 写入权限边界（v2 重新定义，替代 v1 的"绝对不写文件"）
| 内容 | 写入权限 |
| --- | --- |
| `otf_tools/{system}/cli.md`（状态账本） | ✅ **只能由 MCP Server 写入**（通过 `register_cli` / `resolve_escalation`），Code Agent 不应直接编辑此文件 |
| `otf_tools/.otf/`（运行时状态、初始化锁） | ✅ MCP Server 写入 |
| `otf_tools/{system}/cli/*.py`（CLI 业务代码） | ❌ MCP Server 不写，由 **Code Agent** 生成写入 |
| `otf_tools/{system}/onthefly.md` / `swagger.json` | ❌ MCP Server 不写，人工或外部工具维护 |


Server 依然遵守：不执行 subprocess、不发起 network call、不调用 LLM。

## 9.3 并发写入保护
`register_cli` / `resolve_escalation` 写入 `cli.md` 前必须加文件锁（例如 `fcntl.flock` 或"临时文件 + 原子 rename"方案），避免多个 Agent/进程并发写导致脏写。v2 阶段采用**单机文件锁**即可，不引入分布式锁。`ensure_otf_tools()` 初始化过程同样需要加锁（见第 8 节）。

## 9.4 server loop（必须实现）
```plain
while True:
    request = read_stdin_json()

    tool = request["tool"]
    args = request["args"]

    result = dispatch(tool, args)

    write_stdout_json(result)
```

## 9.5 tool router
```plain
def dispatch(tool, args):
    if tool == "discover_system":
        return discover_system()

    if tool == "get_system_context":
        return get_system_context(args)

    if tool == "construct_workflow":
        return construct_workflow(args)

    if tool == "validate_workflow":
        return validate_workflow(args)

    if tool == "register_cli":
        return register_cli(args)

    if tool == "resolve_escalation":
        return resolve_escalation(args)
```

## 9.6 MCP Host 的配置
MCP 的配置中使用 command 使用 uv 命令，本 Server 不接受任何 cwd 配置，始终依赖 MCP Host 注入的当前 workspace（`os.getcwd()`）作为工作目录，因此 otf_tools 目录应该在 workspace 的子目录下。

---

# 10. 统一错误返回结构（v2 新增）
所有 6 个工具在出错时统一返回以下结构，不再各自发明格式：

```json
{
  "error": {
    "code": "system_not_found",
    "message": "human readable message"
  }
}
```

### 已定义的 error code
| code | 触发场景 |
| --- | --- |
| `system_not_found` | 请求的 system 在 `otf_tools/` 下不存在 |
| `command_not_found` | `cli.md` 中不存在该 command 记录（`register_cli` 场景除外，首次登记时允许新建） |
| `invalid_args` | 参数缺失或类型错误 |
| `corrupted_state` | `cli.md` / `onthefly.md` 解析失败（格式损坏） |
| `blocked_pending_human_review` | 该 command 处于 `needs_human_review=true`，拒绝自动构建 |
| `lock_timeout` | 并发写入时获取文件锁超时 |


---

# 11. 安全与边界
## MCP Server 不允许
+ subprocess
+ network call
+ LLM call
+ 写入业务代码 / 生成产物（详见第 9.2 节写入权限边界表）

---

# 12. Code Agent 执行闭环（关键设计，v2 更新）
```plain
1. MCP → construct_workflow(system, command)

2. Receive CCW（mode: build 或 reuse）

3. Validate CCW schema (local，或调用 validate_workflow)

4. IF mode == build:
     FOR each artifact.create:
        write_file(path)
   ELSE (mode == reuse):
     跳过 write_file，直接进入验证步骤

5. FOR each validation_contract.steps:
      run subprocess
      记录 exit_code / stdout / attempts

6. 判定 outcome:
      success:  exit_code == 0（且如需校验业务结果，stdout.success == true）
      failure:  否则

7. 调用 MCP → register_cli(system, command, outcome, attempts, failure_category, api_version)
   → Server 推导并写回 cli.md 状态

8. IF outcome == failure AND attempts < max_attempts:
      修改 cli 代码，重新执行步骤 5-7（mode 仍为 build）
   ELSE IF outcome == failure AND attempts >= max_attempts:
      状态已被 register_cli 标记为 needs_human_review=true
      停止自动重试，报告等待人工介入
      （人工处理完毕后由人工触发 resolve_escalation，不由 Agent 自动调用）
```

---

## Agent 决策规则（强约束，v1 沿用）
### 禁止
+ 不允许跳过 validation
+ 不允许直接 assume CLI correctness
+ 不允许修改 workflow
+ **不允许直接编辑 **`cli.md`，状态变更必须通过 `register_cli` / `resolve_escalation`（v2 新增约束）

### 必须
+ workflow 是唯一 truth
+ CLI 只能从 workflow 推导
+ 所有失败必须基于 exit code + stdout 归因

---

## CLI 执行标准（Agent side）
```plain
python otf_tools/ci/cli/<command>.py --help
python otf_tools/ci/cli/<command>.py --dry-run
```

## 成功判定（v2 更新）
```plain
exit_code == 0
AND
stdout valid JSON (if required)
AND
（如 validation_contract 要求业务结果校验）stdout.success == true
```

## 失败分类（v2 简化，移除 param_error，因参数错误已由 exit_code=2 直接体现，不再作为独立的失败归因分类）
| 类型 | 含义 |
| --- | --- |
| contract_violation | stdout/exit code 不符合 CLI spec |
| api_mismatch | swagger / business rule 不一致 |
| runtime_error | Python exception |


---

# 13. CLI 生成模板
## 文件结构
```plain
cli/
  create_xxx.py
  query_xxx.py
```

## 🧠 标准 CLI 模板（v2，exit code 简化为 0/2/3）
```python
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
        # 业务成功/失败通过 result["success"] 表达，
        # 不使用 exit code 区分业务结果
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

## 强制规范
### stdout
+ 必须 JSON
+ 不能有 print debug
+ 必须包含 `success: true/false` 字段表达业务结果

### stderr
+ 所有 log / error

### exit code
| code | meaning |
| --- | --- |
| 0 | 进程正常执行完毕 |
| 2 | 参数错误 |
| 3 | 系统 / 运行时错误 |


---

## auth 规则（v2 新增命名规范）
```plain
env var only
NO CLI argument tokens allowed
```

**命名规范**：`OTF_{SYSTEM}_{TOKEN_NAME}`，system 名全大写、下划线分隔。

示例：CI 系统的 API token 应命名为 `OTF_CI_API_TOKEN`，Notification 系统则为 `OTF_NOTIFICATION_API_TOKEN`。禁止使用无命名空间前缀的通用变量名（如裸 `TOKEN` / `API_KEY`），避免多 system 间环境变量冲突。

## dry-run 标准
```plain
{
  "dry_run": true,
  "preview": "request payload"
}
```

---

# 14. 系统本质（最终抽象，v2 补充运行期演化路径）
## On-the-Fly = Capability Compiler
| 层 | 类比 | 备注 |
| --- | --- | --- |
| onthefly.md | Source of truth |  |
| swagger.json | Interface spec |  |
| workflow (CCW) | IR / compilation plan |  |
| cli/*.py | Binary artifact |  |
| Code Agent | Compiler + executor（构建期） |  |
| MCP Server (On-The-Fly) | Front-end compiler (planner)，仅构建期参与 |  |
| Skill（运行期，近期） | Runtime scheduler，直接 subprocess 调用已生成 CLI |  |
| Skill + MCP（运行期，远期，架构预留，暂不实现） | Runtime scheduler，通过 adapter 将 CLI 包装为 MCP tool 后调用 | 之所以可行，是因为 CLI Spec v1（第 6.1 节）的 argparse + stdout JSON 契约天然可无损映射为 MCP tool schema |


---

# 15. CCW（Workflow）JSON Schema（机器可校验版本，v2 更新）
## CCW Schema（v2）
```json
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
      "required": ["artifact", "command", "mode"],
      "properties": {
        "artifact": { "type": "string", "enum": ["cli"] },
        "command": { "type": "string" },
        "mode": { "type": "string", "enum": ["build", "reuse"] }
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
                  "stdout_json": { "type": "boolean" },
                  "stdout_success": { "type": "boolean" }
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
          "items": {
            "type": "string",
            "enum": ["contract_violation", "api_mismatch", "runtime_error"]
          }
        }
      }
    }
  }
}
```

> `goal.artifact` 枚举当前仅保留 `"cli"`，为有意的 v1/v2 范围限定（YAGNI），未来若扩展其他产物类型将作为 breaking change 处理，不预留扩展位。
>

---

# 16. 已知限制与未来演化方向（v2 新增，供后续版本参考）
+ stale 判定目前是 system 级全量粒度（第 6.2 节），未来若误伤率过高，可升级为基于 API 片段的精细化判定。
+ `resolve_escalation` 当前为轻量确认模式，不做事后复验；若实践中发现人工判断也存在误差，可加入复验环节。
+ 并发控制为单机文件锁，若未来 On-The-Fly 需要支持多实例/分布式部署，需升级为分布式锁方案。
+ "Skill + MCP（CLI-as-tool）"运行期路径（第 0 节 / 第 14 节）仅作架构预留，具体的 adapter 设计需另立 spec。

# 17. 开发阶段划分与验收标准（给 Codex 的实施指引）


本节为工程实施顺序建议，非 spec 的规范性内容。目的是让每个 phase 结束时都能独立验证一个可工作的切片，避免等全部完成才第一次做端到端联调。依赖顺序特别注意：construct_workflow 的 reuse 判断依赖 cli.md 状态，而该状态只能由 register_cli 写入，因此 register_cli/resolve_escalation（Phase 4）必须先于 construct_workflow 的 reuse 逻辑（Phase 5）完成。

Phase 1 — Skeleton

范围：


MCP stdio server 主循环（第 9.4 节）
tool router，6 个工具全部注册（discover_system / get_system_context / construct_workflow / validate_workflow / register_cli / resolve_escalation），内部先返回 {"error": {"code": "not_implemented", "message": "..."}}
统一错误结构（第 10 节）作为公共模块实现，后续 phase 直接复用，不要各自发明格式


验收标准：


通过 stdio 发送 6 个工具各自的最小合法请求，均能收到结构正确的 JSON 响应（哪怕内容是 not_implemented）
发送一个不存在的 tool 名，返回统一错误结构而不是崩溃



Phase 2 — 初始化 + 只读发现

范围：


ensure_otf_tools() 自动初始化逻辑，含文件锁（第 8 节）
onthefly.md YAML schema 解析（第 4 节）
discover_system 实现


验收标准：


空 workspace 下首次调用任意工具，otf_tools/.otf/ 被正确创建
并发发起两个初始化请求（可用两个进程模拟），不产生目录创建异常或重复初始化错误
在 otf_tools/ci/onthefly.md 存在时，discover_system 返回正确的 system/name/tags/api_version
onthefly.md 格式损坏时，返回 corrupted_state 错误而不是崩溃
无任何 system 时，discover_system 返回空列表而不是报错



Phase 3 — 上下文查询 + stale 判定

范围：


cli.md YAML schema 解析（第 7 节）
get_system_context 实现
stale 判定逻辑（第 6.2 节：比较 generated_from_api_version 与 swagger.json.version，实时计算，不缓存），封装为独立函数供 Phase 5 复用


验收标准：


get_system_context 返回完整的 onthefly/cli_registry/swagger/status 四块内容
手动修改 swagger.json 的 version 后，无需重启进程，下次调用 get_system_context 时 stale_count 立即反映变化（验证"实时计算，不缓存"）
system 不存在时返回 system_not_found
cli.md 不存在时（该 system 下还没有任何 CLI），status.cli_count = 0，不报错



Phase 4 — 状态写入闭环

范围：


register_cli：状态推导规则（成功→stable；失败未达上限→draft；失败达上限→draft + needs_human_review，第 5.5 节）
resolve_escalation：轻量确认解锁（第 5.6 节）
cli.md 并发写入保护（文件锁，第 9.3 节）
history 字段的追加写入，含 source: human | machine 标注


验收标准：


调用 register_cli(outcome=success)，cli.md 中对应 command 状态变为 stable，needs_human_review=false
连续调用 register_cli(outcome=failure) 达到 max_attempts 后，状态为 draft 且 needs_human_review=true
并发发起多个 register_cli 写同一个 command（模拟竞态），最终 cli.md 内容完整无损坏、无交叉写入
调用 resolve_escalation，状态变为 stable，history 中新增一条 source: human 的记录，且带 resolution_note
对不存在的 system/command 调用 register_cli，行为符合预期（新建记录，而不是报错——注意这是 command_not_found 的例外场景，第 10 节已注明）



Phase 5 — construct_workflow

范围：


先实现 build 模式：CCW 生成，含 goal.mode=build、完整的 artifact.create（第 5.3.2 节）
再接入 reuse 判断逻辑（第 5.3.1 节前置判断），依赖 Phase 3 的 stale 判定 + Phase 4 的状态读取
needs_human_review=true 时拒绝请求，返回 blocked_pending_human_review


验收标准（分两步）：


build 模式独立验证：对一个从未构建过的 command 调用 construct_workflow，返回的 CCW 通过 CCW JSON Schema（第 15 节）校验，goal.mode=build，artifact.create 非空
端到端 reuse 验证：

Step A：对 command X 调用 construct_workflow → 得到 build 模式 CCW
Step B：调用 register_cli(command=X, outcome=success, api_version=当前版本)
Step C：再次调用 construct_workflow(command=X) → 应返回 mode=reuse，artifact.create=[]
Step D：手动修改 swagger.json version → 再次调用 construct_workflow(command=X) → 应因 stale 重新返回 mode=build
Step E：将 command X 标记为 needs_human_review=true（可直接走 Phase 4 的失败达上限路径）→ 调用 construct_workflow(command=X) → 应返回 blocked_pending_human_review






Phase 6 — validate_workflow

范围：


workflow schema 校验（对照第 15 节 CCW Schema）
cli.md staleness 检查
swagger version 匹配检查
CLI contract 完整性检查（artifact/validation_contract 等必填字段是否齐全）


验收标准：


传入 Phase 5 生成的合法 CCW，返回 valid: true
人为删除 CCW 中的必填字段（如 retry_policy），返回 valid: false 且 missing_fields 中列出该字段
传入一个 command 对应 stale 状态的 CCW，返回结果中 stale_cli 非空



Phase 7 — 加固与集成

范围：


CLI 模板生成器产出的代码需符合 CLI Spec v1（第 13 节）：argparse、stdout JSON 含 success 字段、exit code 只用 0/2/3、auth 走 OTF_{SYSTEM}_{TOKEN_NAME} 命名规范、支持 --help/--dry-run
完整闭环集成测试（走一遍第 12 节 Agent 执行循环全过程）
并发/边界场景补测：文件锁超时（lock_timeout）、cli.md 损坏时的降级行为、system 不存在的各工具一致性表现


验收标准：


端到端跑通：construct_workflow(build) → 生成 CLI → 执行验证步骤失败 → register_cli(failure, attempts<max) → 重新构建 → 再次失败直到达上限 → register_cli 返回 needs_human_review=true → construct_workflow 返回 blocked_pending_human_review → resolve_escalation → 状态恢复 stable → construct_workflow 返回 mode=reuse
生成的 CLI 脚本本身可独立运行 --help/--dry-run，输出符合 CLI Spec v1
所有 6 个工具在各类异常输入下都返回统一错误结构，没有未捕获异常导致进程崩溃

