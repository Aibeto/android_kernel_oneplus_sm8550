# tools/dtbo — dtbo 分区离线改包工具

一加11（SM8550 / 22811）出厂 dtbo 里的 02/04/08 三个 entry 是 OPPO 把
oplus + audio + display + camera 四个 overlay 合并后的 783 fragments blob，
源码树单编 `salami-22811-kalama-overlay.dts` 只有 371 fragments / 114 KB，
**无法用源码重建**。所以所有 DT 修改都在这三个二进制 blob 上原地做。

## 三个工具

| 文件 | 作用 |
| --- | --- |
| `dtbo_tool.py` | 解包/重打包整块 dtbo 镜像：`extract <img> <dir>` / `repack <dir> <img>`；解析 Android dtbo 头表（9 个 entry + manifest），按 page_size 对齐，`--verify` 校验往返一致 |
| `fdt_setprop.py` | 单个 FDT 里按节点路径改/加属性；只支持原有的标量属性改写，不能新增节点 |
| `fdt_splice.py` | 主力：`--set/--setstr` 增改属性、`--splice` 插入独立编译的子树、`--anchor-prop` 按属性语义定位节点、`--force`、`--verify-only`、`--self-test` |

`fdt_splice.py` 自带三层校验：语义 diff（属性逐个比对）、`dtc` 反编后文本 diff、
以及"把插入的载荷整段剥掉后逐字节还原原 struct"。

## 关键坑：插入点不是固定 fragment 索引

`/fragment@47/__overlay__` 只对 entry 02 / 04 成立，entry 08 的 `&battery_charger`
在 `/fragment@42/__overlay__`（08 少了 7 个根 fragment）。按编号写会**静默**落到
`target = <&soc>` 的 overlay 上。用 `--anchor-prop qcom,pd_input_current_charger_ma`
定位，并确认唯一命中。

## 测试

- `test/`：dtbo_tool 的 extract/repack 往返用例（`orig.img` → `ex/` → `re.img`）。
- `test_splice/`：fdt_splice 的插入/二次插入/`--force`/坏输入用例。
- `python3 tools/dtbo/fdt_splice.py --self-test` 可自检。

实际的 PPS 载荷与重建流程见 `tools/pps-dtbo/`。
