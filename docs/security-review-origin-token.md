# Origin / Token 校验点审阅（bridge.py）

> 审阅对象：commit `fix(bridge): allow extension requests without Origin`
> 审阅日期：2026-11-16　范围：`bridge.py` 鉴权与请求校验
> 行号以审阅时 `bridge.py` 为准

## 一、逐项结论

| # | 审阅点 | 期望行为 | 结论 | 位置 |
|---|---|---|---|---|
| 1 | token 长度下限 | ≥32 字符，不足拒绝启动 | ✅ | `--check`: 1367；运行时: 1405 |
| 2 | token 比较方式 | `hmac.compare_digest` 常量时间 | ✅ | 1121 |
| 3 | token 缺失处理 | 无头/格式错 → 401，不进业务 | ✅ 未配置时 503（更准确） | 1222–1229 |
| 4 | 绑定地址 | 只 bind `127.0.0.1` | ✅ 硬编码 + 运行时兜底 | 81 / 1397–1398 |
| 5 | Host 头校验 | 只认环回地址 | ✅ **比清单更严**：只接受 `127.0.0.1:<port>`，不接受 `localhost` / `[::1]` | 1204–1207 |
| 6 | Origin 严格模式 | 配置了就必须有 Origin | ❌ **与 README 声明冲突**（见第二节） | 1209–1215 |
| 7 | 放宽分支触发条件 | ①未配置才放行 ②配置了必须拒 ③不绕过 token | ⚠️ ① ② 不成立，③ 成立（已确认 token 未被绕过） | 1209–1215 / 1222 |
| 8 | Origin 精确匹配 | 全串相等，无前缀/子串匹配 | ✅ 且已补前缀攻击用例 | 1213 |
| 9 | 放宽与第 6 点是否冲突 | 交叉比对 | ❌ **冲突成立**，决策见第二节 | — |
| 10 | Content-Type 校验 | POST 必须 `application/json` | ✅ | 1159–1161 |
| 11 | 请求体大小上限 | 先限长再读 | ✅ 8 KB（`MAX_BODY_SIZE`）+ socket 超时 | 32 / 1169–1174 |
| 12 | 输入校验（ID/URL） | ID 白名单、URL 校验协议与域 | ✅ `UUID_RE` / `_valid_page_id` / `_is_flowus_url` | 34 / 1089 / 1076 |
| 13 | 路径限制 | `realpath` 后必须落在允许目录 | ✅ 双重防护：`_is_inside` + `_job_dir` 字符白名单 | 139–146 / 685–686 |
| 14 | `/state` 脱敏 | 不含 token、完整路径、page ID | ✅ 仅 status/message/不透明 id | 1093–1113 |
| 15 | 敏感端点鉴权覆盖 | 全部走鉴权 | ✅ `do_GET`/`do_POST` 先鉴权再路由；`do_OPTIONS` 不鉴权（正确，预检无法携带凭据） | 1239 / 1252 |
| 16 | `/reset` 风险 | 不清 ledger、不删 artifact | ✅ 仅重置内存态 | 1334–1340 |
| 17 | 错误响应不泄露 | 不含内部路径/堆栈/token | ⚠️ **发现缺陷并已修复**（见第三节） | 1331–1333 |
| 18 | 配置解析失败 | 明确报文件+原因，停止启动 | ✅ `SystemExit(2)` | 43–55 |

## 二、关键分歧：第 6/7/9 点（已决策）

### 冲突事实
- README/文档原声明：配置 `bridge_allowed_origin` 后，**缺失 Origin 应拒绝**（严格模式）。
- 实际实现：`if origin and origin != ALLOWED_ORIGIN: reject` —— 缺失 Origin **一律放行**。
- 清单建议：改为"仅在未配置 `bridge_allowed_origin` 时放行缺失 Origin"。

### 为什么不采纳清单建议
Chrome 157 起，扩展 service worker 的 `fetch()` **不发送 Origin**（`Origin` 属浏览器禁用头，扩展代码无法补）。若按清单第 6 点收紧为"配置了就必须有 Origin"，则：

> 配置了 `bridge_allowed_origin` ⇒ 扩展永远拿不到 200 ⇒ "测试连接"永久失败

而这正是本次修复要解决的故障。两种口径不可同时成立。

### 采用方案：保留放宽，修正文档
放开放行缺失 Origin **并未削弱对网页的防护**：

- 普通网页发起跨站请求时**必然携带 Origin**，会被精确匹配拦下；
- 缺失 Origin 的请求不可能来自网页上下文，只可能来自扩展/本地程序；
- 真正的身份门是 `Authorization: Bearer`（常量时间比较，长度下限 32）。

因此定稿语义为：**「有 Origin 必须精确匹配；无 Origin 放行；token 始终强制」**。
`AGENTS.md` 第 6/7/8 节与 `README.md` 需与此保持一致。

## 三、已修复缺陷

### 缺陷 1（第 17 点）：`/archives` 500 响应泄露内部异常
原实现 `self._send(500, {"ok": False, "error": str(e)})` 会把 SQLite 路径、SQL 片段等内部信息返回给客户端。

修复后：

```python
except Exception as e:
    sys.stderr.write("[bridge] /archives failed: %r\n" % (e,))
    self._send(500, {"ok": False, "error": "internal error"})
```

### 观察项（非缺陷，记录备查）
- `/retry` 的 `archive_id` 未做 UUID 校验，但下游 `_job_dir()` 用 `[^A-Za-z0-9._:-]` 白名单清洗且 `/` 会被替换，无目录穿越风险。
- Host 校验不接受 `localhost:<port>`；若未来允许，需同步放宽 `EXPECTED_HOST`，当前保持严格。

## 四、新增回归测试

`tests/test_bridge_origin_auth.py`（9 个用例，纯标准库，真实起进程 + 真实 HTTP）：

| 用例 | 断言 |
|---|---|
| 无 Origin + 合法 token | 200（锁定 Chrome 扩展行为） |
| 无 Origin 访问 `/state` | 200 |
| 正确 Origin | 200 |
| 错误 Origin | 403 |
| Origin 前缀扩展（`<id>x`） | 403（防前缀匹配） |
| 无 token | 401 |
| 错误 token | 401 |
| `Basic` 方案 | 401 |
| 非环回 Host 头 | 403 |

运行：

```bash
python -m unittest tests.test_bridge_origin_auth -v
```

## 五、幂等归档测试现状（对照原骨架）

`tests/test_archive_idempotency.py` 已存在 11 个用例，且已对齐真实 API（`ArchiveStore` / `make_archive_id` / `claim` / `mark_done` / `mark_error` / `mark_needs_review`），覆盖：

- 首次归档写入、二次归档跳过（`claim` 原子性防并发重复）
- 远端 marker 已存在时跳过（`test_main_skips_when_marker_already_present`）
- 追加失败转 `needs_review`（不确定写入）
- `error` 可重试且步骤持久化
- 同 meeting 不同 page 不互相去重

即骨架中的 5 个用例已全部覆盖，并多出 marker 文本、临时目录唯一性等边界。**无需再造骨架**，只需在后续改动 `claim`/`mark_*` 语义时保持该套用例为绿。
