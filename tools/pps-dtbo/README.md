# tools/pps-dtbo — 一加11 第三方 PPS 的 dtbo 载荷与重建

把 PPS 需要的一组 DT 属性（标量 + `pps_charge_third_strategy` 曲线子树）插入
**出厂 dtbo 镜像**的 `&battery_charger` 节点，产出可刷的 `dtbo.img`。

## 用法

```bash
bash tools/pps-dtbo/build.sh 1   # 只插标量/字符串属性
bash tools/pps-dtbo/build.sh 2   # 标量 + 曲线子树（当前在用的版本）
```

输入就是本目录的 `payload/`：

| 文件 | 内容 |
| --- | --- |
| `payload/pps_props.txt` | 标量属性载荷，一行一个属性（`name = <...>;` / `name = "..."`），支持注释 |
| `payload/pps_subtree.dts` | 曲线子树，独立 `dtc -@` 编译后整段 splice 进去 |

产物（不进 git，构建中途文件一律落在 `artifacts/`）：

- `artifacts/dtbo/work/` — 出厂 entry 的纯净副本与编译产物，从不原地修改
- `artifacts/dtbo/out/stage<N>_*/entries/*.dtbo` — 9 个 entry，只改 02/04/08
- `artifacts/dtbo/out/stage<N>_*/dtbo.img` — 整块可刷镜像
- `artifacts/dtbo/out/stage<N>_*/decompiles/` — 每个 blob 改动前后的 dts 与 unified diff
- `artifacts/dtbo/out/stage<N>_*/summary.txt` — 字节差统计

`STOCK_IMG=/path/to/dtbo_stock.img` 可覆盖出厂镜像路径（只读，绝不原地改）。

## 校验

`build.sh` 内部做的检查，任何一条不过就退出：

1. `validate_payload.py` — 属性名/类型/取值域，曲线结构必须严格 6 SOC × 6 温度档；
2. `fdt_splice.py --verify-only` — 载荷能被重新解析；
3. `verify_payload.py` — 改完后按驱动读法逐属性比对期望值；
4. 6 个不匹配本机的 entry 必须与出厂字节逐一 `cmp` 相同；
5. 重打包后再解包，9 个 entry 必须与原 entry 一致。

插入点用 `--anchor-prop qcom,pd_input_current_charger_ma` 定位（02/04 在
`/fragment@47/__overlay__`，08 在 `/fragment@42/__overlay__`），不能按索引写。

## 与源码树的一致性

`kernel_platform/.../oplus_chg/oplus-chg-22811.dtsi` 里那份 `oplus,pps_*` 是同一套
标量的可读副本（源码树无法复现出厂 blob，见 `PPS_HANDOFF.md`）。改载荷时两边一起改。
