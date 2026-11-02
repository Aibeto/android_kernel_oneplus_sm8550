# 一加11（SM8550 / 22811）55W PPS 适配 · 交接文档

> 生成时间：2026-11-02 17:40（Asia/Shanghai）
> 工作区：`/home/aibeto/code/android_kernel_oneplus_sm8550`（分支 `feat/16.0/oneplus_11/pps`）
> 状态：**代码已改、未提交、未上机验证**。上一步只验证了 27W（纯 DT 数值，二进制改 dtbo）。

---

## 1. 一句话现状

一加11 的 PD 输入限流已经从 2 A 提到 3 A（**真机实测 27 W 成功**）。
PPS 的 DT 属性也已写进源码，但**还没生成可刷的 dtbo**，因为「源码 → stock blob」对不上（见 §6 阻塞）。

---

## 2. 已完成且已验证

| 项 | 内容 | 证据 |
|---|---|---|
| ADSP 支持 PPS | `dmesg` 反复打印 `oplus_sm8150_get_pd_type, pst->prop[prop_id]=8`（8=PD_PPS），`usb_type=PD_PPS` | 用户真机日志 |
| PPS 引擎在 stock 模块里 | `strings /vendor_dlkm/lib/modules/oplus_chg.ko \| grep -c pps_pdo_select` = **1** | 用户真机执行 |
| 27 W 达成 | 只改 dtbo 里 `qcom,pd_input_current_charger_ma` 2000→3000 | 用户原话："实测27w PD已实现" |
| dtbo 改包工具链 | `tools/dtbo/{dtbo_tool.py,fdt_setprop.py,mkdtboimg.py}` | 逐字节校验通过，见 §4 |

---

## 3. 当前未提交改动（都在子模块 `devicetree/sm8550` 内）

```
devicetree/sm8550$ git diff --stat
 .../oplus/oplus_chg/oplus-chg-22811.dtsi        | 25 +++++++++++++++++++++-
 .../charger/charger_ic/oplus_battery_sm8550.c   | 18 ++++++++++++++++
```

### 3.1 DT：`oplus-chg-22811.dtsi`（+25/−1）

位置：`&battery_charger` 内，`qcom,qc_input_current_charger_ma` 之后。两处改动：

1. `qcom,pd_input_current_charger_ma` 2000 → **3000**（已上机验证 27 W）
2. 新增 22 行 PPS 属性（**尚未上机**）：

```dts
/* PPS 55W (11V x 5A) - enable PPS engine for third-party adapters */
oplus,pps_support_type = <1>;
oplus,pps_support_third = <1>;
oplus,pps_region_list = <0xFFFF>;
oplus,pps_warm_allow_vol = <4000>;
oplus,pps_warm_allow_soc = <55>;
oplus,pps_strategy_normal_current = <5000>;
oplus,pps_over_high_or_low_current = <5000>;
oplus,pps_timeout_third = <15000>;
oplus,pps_timeout_oplus = <15000>;
oplus,pps_ibat_over_third = <11000>;
oplus,pps_ibat_over_oplus = <11000>;
oplus,pps_full_cool_sw_vbat = <4500>;
oplus,pps_full_normal_sw_vbat = <4500>;
oplus,pps_full_normal_hw_vbat = <4510>;
oplus,pps_full_ffc_vbat = <4520>;
oplus,pps_full_warm_vbat = <4130>;
oplus,pps_full_cool_sw_vbat_third = <4500>;
oplus,pps_full_normal_sw_vbat_third = <4500>;
oplus,pps_full_normal_hw_vbat_third = <4530>;
oplus,pps_strategy_batt_high_temp = <410 430 435 400 425 430>;
oplus,pps_strategy_high_current = <5000 5000 3000 5000 5000 5000>;
```

### 3.2 驱动：`oplus_battery_sm8550.c`（+18）

class attribute 写探针（debugfs 在一加11 上被 SELinux 禁掉，故改挂 class）：

- 前向声明 `int oplus_chg_set_pps_config(int vbus_mv, int ibus_ma);`
- `static ssize_t pps_req_store(struct class *c, struct class_attribute *attr, const char *buf, size_t count)`：`sscanf(buf, "%d %d", ...)` → 调 `oplus_chg_set_pps_config()` → `chg_err` 打印返回值
- `static CLASS_ATTR_WO(pps_req);` 并加入 `battery_class_attrs[]`
- 入口：`/sys/class/qcom-battery/pps_req`（class 名 `qcom-battery`，定义在 `oplus_battery_sm8550.c:13869`）

用法：`echo "11000 5000" > /sys/class/qcom-battery/pps_req`

---

## 4. 工具（`tools/dtbo/`）

| 文件 | 作用 | 状态 |
|---|---|---|
| `dtbo_tool.py` | `extract <img> <dir>` / `repack <dir> <img>`；FDT header v0.4 大端 | 合成镜像往返**字节一致** |
| `fdt_setprop.py` | `fdt_setprop.py <blob> <prop> <hex>`，只改已有 u32 属性 | 已在真机生效 |
| `mkdtboimg.py` | AOSP 官方打包器（仓库自带的那份是断链） | 可用 |

工作产物：
- 原始镜像：`/home/aibeto/dtbo_stock.img`（24 MB，设备 dd）
- 解包目录：`/home/aibeto/dtbo_work`、原始备份 `/home/aibeto/dtbo_ref`
- 已刷过的 27W 镜像：`/home/aibeto/dtbo_new.img`（只改 02/04/08 三个 blob，与 stock 仅差 6 字节）

设备 dtbo 结构（重要）：9 个 overlay = **msm-id × hw-id 矩阵**
- msm-id 三组：`0x258/0x259`（blob 00/03/07）、`0x25c`（01/05/06）、`0x207/0x218`（02/04/08）
- 本机 live DT 为 `0x207/0x218` → **只匹配 02/04/08**，改这三个就够
- 九个 blob 的 `oplus,project-id` 都是 `<0x591b 0x594e 0x594d 0x5950 0x594f>`（0x591b=22811）

---

## 5. 关键代码事实（给下一个人省时间）

### 5.1 PPS 引擎的门槛只有一条硬性的

`vendor/oplus/kernel/charger/oplus_pps.c:499-504`：

```c
rc = of_property_read_u32(node, "oplus,pps_support_type", &chip->pps_support_type);
if (rc || chip->pps_support_type == 0) {
        chip->pps_support_type = 0;
        return -ENODEV;          /* 整段 PPS 直接退出 */
}
```

其余 30+ 属性都有缺省值（`pps_strategy_normal_current` 缺省 6000 等），读不到不致命。

### 5.2 ops（`pps_pdo_select`）很可能已经注册了

`chargepump_ic/oplus_pps_cp.c:556` 的 `oplus_pps_cp_init()` **无条件**执行：

```c
oplus_pps_ops_register("cp-sc8571", &oplus_cp_pps_ops);
```

而 `oplus_battery_sm8550.c:14110` 在 probe 里**无条件**调用 `oplus_pps_cp_init()`。
它的 `.pps_pdo_select = oplus_chg_set_pps_config`（:523），直接走 ADSP 的 `USB_SET_PPS_VOLT/CURR`。

**推论**：一加11 很可能不需要任何 CP 硬件，DT 属性一到位，PPS 请求链路就通；
CP 相关的 `pps_cp_*` 调用会失败，但 `oplus_pps_get_cp_ratio()` 在无 CP 时返回 `PPS_BYPASS_MODE`（直充）。
**这是「一步上 55 W」最关键的假设，待真机验证。**

### 5.3 第三方 PPS 的两道闸

| 闸 | 位置 | 现状 |
|---|---|---|
| 区域白名单 | `v1/oplus_region_check.c:34` `oplus,pps_region_list`；:173-177 单个 `0xFFFF` 即全放行 | 全仓库没有设备设过 → 默认全关，**必须在 DT 里写 0xFFFF** |
| 电压上限 | `oplus_pps.h:141` `PPS_3RD_ASK_VOLT_MAX = 10500`；`oplus_pps.c:4212` 无条件 clamp | 第三方头只能要到 10.5 V，5 A 时 52.5 W。**要真 55 W 需把它改成 11000**（改常量，需重编模块） |

### 5.4 版本关系

- 一加11 = salami-22811（kalama），走 **v1**：`compatible "qcom,battery-charger"` → `oplus_battery_sm8550.c:14065`
- 一加12/13 走 **v2**：`compatible "oplus,hal_sm8350"` → `v2/charger_ic/oplus_hal_adsp.c:15975-15977`
- 8550 内核两套都编（`kalama_GKI.config:622 CONFIG_OPLUS_CHG=m`、`:633 CONFIG_OPLUS_CHG_V2=m`），差别在 DT 绑定
- 一加12 的 PPS 参数：`11000 / 3000`（`oplus-chg-22825-pps.dtsi:24-25`）；一加13：`11000 / 5000`（`oplus-chg-24821.dtsi:1388-1389`）

---

## 6. 阻塞：源码编出来的 dtbo 和 stock 对不上

用 `cpp + dtc -@` 编 `oplus/salami-22811-kalama-overlay.dts`：

| | 大小 | fragment 数 | 反编行数 |
|---|---|---|---|
| 源码编译结果 | 113402 B | 371 | 5299 |
| 设备 stock blob 02 | 458901 B | 783 | 16090 |

**差约一倍，说明 stock 的 02/04/08 不是这个 .dts 直接编出来的**（可能是带额外 include 的变体，或 OPPO 用了不同的合并方式）。

⚠️ **在搞清这一点之前，不要把源码编出来的 dtbo 刷进机器。**

---

## 7. 下一步（按优先级）

### A. 先验证 PPS 能不能起来（不需要解决 §6）

两条路，任选：

**A1. 二进制插入属性（我原本打算做，未完成）**
在 stock blob 的 `battery_charger` 节点里直接插入 `oplus,pps_support_type` 等 u32 属性。
需要扩展 `fdt_setprop.py` 支持「插入」：改 property 块、更新 `size_dt_struct` 与后续偏移。
能插标量，插不了 phandle 引用和子节点——但按 §5.2 推断，标量可能就够了。

**A2. 解决 §6，走正常构建**
对比 stock 与编译结果的 fragment 目标列表，找出缺的是哪些 include；修好后 `make dtbs` 产物即可刷。

### B. 真 55 W 需要改常量

`oplus_pps.h:141` `PPS_3RD_ASK_VOLT_MAX` 10500 → 11000，然后重编 `oplus_chg.ko`（vendor_dlkm）。

### C. 构建与刷写（用户执行）

**dtbo**（改 DT 后）：
```bash
# 完整 kernel_platform 树内
make O=out ARCH=arm64 LLVM=1 LLVM_IAS=1 ... dtbs
python3 tools/dtbo/mkdtboimg.py create dtbo.img --page_size=4096 <所有 .dtbo>
fastboot flash dtbo dtbo.img
```
或用 §4 的 `dtbo_tool.py repack` 把新 blob 塞回原镜像（保留 9 个 entry 的顺序/id/rev）。

**模块**（改 `PPS_3RD_ASK_VOLT_MAX` 或驱动后）：
```bash
# 只编 vendor_dlkm 模块
make O=out ARCH=arm64 ... modules
# 产物 out/vendor/oplus/kernel/charger/v1/oplus_chg.ko → 打进 vendor_dlkm.img
fastboot flash vendor_dlkm vendor_dlkm.img
```

**注意**：`scripts/build-gki.sh` **不能**用于本任务——它只编 `common/` 的 GKI `Image`（无 modules、无 dtbo），
`scripts/anykernel.sh` 也只换 boot 分区的 kernel。

回滚：`fastboot flash dtbo /home/aibeto/dtbo_stock.img`

---

## 8. 设备与约束

- 设备：OP591BL1（一加11），`ro.boot.prjname=22811`
- BL 已解锁、AVB 已关闭（用户确认）；**刷写、adb、构建都由用户本人执行**，助手只出命令
- 一加11 硬件：双电芯 2S（`qcom,vbatt_num=<2>`）、VOOC 域上限 11 V/9.1 A（`oplus-chg-22811.dtsi:472-473`）
- 一加11 DT 里**没有任何 CP 节点**（sc8547/sc8517/max77939/hl7227 全无），100 W 由 ADSP 的 `oplus,adsp-voocphy` 控制

---

## 9. 相关文档

- `PPS_55W_MIGRATION_PLAN.md` —— 完整方案、E1~E12 证据链、P0 实测、P0.5 写探针、27W 结果
- `tools/dtbo/` —— 三个工具
- 本文件 —— 交接速览
