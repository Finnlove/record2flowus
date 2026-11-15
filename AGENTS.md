# AGENTS.md — record2flowus（Hex → FlowUs 语音笔记工作流）

> 面向 **AI Agent（在获得用户明确授权后）** 的仓库操作规范。目标：帮助 Agent 理解项目、检查环境、指导/协助安装配置、启动服务、诊断问题，并协助完成**首次语音归档**。
>
> **本文件是操作规范，不代表所有步骤已自动化。** 以当前仓库代码与文档为准；与实现不一致时，以 `bridge.py`、`scripts/`、`extension/` 及现有 `.cmd` 脚本的实际行为为准。
>
> 人类用户入口与详细步骤请优先参考：[`README.md`](README.md)、[`docs/quickstart-windows.md`](docs/quickstart-windows.md)、[`docs/troubleshooting.md`](docs/troubleshooting.md)、[`docs/milestone-2-productization.md`](docs/milestone-2-productization.md)。

---

## 1. 项目说明（识别与边界）

- **用途**：在 Windows 本地将 **Hex** 会议录音（音频 + 本地 ASR 转写/manifest）整理为结构化 **FlowUs（息流）** 语音笔记页面。
- **架构/组件关系**（本仓库实现）：
  - `extension/`（MV3，Chrome/Edge）：在 `https://flowus.cn/*` 页面注入悬浮按钮，读取当前页面 ID/URL/标题，通过后台向本地桥接发起 `/start` `/stop` `/state` `/reset` `/ping`（需 token + 端口配置，受 Origin/发送方限制）。
  - `bridge.py`（本地 HTTP 桥接，**仅监听 `127.0.0.1:<bridge_port>`**，默认 `27123`）：校验 Host/Origin/Authorization/请求体与目标页面，调用 Hex CLI（`hex meetings record/stop/list`）录音与等待转写完成，读取会议产物，生成摘要/要点/待办/思维导图载荷（**启发式基线，非 LLM 语义理解**），调用同步脚本上传并追加到 FlowUs。
  - `scripts/sync_to_flowus.py`：读取 Hex 会议目录产物，上传音频并向 FlowUs 页面追加美化块（可选思维导图）；支持追加到已有页或创建新页/数据库记录（CLI 参数见脚本 `--help` 与 README）。
  - `scripts/setup.py` / `scripts/doctor.py`：本地配置引导与诊断（Windows 包装：`setup.cmd`、`doctor.cmd`）。
  - `start.cmd`：先执行 `python bridge.py --check` 预检，通过后启动 `bridge.py`；`start-bridge.cmd` 调用 `start.cmd`（失败时暂停）。
- **运行平台**：**Windows**（Hex 路径/会议目录、`.cmd`、扩展加载流程均按 Windows 设计）。
- **当前已实现能力（以代码/文档为准）**：本地桥接与鉴权/校验、扩展按钮触发录音-停止-归档到当前 FlowUs 页、CLI 同步、配置/诊断脚本、连接测试（扩展选项页）、文本处理回归测试（`tests/`，`python -m unittest discover -s tests -p "test_*.py" -v`）。
- **已知限制（节选，详见 README“Limitations”与故障文档）**：摘要/要点/待办与思维导图为启发式；**无按 meeting ID 幂等/去重**（重试/重复操作可能重复追加）；中断恢复与重试工具有限（M3 规划）；依赖本地 Hex/FlowUs CLI 可用性；扩展无法自动启动桥接进程；部分步骤需用户在浏览器/Hex/FlowUs 中手动完成。

---

## 2. Agent 工作原则

- **先理解仓库，再执行操作**：先读 `README.md`、`docs/quickstart-windows.md`、`docs/troubleshooting.md`、`docs/milestone-2-productization.md`，再检查 `setup.cmd`/`doctor.cmd`/`start.cmd`、`bridge.py`、`scripts/`、`extension/` 的实际行为与字段。
- **优先使用项目现有脚本/入口**：配置用 `setup.cmd`/`python scripts/setup.py`，诊断用 `doctor.cmd`/`python scripts/doctor.py`，预检/启动用 `start.cmd` 或 `python bridge.py --check` / `python bridge.py`；不要臆造不存在的命令或参数。
- **不重复安装已满足的依赖**：先用 `doctor.cmd`、`python --version`、`flowus --json whoami` 等验证现状，再建议安装。
- **操作前检查现有配置**：优先查看 `config.local.json`（及可能存在的 `config.json`）的**结构与字段是否就绪**；避免盲目覆盖。注意：`config.local.json`/`config.json` 已在 `.gitignore` 中，且可能包含密钥——**读取/展示时必须脱敏**；不得将真实 token/凭据暴露在对话输出、日志、补丁或提交中。`scripts/setup.py` 在生成/更新 `bridge_token` 时会在**本地终端向用户**显示新 token 以便复制到扩展选项（合理的本地安装行为）；Agent **不得**将包含 token 的终端输出、配置文件内容或截图文本转发/复述到对话、日志、补丁或提交中，应仅提示用户“已在本地终端生成/显示，请自行复制并妥善保存”。
- **保留用户已有配置和数据**：不删除/覆盖用户录音、转写、导出、`config.local.json`、FlowUs 页面内容，除非用户明确授权且已说明风险。
- **每个阶段执行后都进行验证**：区分“命令返回 0/无报错”与“对应阶段真实成功”（见第 6 节验收分层）。
- **发生错误时先诊断，不盲目重复执行**：优先 `doctor.cmd`、`python bridge.py --check`、扩展选项页“测试连接”、桥接控制台输出；对照 `docs/troubleshooting.md` 定位。避免在未知状态下反复点击录制/停止或重试上传（可能造成重复追加，且无幂等保护）。

---

## 3. 环境检查（以实际方法为准）

在 Windows PowerShell/终端中，于仓库根目录执行（按需）：

| 项目 | 检查方法（示例） | 说明 |
|---|---|---|
| Windows / Python | `python --version`；`doctor.cmd` 或 `python scripts/doctor.py` | `doctor.py` 要求 Python ≥ 3.9，并给出 PASS/WARN/FAIL 与修复建议（不打印密钥明文）。 |
| Hex | `doctor.cmd`（检查 `hex_exe`、`hex meetings list --json`、会议目录提示）；确认 Hex 已安装、麦克风/模型可用、**会议已启用**（`meetings.enabled=true`，需用户在 Hex 设置中确认） | `hex_exe` 可由 `config.local.json` 指定，或由脚本在 `%APPDATA%\hex\versions\*`/PATH 探测（以 `setup.py`/`doctor.py`/`bridge.py` 实现为准）。 |
| FlowUs CLI | `flowus --json whoami`；`doctor.cmd` | 需已认证且对目标页面/空间有写权限；失败时引导用户完成官方 CLI 登录流程（不要代填/索取凭据）。 |
| 浏览器扩展 | 检查 `extension/manifest.json` 等必需文件存在（`doctor.cmd` 含文件检查）；在 `chrome://extensions` 加载已解压 `extension/` 并查看扩展选项/Origin | 扩展 ID/Origin 由浏览器分配，需与桥接配置对齐（见第 5 节）。 |
| 配置文件 | `doctor.cmd`；`python bridge.py --check` | 合并读取 `config.json`、`config.local.json`（后者可覆盖）；校验 token 长度/占位符、Origin 格式、端口范围、`hex_exe` 路径等（具体规则见脚本/`bridge.py`）。 |

> `bridge.py --check`：用于配置/可启动性与浏览器可连接前提的轻量检查（退出码含义以 `bridge.py` 为准：通常 0=通过，1=可启动但浏览器侧前提未满足，2=无法启动/配置硬错误）。它**不**等同于端到端归档成功。

---

## 4. 安装与初始化（可执行顺序；区分自动/用户参与）

### 4.1 Agent 可协助执行（在用户授权与本机环境允许时）
1. **预检/诊断**：`doctor.cmd`（或 `python scripts/doctor.py`）查看缺失项与修复建议。
2. **生成/修复本地配置**：`setup.cmd`（或 `python scripts/setup.py`）按向导/参数创建或更新 `config.local.json`（幂等倾向、尽量保留未知键；覆盖 token 等敏感变更需按脚本交互/参数确认）。
   - 非交互示例（需显式参数，谨慎使用）：`python scripts/setup.py --yes ...`（仅在用户明确同意且参数充分时使用；避免擅自替换有效 token/origin）。
3. **桥接预检与启动**：`start.cmd`（推荐，先 `bridge.py --check` 再启动）；或分步 `python bridge.py --check`、`python bridge.py`。保持进程运行期间供扩展使用。

### 4.2 必须用户参与/确认的步骤（Agent 主要提供指导与核对）
- **安装/准备外部依赖**：安装 Python 3.9+、Hex（并在 Hex 中配置麦克风/模型与启用会议）、安装并登录 FlowUs CLI、安装 Chrome/Edge（若缺失）。Agent 不得静默执行来源不明安装器或远程脚本。
- **加载浏览器扩展**：`chrome://extensions` → 开发者模式 → “加载已解压的扩展程序” → 选择仓库 `extension/` 目录；确认站点权限覆盖 FlowUs。
- **扩展选项配置**：在扩展 Options 中保存与 `config.local.json` **相同**的 `bridge_token` 与 `bridge_port`；使用“测试连接”验证。
- **Origin 对齐**：从扩展选项页复制 **Extension Origin**（`chrome-extension://<扩展ID>`），写入 `config.local.json` 的 `bridge_allowed_origin`，并**重启 bridge**（扩展重载导致 ID 变化时需重新对齐）。
- **Hex/FlowUs 侧前置条件**：Hex 会议可用、麦克风权限正常；FlowUs CLI 已认证且用户对目标页面有编辑权限。
- **真实归档验证**：在用户**明确授权**的可编辑 FlowUs 页面上执行一次录音→停止→归档，并由用户确认页面内容正确（见第 6、8、9 节）。

---

## 5. 配置管理（实际字段；禁止泄露凭据）

配置文件：`config.example.json`（示例）、`config.local.json`（本地，优先/推荐）、`config.json`（可选，均 gitignored；`bridge.py`/`doctor.py` 合并读取）。关键字段（以代码为准）：

| 字段 | 作用 | 规则/注意 |
|---|---|---|
| `bridge_token` | 扩展与本地桥接的 Bearer 认证密钥 | **必填**，高熵随机，**长度 ≥ 32**，且不得为示例占位符；必须与扩展 Options 中保存值一致。修改后需重启桥接。**禁止输出真实值。** |
| `bridge_port` | 桥接监听端口（`127.0.0.1`） | 默认 `27123`；扩展 Options、`config.local.json`、运行中桥接需一致；变更后重启桥接并更新扩展设置。 |
| `bridge_allowed_origin` | 允许的浏览器扩展 Origin | 格式 `chrome-extension://<32 位小写字母 a-p>`；必须与已加载扩展实际 ID/Origin 一致；缺失或不匹配会导致浏览器请求被拒绝。修改后需重启桥接。 |
| `hex_exe` | Hex 可执行文件路径 | 可自动探测或手动指定；需存在且可用。 |
| `hex_meetings_dir` | Hex 会议产物目录（可选） | 为空时通常回退默认 `%APPDATA%\hex\meetings`（以实现为准）。 |
| `hex_export_dir` | Hex 导出目录（可选） | 用于转写路径解析的允许目录之一（以实现为准）。 |
| `sync_script` | 桥接调用的同步脚本路径 | 默认 `scripts/sync_to_flowus.py`（仓库布局）；相对路径相对仓库/桥接目录解析（以 `bridge.py` 为准）。 |

- **区分与禁止**：
  - **允许（本地产品行为）**：本地安装/配置程序（如 `scripts/setup.py`）在用户本机终端向用户展示新生成的 `bridge_token`，供用户复制到扩展选项与本地配置。
  - **禁止（Agent 行为）**：将真实 token/密码/Cookie 等凭据写入或转发到对话回复、日志、截图文本、Issue、补丁、代码或 Git 提交；不得代用户从终端输出中提取并复述 token。引用配置时仅可说明字段名、格式要求与脱敏状态（如“已配置/长度不足/占位符/已在本地终端显示”）。

---

## 6. 启动与端到端验证（分层判定，禁止混淆）

必须明确区分以下层级，**不得将任一项成功冒充为整个流程成功**：

1. **环境检查通过**：`doctor.cmd` 关键项 PASS（或已知 WARN 经用户确认可接受）；Python/Hex/FlowUs CLI/配置/扩展文件满足当前阶段要求。
2. **Bridge 启动成功**：`python bridge.py --check` 通过对应启动前提且 `python bridge.py`/`start.cmd` 成功监听 `http://127.0.0.1:<port>`（无配置/端口占用等启动错误）；可选用认证后的 `/ping`（或扩展测试连接）辅助确认可达性。
3. **浏览器扩展连接成功**：扩展 Options 中 token/端口正确且“测试连接”成功（或等价认证请求成功）；`bridge_allowed_origin` 与实际扩展一致且桥接已重启。
4. **录音和转写成功**：在用户授权的 FlowUs 页面触发录音/停止后，Hex 生成有效会议产物（音频、转写、manifest/完成状态等）；桥接状态推进至处理阶段且无 Hex/转写错误（以桥接日志/状态与本地产物为准）。
5. **FlowUs 页面归档成功**：目标页面**实际追加/生成**预期内容（概览/摘要/要点/待办、折叠转写、音频/文件块、可选思维导图等，以当次运行配置为准），且**由用户确认内容正确**。CLI 场景以命令成功输出与用户核验页面为准。

> 注意：`bridge.py --check` 的“浏览器可连接前提”通过 ≠ 扩展已实际连通并完成授权操作；`/ping` 成功 ≠ 已完成录音转写与 FlowUs 写入。

---

## 7. 故障处理

- **优先工具/入口**：`doctor.cmd`（`python scripts/doctor.py`）、`python bridge.py --check`、`start.cmd` 预检输出、扩展 Options 的“测试连接”、桥接控制台日志（UI 状态为脱敏提示）。
- **依据实际错误分类处理**（详见 [`docs/troubleshooting.md`](docs/troubleshooting.md)）：Python/配置解析失败、token 缺失或不匹配、Origin 被拒绝/未配置、端口占用或桥接不可达、扩展未加载/页面 ID 未识别、Hex 未就绪或会议未启用、FlowUs CLI 未认证/无权限/上传追加失败、重复追加（无幂等）等。
- **原则**：先定位根因（配置对齐、进程/端口、鉴权、外部依赖状态），再做最小修复并逐层复验；不要在失败原因不明时盲目重复执行录制、停止或同步命令。

---

## 8. 安全与授权边界

Agent **不得擅自**：
- 关闭防火墙或安全软件（包括 Windows Defender/防火墙）；
- 修改系统级环境变量或系统安全策略；
- 删除用户录音、转写、导出、配置文件或笔记内容；
- 上传或公开用户数据（含音频、转写、摘要、页面内容）；
- 将凭据（token/密码/密钥等）写入日志、代码、示例、截图文本或 Git 提交；
- 未经确认向真实 FlowUs 页面写入测试数据；
- 执行未经审查的远程脚本或来源不明的安装程序。

**必须先获得用户明确授权**的操作（并说明范围与风险）包括但不限于：向真实 FlowUs 页面/数据库写入或创建内容（含首次归档验证）、删除/覆盖任何本地或远端数据、修改或重新生成 `bridge_token`/鉴权信息、变更端口/Origin/权限相关配置、安装或更新系统/外部依赖、执行可能产生费用或影响他人协作空间的操作。

> 附加安全事实（实现已强制）：桥接仅本地回环、要求 token 与请求校验、对 Origin/Host/输入/路径做限制、`/state` 脱敏；但**工具与配置错误仍可能导致数据写入或泄露**，Agent 必须遵循最小授权与可验证原则。

---

## 9. 完成标准（“可用”需真实验证）

- **安装完成 ≠ 项目可用**。仅完成依赖安装、配置生成或桥接启动，不得宣称可用。
- **必须通过真实流程验证首次归档**：在用户明确指定且授权写入的可编辑 FlowUs 页面，完成一次录音→转写→上传/追加的端到端流程；并由**用户确认目标页面内容正确**（内容完整性、落位符合预期）。
- 如因权限、环境、外部服务或用户未授权而无法完成端到端验证，Agent 必须明确报告已完成与未完成的层级（按第 6 节）、阻塞原因及用户需参与的下一步，**不得虚报成功**。

---

## 10. 文档维护原则（对 Agent 与贡献者）

- 不复制整篇 README；优先引用 [`README.md`](README.md)、[`docs/quickstart-windows.md`](docs/quickstart-windows.md)、[`docs/troubleshooting.md`](docs/troubleshooting.md) 等现有文档。
- 不写入与当前代码不符的命令、参数、路径或配置字段。
- 不宣称尚未实现的自动化能力（如自动安装依赖、自动加载扩展并固定 ID、自动保证幂等/恢复等）。
- 明确标记仍需用户手动操作或用户授权的步骤；发现文档与实现漂移时，以代码为准并提示更新文档。