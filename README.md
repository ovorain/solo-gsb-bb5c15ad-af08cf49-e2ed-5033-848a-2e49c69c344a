# 🧶 编织行指令审校（Knit Row Review）

一个用于校验编织逐行针数指令的 Web 应用：填写起始针数和逐行指令后，系统解析并展开每行，以上一行产出作为下一行的可用针数，逐行显示 **消耗 / 产出 / 剩余**；针数不匹配时**定位第一个无法执行的源指令**，并给出**短缺**或**未消耗**数量。编辑任一行后实时重算全部后续行，方案可导入 / 导出为 JSON。

- 后端：Python 3.11 + FastAPI（解析、展开、逐行审校引擎）
- 前端：原生 HTML / CSS / JavaScript（无构建、无前端框架）
- 部署：Dockerfile + Docker Compose，依赖安装与启动初始化都在容器启动流程中完成

---

## 一条命令启动

```bash
docker compose up --build -d
```

启动后访问：

> **http://localhost:8000**

停止：`docker compose down`；查看日志：`docker compose logs -f`。

### 必要配置

| 配置 | 说明 | 默认值 |
| --- | --- | --- |
| `HOST_PORT` | 宿主机映射端口（容器内固定监听 8000） | `8000` |

如需改用其他宿主机端口：

```bash
HOST_PORT=9000 docker compose up --build -d   # 访问 http://localhost:9000
```

容器启动时会执行初始化自检（校验 FastAPI / Uvicorn / Pydantic 依赖、校验应用模块可导入），随后启动 Uvicorn。无需数据库或其他外部服务。

---

## 语法规则

指令之间可用任意空白分隔（空白会被忽略），数字必须为正整数。

| 指令 | 含义 | 消耗 | 产出 |
| --- | --- | --- | --- |
| `kN` | 平针：编织 N 针 | N | N |
| `incN` | 加针：1 针放出 N 针 | 1 | N |
| `decN` | 减针：N 针并为 1 针 | N | 1 |
| `[指令序列]*M` | 将方括号内序列整体重复 M 次，**可嵌套** | 序列合计 × M | 序列合计 × M |

示例（起始 20 针）：

```text
[k2 inc1]*4 k8      # 耗 20 产 20
[k3 dec2]*4         # 耗 20 产 16
k16                 # 耗 16 产 16
[k3 inc2]*4         # 耗 16 产 20
```

嵌套示例：`[[k2]*2 inc1]*3` 表示 `(k2 k2 inc1)` 重复 3 次。

### 审校语义

- **每行可用针数 = 上一行产出**（第 1 行为填写的起始针数）。
- 展开行内指令并按顺序模拟执行；某条指令需要的针数超过当前剩余针数时：
  - 状态为「针数短缺」，在源文本中**高亮第一个无法执行的指令**；
  - 返回该指令位置（列区间）、`需要 N 针 / 仅剩 M 针 / 短缺 N-M 针`；
  - 该行不产生产出，**后续行标记为「已阻断」**（可用针数无法确定）。
- 一行指令全部可执行但**没有把可用针数用完**时：状态为「未全部消耗」，给出未消耗数量（该行仍正常产出，不阻断后续行）。
- 语法错误（未闭合的方括号、缺少数字、重复次数为 0 等）会在出错列标出 `▲` 并阻断后续行。
- 单条方案展开后的叶子指令上限为 100,000 条，防止超大重复次数导致的资源问题；界面上超过 200 条时只预览前 200 条并注明总数。

## 界面功能

- 左侧：起始针数、逐行指令编辑（添加 / 删除 / 上移 / 下移）、语法帮助；编辑后 250 ms 防抖自动重算，内容自动保存到浏览器 `localStorage`。
- 右侧：汇总徽标（通过 / 未耗尽 / 短缺 / 语法错误 / 阻断数量与最终针数）与逐行卡片，含行之间的「上一行产出 → 本行可用」流向、消耗 / 产出 / 剩余指标、错误高亮、展开指令列表。
- 顶部：**载入示例**、**导入 JSON**、**导出 JSON**。

### 方案 JSON 格式

```json
{
  "format": "knit-row-review/v1",
  "name": "编织方案",
  "start_stitches": 20,
  "rows": ["[k2 inc1]*4 k8", "[k3 dec2]*4", "k16", "[k3 inc2]*4"],
  "exported_at": "2026-09-20T00:00:00.000Z"
}
```

导入时只需包含 `start_stitches`（非负整数）和 `rows`（字符串数组）；其余字段可省略。

---

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/analyze` | 提交方案，返回逐行审校结果 |
| `GET` | `/api/health` | 健康检查，返回 `{"status":"ok"}` |
| `GET` | `/` | 前端页面 |

请求：

```bash
curl -s -X POST http://localhost:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"start_stitches": 6, "rows": ["k2 inc1 dec4"]}'
```

短缺响应（节选）：

```json
{
  "status": "shortage",
  "available": 6,
  "consumed": 3,
  "failing_instruction": "dec4",
  "failing_start": 8,
  "failing_end": 12,
  "shortage": 1,
  "error": "针数不足：执行 dec4 时需要 4 针，本行剩余可用仅 3 针，短缺 1 针"
}
```

---

## 本地开发（不使用 Docker）

```bash
pip install -r requirements-dev.txt        # 含 pytest / httpx
uvicorn app.main:app --reload --port 8000  # http://localhost:8000
pytest -q                                   # 运行测试（28 个用例）
```

项目结构：

```text
app/
  engine.py           # 解析器（递归下降）、repeat 展开、逐行审校
  main.py             # FastAPI：/api/analyze、/api/health、静态托管
  static/
    index.html        # 页面结构
    style.css         # 样式
    app.js            # 编辑器、防抖重算、结果渲染、JSON 导入导出
tests/                # 引擎与 API 测试
Dockerfile            # 镜像构建（构建时安装依赖）
docker-entrypoint.sh  # 容器启动初始化自检
docker-compose.yml    # 单服务编排 + 健康检查
```
