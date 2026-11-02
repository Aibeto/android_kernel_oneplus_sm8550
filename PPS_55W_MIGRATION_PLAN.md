# 一加11（sm8550 / 项目 22811）启用 55W PPS 迁移计划

> 状态：**草案，未经验证**。本文中的推断均以「假设」标注，需按 §5 的判据逐条证伪。
> 分支：`feat/16.0/oneplus_11/pps`　工作区：`/home/aibeto/code/android_kernel_oneplus_sm8550`
> 说明：本文不预设"能做"或"不能做"，只定义验证顺序与失败分支。

> **v2 修订（详细复查后，2026-11-02）**：本版推翻了 v1 草案中的三处判断——
> ① 原以为"只改 DT 就能试 PPS"：**不成立**，v1 的请求必须经 `ops->pps_pdo_select()`（`oplus_pps.c:4402`），无 ops 则引擎只会重试 100 次后放弃（`:4880-4892`）；
> ② 原以为"第三方 PPS 受 NV 锁死"：**不成立**，闸门其实在 DT——`oplus_region_check.c:34` 的 `oplus,pps_region_list`，只要列表只有一个 `0xFFFF` 即全 region 放行（`:173-177`）；
> ③ 原以为"一加12 的 HL7227 可复用"：**不成立**，`oplus_hl7227.c:694` 只提供 compatible，未注册 PPS ops。
> 详见 §9 复查记录。


---

## 1. 目标与边界

| 项 | 内容 |
|---|---|
| 目标 | 一加11 在**公版 PPS**（标准 PD3.0 APDO，11 V / 5 A）下稳定充电，不依赖厂商白名单与私有适配器 |
| 不做 | 6 A（超 USB-C 5 A 规范，属 SVOOC 私有域）；不改 VOOC/SVOOC 既有行为 |
| 成功判据 | `usb_type = PD_PPS`、VBUS 11.0 V ±0.1、IBUS 稳态 5.0 A（或工程折中值）、壳温 <43 ℃ 不降档、可回退 |

---

## 2. 已核实的事实（有源码依据）

### 2.1 三机型充电栈的真实分界

| 机型 | `&battery_charger` compatible | 绑定驱动 |
|---|---|---|
| 一加11（22811） | `"qcom,battery-charger"`（未改） | v1 `oplus_battery_sm8550.c:14065` |
| 一加12/13（22825/24821） | `"oplus,hal_sm8350"` | v2 `v2/charger_ic/oplus_hal_adsp.c:15975-15977` |

一加11 的 `kalama_GKI.config` 同时开 `CONFIG_OPLUS_CHG=m`(:622) 与 `CONFIG_OPLUS_CHG_V2=m`(:633)，两套代码都编进内核；**差别在 DT 绑定，不在编译**。

### 2.2 v1 的 PPS 引擎启动条件（关键）

- `oplus,pps_support_type` 必须非 0，否则整条 PPS 关闭（`oplus_pps.c:499-504`）。
- 必须有 `chip->ops`（`struct oplus_pps_operations`）。ops 由 CP/MCU 设备注册：
  - `voicephy/oplus_cp_intf.c:553-575`：`oplus_cp_device_register()` → `oplus_pps_ops_register(cp_dev->dev_name, &oplus_cp_pps_ops)`
  - 注册者：`oplus_sc8547.c:2070`、`oplus_sc8547_slave.c:1119`、`oplus_sc6607_charger.c:7231`、`v1/ufcs/ufcs_ic/oplus_sc8547a.c:2529`、`vooc_ic/oplus_op10.c:1151`（"mcu-op10"）、`chargepump_ic/oplus_pps_cp.c:569`（"cp-sc8571"）
  - **`oplus_hl7227.c` 只提供 `compatible = "oplus,hl7227-cp"`（:694），未注册 PPS ops**
- ops 里的核心是 `pps_pdo_select(int vbus_mv, int ibus_ma)`（`oplus_pps.h:724`）——真正的"要电压/要电流"。
- 无 ops 时 `oplus_pps_init()` 会调度 `oplus_pps_get_ops_work` 无限重试（`oplus_pps.c:5379+`）。

### 2.3 CP 与 cp_ratio

- `PPS_BYPASS_MODE = 1`、`PPS_SC_MODE`（`oplus_pps.h:160`）。
- `oplus_pps_get_cp_ratio()`（`oplus_pps.c:1233`）：无 `ops->pps_cp_mode_init` 或非 SC 模式 → 返回 BYPASS(1)。
- 调压函数用 `cp_ratio` 算电压下限（`oplus_pps.c:2245` 附近：`charger_volt_min = vbat*batt_num*cp_ratio + 余量`）。

### 2.4 第三方（公版）PPS 的限幅

- `PPS_3RD_ASK_VOLT_MAX = 10500`（`oplus_pps.h:141`），在调压函数中**无条件** clamp（`oplus_pps.c:4212`）→ 公版 PPS 名义上限 10.5 V，5 A 时 **52.5 W**。
- `oplus,pps_support_third`（`oplus_pps.c:507`）还要过 `third_pps_supported_from_nvid()`（:512、:5637）才生效。

### 2.5 一加11 的硬件事实

- 双电芯 2S：`qcom,vbatt_num = <2>`（22811:401）；`qcom,dual_ffc`（:403）。
- VOOC 域上限：`qcom,vooc-max-input-volt-support = <11000>`、`...current-support = <9100>`（:472-473）→ 100 W 链路已验证。
- 22811 **没有任何 CP 节点**（sc8547/sc8517/max77939/hl7227 均未出现），CP 由 ADSP 的 `oplus,adsp-voocphy`（:8）控制。
- `qcom,pd_input_current_charger_ma = <2000>`（:257）：固定 PD 档 2 A。

---

## 3. 待验证假设（本文成立的前提，逐条可证伪）

| 编号 | 假设 | 证伪方式 | 若不成立 |
|---|---|---|---|
| **H1** | ADSP 固件实现了 PPS APDO 协商，并接受 `USB_SET_PPS_VOLT/CURR` | 见 §5 P0 | 除路线 C 外全部作废 |
| **H2** | 一加11 的 2:1 变换在 ADSP 内完成，AP 侧 `cp_ratio=BYPASS` 不影响实际功率 | P1 实测功率 | 必须走路线 B1（接真实 CP） |
| **H3** | v1 的 `oplus_pps` 策略引擎在 `ops` 仅实现 `pps_pdo_select` 等必要项时能正常运行 | P1 编译 + 运行 | 需补更多 ops 或改引擎 |
| **H4** | 一加11 的 CP 若存在，型号在 SC8547/SC8571 系（v1 有 ops 实现） | 硬件资料/dmesg | 若为 HL7227，v1 无 ops，只能自建适配层或走 v2 |
| **H5** | 公版 PPS 不依赖 `oplus,cpa` 协议仲裁（22811 无此节点） | P1 行为观察 | 需补 cpa 节点或绕过 |

---

## 4. 四条可选路线

| 路线 | 做法 | 改动面 | 风险 | 何时选 |
|---|---|---|---|---|
| **A. 切到 v2 栈** | `&battery_charger` 改 `"oplus,hal_sm8350"`，搬一加12/13 的 HAL + PPS + CP 框架 | 整条充电子系统 | 高（SVOOC/无线充/热控全受影响） | H1 成立且 H3 不成立时 |
| **B1. 接真实 CP** | DT 实例化 CP（SC8547/SC8571），由现成驱动注册 ops | DT + 可能补驱动 | 中（依赖硬件确实有该 CP） | H4 成立 |
| **B2. 自建 ops 适配层** | 写一个轻量 ops 提供者，`pps_pdo_select` → 复用 `oplus_chg_set_pps_config()`（`oplus_battery_sm8550.c:11112`），CP 相关 ops 留空 | 新增一个小驱动/或挂在充电驱动里 | 低-中（不动物理层） | H1、H2 成立，H4 不成立 |
| **C. 标准 PD 栈** | 启用 QTI engine 或 TCPM 的 PPS，用标准 `power_supply` 属性调压 | 需补 Type-C/PD PHY DT 节点 + 与 ADSP UCSI 争 owner | 高（架构冲突） | H1 不成立 |

**推荐顺序：B2 → B1 → A → C。** 理由：B2 不碰物理层、不改充电栈，只用已有的 ADSP 通道；若 H2 成立（ADSP 自己管 CP），B2 就是完整方案。

---

## 5. 分阶段实施

### P0　能力探针（零代码改动，0.5 天）

**目的**：证伪或确认 H1。

1. 抓 `MSG_OWNER_BC`（32778）的 GLINK 收发：插入支持 PPS 的源后，观察是否上报 APDO、`USB_PPS_*` 系列属性是否返回合理值。
2. `cat /sys/class/power_supply/usb/usb_type`：是否出现过 `PD_PPS`。
3. 若驱动有 debugfs/proc 节点，读取 ADSP 上报的 PDO 列表。

**判据**：ADSP 能报出 PPS APDO。
**失败**：走路线 C，或终止。

---

### P1　最小可运行（B2 路线，3~5 天）

**目的**：让 v1 的 PPS 引擎跑起来，验证 H2/H3/H5。

1. **ops 适配层**：实现 `struct oplus_pps_operations` 的最小集合
   - `pps_pdo_select(vbus_mv, ibus_ma)` → 调 `oplus_chg_set_pps_config()`
   - `get_pps_status()` → `oplus_chg_get_pps_status()`
   - `pps_get_authentiate()` → 读 ADSP 属性（若无则返回固定值）
   - CP 相关（`pps_cp_*`）全部留 NULL
   - 通过 `oplus_pps_ops_register("<name>", &ops)` 注册
2. **DT**：在 `&battery_charger` 内（**不是 `&soc`**）加：
   - `oplus,pps_support_type = <1>`（必须非 0）
   - `oplus,pps_support_third = <1>`
   - `oplus,pps_strategy_normal_current = <5000>`
   - 子节点 `pps_charge_third_strategy`（v1 认的名字，`:915`），首列 11000，参考 24821 的数值改写
3. **不要**写 `oplus,curr_max_ma`（v1 不解析，全树零命中）；v1 的 target 电压来自策略表首列（`:988`）。

**判据**：`usb_type = PD_PPS`、VBUS 拉到 11 V、有实际充电电流。
**观测点**：功率是否随 `pps_strategy_normal_current` 变化；`cp_ratio` 是否为 1。

- 若功率接近 5 A × 11 V → H2 成立，跳到 P3。
- 若功率卡在 30~40 W → 说明走 buck 直充，H2 不成立，转路线 B1。

---

### P2　（条件触发）接电荷泵（视 H4，1 天~1 周）

1. 确认 CP 型号（原理图 / dmesg / ADSP 日志）。
2. 若为 SC8547/SC8571 系：DT 实例化 CP 节点（参考 23861 的 `sc8517` 写法），驱动会自动 `oplus_cp_device_register()` 并注册 ops，届时保留 P1 的 ops 适配层作为兜底或删除。
3. 若为 HL7227：v1 无 ops 实现（`oplus_hl7227.c:694` 只有 compatible），需新增适配或转路线 A。

**判据**：`cp_ratio` 变为 SC 模式、CP 的 VBUS/IBUS 与电池电流出现 2:1 关系。

---

### P3　策略整定（3~5 天，需真机标定）

1. 按 24821 的数值改写，但换成 v1 键名：

| 24821（v2） | 一加11（v1） |
|---|---|
| `curr_max_ma = <5000>` | 策略表首列 11000 + `pps_strategy_normal_current` |
| `pps_strategy_batt_high_temp` / `high_current` | 同名可用（`oplus_pps.c:709` / `:733`） |
| `temperature_strategy_name = "cgcl"` | v1 无此框架，用 `batt_high_temp` + `high_current` 近似 |
| `plc_strategy-data` | v1 无 plc，忽略 |
| `pps_ibat_over_oplus = <11400>` | 同名可用（`:646`），按 22811 电池重标 |

2. 解 `PPS_3RD_ASK_VOLT_MAX = 10500` 的帽子（`oplus_pps.h:141`）——**放在最后做**，前面没跑通前改它没有意义。
3. 温度/电流标定：壳温 43 ℃ 以上必须降档，参照 24821 的 `temperature_strategy_data` 形状。

---

### P4　验收与回退

**验收**
- `usb_type = PD_PPS`；VBUS 11.0 V ±0.1；IBUS 稳态 5.0 A；电池 8.8 V / 6.2 A（2S）
- 壳温 <43 ℃ 不降档；插拔/硬复位后能回固定 PDO
- 3 A 线自动回落，不报错

**回退**
- DT：`oplus,pps_support_type = <0>` 或删节点 → 恢复 18 W 行为
- 驱动：ops 适配层独立文件，删除即回退

---

## 6. 明确不做

1. 不把 compatible 改成 `"oplus,hal_sm8350"`（除非走路线 A）
2. 不照抄 24821 的 `&soc { oplus_pps_charge }` 结构（那是 v2 的 platform driver）
3. 不写 `oplus,curr_max_ma`（v1 不解析）
4. 不在 P0/P1 通过前改 `PPS_3RD_ASK_VOLT_MAX`
5. 不动 6 A（超 USB-C 规范）

---

## 7. 关键文件索引

| 用途 | 路径 |
|---|---|
| 一加11 DT | `devicetree/sm8550/kernel_platform/qcom/proprietary/devicetree/oplus/oplus_chg/oplus-chg-22811.dtsi`（`&battery_charger` :248、pps 残留表 :551、BUCK :716、vooc 上限 :472-473） |
| 参考 55W DT | `devicetree/sm8750/.../oplus_chg/oplus-chg-24821.dtsi`:1382-1426（PPS 节点）、:762-780（virtual_cp） |
| 参考 33W DT | `devicetree/sm8650/.../oplus_chg/oplus-chg-22825-pps.dtsi`:24-48 |
| v1 PPS 引擎 | `devicetree/sm8550/vendor/oplus/kernel/charger/oplus_pps.c`（ops 结构见 `oplus_pps.h:717-760`） |
| v1 ops 注册 | `.../charger/voocphy/oplus_cp_intf.c:553-575`、`.../chargepump_ic/oplus_pps_cp.c:569` |
| ADSP 下发 | `.../charger/charger_ic/oplus_battery_sm8550.c:11112`（`oplus_chg_set_pps_config`） |
| v1 编译开关 | `.../charger/v1/Makefile:196`（CONFIG_OPLUS_SM8550_CHARGER 分支） |

---

## 8. 我可能错在哪里（自查）

1. **H1 最可能是错的**：一加11 这一代产品线全线没有 `CHG_PROTOCOL_PPS` 条目，ADSP 固件很可能不实现 PPS。若 P0 证伪，本计划大部分作废。
2. **"ADSP 管 CP" 是推断**：证据只是"DT 无 CP 节点 + VOOC 能跑 100 W"。也可能 CP 在适配器侧（部分 SVOOC 方案把 2:1 放在充电头里），那样 `cp_ratio` 就应该是 1，P2 永远不需要。
3. **ops 适配层可能不止 `pps_pdo_select`**：v1 引擎可能在多处检查其他 ops 指针是否为空，需要读完整调用链后再定最小集。
4. **`pps_support_third` 的 NV 闸可能绕过不了**：`third_pps_supported_from_nvid()` 是 NV 项，纯改 DT 可能无效。

---

## 9. 复查记录（v2，逐条源码证据）

| 编号 | 结论 | 证据 |
|---|---|---|
| E1 | v1 的 PPS 请求**只能**经 `ops->pps_pdo_select()` 发出 | `oplus_pps.c:4402` `ret = chip->ops->pps_pdo_select(chip->ask_charger_volt, chip->ask_charger_current);` |
| E2 | ops 只由 CP / MCU 设备注册 | `voocphy/oplus_cp_intf.c:553-575` `oplus_cp_device_register()` → `oplus_pps_ops_register(cp_dev->dev_name, &oplus_cp_pps_ops)`；注册者：`oplus_sc8547.c:2070`、`oplus_sc8547_slave.c:1119`、`oplus_sc6607_charger.c:7231`、`v1/ufcs/ufcs_ic/oplus_sc8547a.c:2529`、`vooc_ic/oplus_op10.c:1151`、`chargepump_ic/oplus_pps_cp.c:569` |
| E3 | HL7227 **不注册** v1 的 PPS ops | `charger_ic/oplus_hl7227.c:694` 仅 `{ .compatible = "oplus,hl7227-cp" }`，全树无 `oplus_pps_ops_register` 调用 |
| E4 | 一加11 DT **没有任何** CP 节点 | 全 `devicetree/sm8550/kernel_platform` 检索 sc8547/sc8517/max77939/hl7227，命中仅 22003、22851、22851-EVB、23061、23801、23801-v2、23861、23871 |
| E5 | 第三方 PPS 的闸门**在 DT 里**，不是安全 NV | `v1/oplus_region_check.c:34` `[PPS_SUPPORT_REGION] = "oplus,pps_region_list"`；`:81-110` 从 battery_charger 的 of_node 解析；`:173-177` 若列表仅一个 `0xFFFF` 则**全 region 放行**；`:10-12` `dbg_nvid` 可作为 module_param 覆盖 |
| E6 | **全仓库没有任何设备设置** `oplus,pps_region_list` | 对 sm8550/sm8650/sm8750 三棵 DT 树 grep `pps_region_list` 均为空 → 默认 elem_count=0 → `find_id_in_region_list` 返回 false → 第三方 PPS 默认全关 |
| E7 | PPS 启动要求 ADSP 报 `PD_PPS_ACTIVE` | `oplus_charger.c:13656+` `oplus_chg_pd_stage2()`：`chg_ops->oplus_chg_get_pd_type() == PD_PPS_ACTIVE`；该值来自 `oplus_battery_sm8550.c:11006-11016` 读 ADSP 属性 `USB_GET_PPS_TYPE`（`.h:285`） |
| E8 | 第三方分支还要过两个开关 | `oplus_pps_check_third_pps_support()`（`oplus_pps.c:5121-5130` = `chip->pps_support_third`）与 `third_pps_priority_than_svooc()`（`oplus_region_check.c:191`） |
| E9 | 缺 ops 时引擎只重试 100 次 | `oplus_pps.c:4880-4892` `retry = 100` × 100 ms 后放弃 |
| E10 | weak 函数可被自建 ops 直接复用 | `oplus_pps.c:131-143` 定义 weak 的 `oplus_chg_set_pps_config` / `oplus_chg_get_pps_status` / `oplus_chg_pps_get_max_cur`；强定义在 `oplus_battery_sm8550.c:11112` |
| E11 | v2 栈**没有** `pps_support_third` 这个闸 | `v2/oplus_chg_pps.c` 全文件 grep 无 `pps_support_third` / `third_pps_supported` → 一加12/13 的 PPS 准入走的是 `oplus,cpa` 的 `protocol_list`，与 v1 机制不可类比 |
| E12 | `pps_support_type` 必须非 0，否则整条 PPS 关闭 | `oplus_pps.c:499-504`；22811 当前 **无此属性** |

### 复查后对方案的修订

1. **P1 必须同时加两项 DT 属性**：`oplus,pps_support_type`（非 0）与 `oplus,pps_region_list = <0xFFFF>`（解开第三方闸）。
2. **P1 必须提供 ops 适配层**（B2），且最小集可缩到 `pps_pdo_select` + `get_pps_status` 两项——因为弱符号已有强定义可调。
3. **一加12 的经验不能照搬**：它的 55W/33W 准入走 v2 的 cpa 协议表，一加11 走 v1 的 region/support_type 双闸。
4. **剩余最大未知仍是 ADSP**：属性 ID 在驱动里存在（`oplus_battery_sm8550.h:285-301` 有 `USB_SET_PPS_VOLT`/`USB_SET_PPS_CURR`/`USB_PPS_*` 一整组），但"驱动有 ID"不等于"固件实现了该 QMI 属性"。E7 的 `USB_GET_PPS_TYPE` 是唯一能直接证伪 H1 的观测点。


---

## 10. 关键新证据：SM8550 平台从未发布过 PPS 机型

对 sm8550 的整棵 DT 树检索：

- `compatible = "oplus,pps_charge"` **只出现在**未被任何 overlay 引用的模板 `oplus-chg-bh-pps-33w.dtsi:18`；
- 全树 `CHG_PROTOCOL_PPS` 只有宏定义（`oplus_chg.h:398`、`oplus_chg_8550.h:70`、`oplus_chg_7550.h:70`），**没有任何机型的 `protocol_list` 使用它**；
- 23061 / 23801 / 23801-v2 的 3000 mA 属于 UFCS 节点（`oplus_ufcs_charge`），不是 PPS。

**推论**：OPlus 从未在 SM8550 平台发布 PPS 机型。因此"照抄同平台先例"这条路不存在——一加11 要 PPS，等于把一加12/13 那次 OTA 的软件改动**手工复刻到 v1 机型**上。

## 11. 若固件判定失败时的低风险退路

在 `&battery_charger` 把 `qcom,pd_input_current_charger_ma` 从 `<2000>`（22811:257）提到 `<3000>`：
- 固定 PD 档 9 V × 3 A = **27 W**（3 A 在 USB-C 免 e-marker 范围内）；
- 纯 DT 参数，不碰协议栈、不碰 CP；
- 可作为 PPS 路线失败后的兜底收益。


---

## 12. P0 实测结果（2026-11-02，一加11 真机）

命令：
```bash
adb shell su -c 'dmesg -w | grep -E "oplus_sm8150_get_pd_type|oplus_get_pps_type|pps|PD_PPS"'
adb shell su -c 'cat /sys/class/power_supply/usb/usb_type'
adb shell su -c 'cat /sys/class/power_supply/usb/voltage_max /sys/class/power_supply/usb/current_max'
```

结果：

| 项 | 值 | 含义 |
|---|---|---|
| `pst->prop[prop_id]` | **8** | `POWER_SUPPLY_USB_TYPE_PD_PPS` —— **ADSP 解析出 PPS APDO** |
| `usb_type` | **PD_PPS** | 同源属性，一致 |
| `voltage_max` | 9000000 µV（9 V） | 适配器/PDO 的电压上限 |
| `current_max` | 5000000 µA（5 A） | 电流上限，达到 5 A 档 |
| `oplus_get_pps_type` 打印 | **未出现** | 正常：`oplus_sm8150_get_pd_type()` 在 `oplus_pps_get_chg_status() == PPS_NOT_SUPPORT` 时短路，不调用它 |

**判定：H1 成立——ADSP 固件支持 PPS。** `oplus_sm8150_get_pd_type()` 因 PPS 引擎未初始化（DT 无节点 → `pps_support_type=0` → `PPS_NOT_SUPPORT`）而降级返回 `PD_ACTIVE`，充电仍停在固定 PD 档，实际被 `qcom,pd_input_current_charger_ma = <2000>` 限在 9 V×2 A = 18 W。

**仍未证明**：ADSP 是否接受 `USB_SET_PPS_VOLT` / `USB_SET_PPS_CURR` 写入（检测 ≠ 可请求）。下一步用写探针验证（见 §13）。

## 13. P0.5 写探针（约 15 行，先于完整 P1）

在 `charger_ic/oplus_battery_sm8550.c` 的 debugfs 目录（已存在：`battery_chg_add_debugfs()` :4237-4252，创建 `/sys/kernel/debug/battery_charger/`）加一个可写文件 `pps_req`：

```c
static ssize_t pps_req_write(struct file *f, const char __user *buf,
			     size_t len, loff_t *ppos)
{
	int vbus, ibus;
	char kbuf[32] = {0};
	if (len >= sizeof(kbuf)) return -EINVAL;
	if (copy_from_user(kbuf, buf, len)) return -EFAULT;
	if (sscanf(kbuf, "%d %d", &vbus, &ibus) != 2) return -EINVAL;
	chg_err("pps_req probe: %d mV %d mA, rc=%d\n", vbus, ibus,
		oplus_chg_set_pps_config(vbus, ibus));
	return len;
}
```

验证：
```bash
echo "11000 5000" > /sys/kernel/debug/battery_charger/pps_req
cat /sys/class/power_supply/usb/voltage_now /sys/class/power_supply/usb/current_now
```

- `rc=0` 且 VBUS 向 11 V 移动 → ADSP 接受 PPS 请求，P1 可全面推进；
- `rc<0` 或 VBUS 不动 → 固件不实现该写属性，到此为止。


### 13.1 入口改版（2026-11-02 实测后修正）

一加11 真机上 **debugfs 不可用**：`/sys/kernel/debug` 未挂载，`mount -t debugfs none /sys/kernel/debug` 返回 `Operation not permitted`（SELinux 拒绝）。原 debugfs 探针方案作废，改用**始终存在的 class attribute**：

- 驱动注册的 class 名是 `"qcom-battery"`（`oplus_battery_sm8550.c:13869`），属性挂在 `/sys/class/qcom-battery/` 下（同目录已有 `soh`、`ship_mode_en`、`restrict_cur` 等）。
- 新增写属性 `pps_req`：

```c
int oplus_chg_set_pps_config(int vbus_mv, int ibus_ma);

static ssize_t pps_req_store(struct class *c, struct class_attribute *attr,
			     const char *buf, size_t count)
{
	int vbus_mv, ibus_ma, rc;
	if (sscanf(buf, "%d %d", &vbus_mv, &ibus_ma) != 2) return -EINVAL;
	rc = oplus_chg_set_pps_config(vbus_mv, ibus_ma);
	chg_err("pps_req: request %dmV %dmA, rc=%d\n", vbus_mv, ibus_ma, rc);
	return count;
}
static CLASS_ATTR_WO(pps_req);
```

用法：`echo "11000 5000" > /sys/class/qcom-battery/pps_req`

### 13.2 刷机口径（本轮踩到的坑）

- 充电驱动是模块：`CONFIG_OPLUS_CHG=m`（`kalama_GKI.config:622`），产物 `oplus_chg.ko` 位于 **vendor_dlkm**，真机实测路径 `/vendor_dlkm/lib/modules/oplus_chg.ko`（7357432 B）。**只刷 boot.img 不会更新它**。
- DT 改动（`oplus-chg-22811.dtsi`）产物在 **dtbo**。
- 因此验证需同时更新：`vendor_dlkm`（模块）+ `dtbo`（DT），`boot` 视内核本身是否改动。
- 实测确认：首轮测试时设备上的 `oplus_chg.ko` 时间戳为 2026-04-08 16:18（stock），即改动未上机。


### 13.3 P0.5 结果：27 W 已实现（2026-11-02）

做法：不走构建，直接改设备 dtbo 分区里的 `qcom,pd_input_current_charger_ma`（2000 → 3000 mA），只影响本机 msm-id 匹配的 3 个 overlay（02/04/08），其余 6 个一字未动。

- 工具：`tools/dtbo/dtbo_tool.py`（extract/repack，FDT header v0.4 大端）、`tools/dtbo/fdt_setprop.py`（二进制就地改 u32）、`tools/dtbo/mkdtboimg.py`（AOSP 版）。
- 结果：`/home/aibeto/dtbo_new.img`（3001485 B）与 stock 逐字节仅差 6 字节（3 blob × 2 字节，0x7d0→0xbb8），header 与 9 条 entry 表完全一致。
- 真机：刷入后**实测 27 W PD 达成**。

**意义**：DT 属性 → 驱动 → ADSP 这条链路打通，且证明 18 W 的瓶颈确实来自该属性。P1 的 DT 改动因此有据可依。

### 13.4 P1 的方法约束（新增节点 ≠ 改 4 字节）

P0.5 能纯二进制补丁，是因为只改了一个已有 u32。P1 要**新增** `oplus,pps_support_type`、`oplus,pps_region_list`、PPS 策略等节点/属性，二进制补丁不可行，必须重编 DTS。但实测 `dtc` 反编真实 overlay 再重编会掉 148 字节（438442→438294），有损。

因此 P1 落地前必须先解决编译链。两条候选：
1. 用 `dtc -@` 重编（怀疑掉的是 `__symbols__`，待验证）；
2. 从源码编译：`cpp -nostdinc -I <dts 目录> -I include … salami-22811-kalama-overlay.dts | dtc -@ -O dtb`，与 stock blob 对比验证一致后再改源码。


### 13.5 源文件对不上（2026-11-02 发现）

用 `cpp -nostdinc -D__DTS__ -I <devicetree> -I <kernel include>` + `dtc -@` 能编译出 `oplus/salami-22811-kalama-overlay.dts`，结果 **113402 B / 371 个 fragment**，反编 5299 行。

而设备上真正生效的那三个 blob（02/04/08）是 **448555–459386 B / 783 个 fragment / 16090 行**，是编译结果的两倍多。

结论：**stock dtbo 里 02/04/08 不是由 `salami-22811-kalama-overlay.dts` 直接编出来的**。P1 要加 PPS 节点，必须先把「哪个 .dts 生成了这三个 blob」对上，否则改的是错的地方。

可能原因（待验证）：
- 那三个 blob 由带额外 include 的变体 dts 生成（如 `-T0` / `-v2` / `-DVT`）；
- 或 stock 编 DT 时用了不同的合并方式（多 dts 合并 / 不同 dtc 参数）。

下一步：跑通完整的 `make dtbs`，产出整套 dtbo，再按内容（fragment 数、节点指纹）与 stock 逐一比对，定位源文件。


---

## 14. 交接状态（2026-11-02 17:40）

> 详细交接见 **PPS_HANDOFF.md**（同目录）。以下是本计划文档的落点。

### 14.1 已达成
- **27 W 已真机验证**：仅改 dtbo 中 qcom,pd_input_current_charger_ma 2000→3000（用户原话"实测27w PD已实现"）。
- ADSP 支持 PPS 已证实：dmesg 中 oplus_sm8150_get_pd_type, pst->prop[prop_id]=8，usb_type=PD_PPS。
- PPS 引擎在 stock oplus_chg.ko 内：strings ... | grep -c pps_pdo_select = 1。
- dtbo 工具链自建完成（extract/repack/就地改 u32），合成镜像往返字节一致。

### 14.2 已写入但未验证（未提交）
1. devicetree/sm8550/.../oplus-chg-22811.dtsi：&battery_charger 内加 22 行 PPS 属性（pps_support_type=<1>、pps_region_list=<0xFFFF>、pps_strategy_normal_current=<5000> 等）+ pd_input_current_charger_ma 改 3000。
2. devicetree/sm8550/vendor/oplus/kernel/charger/charger_ic/oplus_battery_sm8550.c：加 18 行 class attribute 探针，入口 /sys/class/qcom-battery/pps_req。

### 14.3 阻塞
**源码编出的 dtbo 与设备 stock blob 对不上**：编译 113402 B / 371 fragment，stock 02 号 458901 B / 783 fragment。
在查清原因前，**不要刷源码构建的 dtbo**。

### 14.4 下一步（二选一）
- **A（快）**：把 fdt_setprop.py 扩展成支持"插入属性"，直接在 stock blob 的 battery_charger 节点插入 oplus,pps_support_type 等标量，绕开构建。依据 §5.2 的推断，标量可能就够（ops 已由 oplus_pps_cp_init() 无条件注册）。
- **B（稳）**：对齐源文件与 stock blob（对比 fragment 目标列表找缺失 include），再走 make dtbs + dtbo_tool.py repack。

### 14.5 真 55 W 还差一步
oplus_pps.h:141 的 PPS_3RD_ASK_VOLT_MAX = 10500 需改成 11000（第三方头当前最高只能到 10.5 V，5 A 时 52.5 W），改后需重编 oplus_chg.ko 并刷 vendor_dlkm。

### 14.6 约束
- 刷写 / adb / 构建**只能用户本人执行**（用户明令）；助手可改文件。
- 所有改动**不提交**（用户明令）。
- BL 已解锁、AVB 已关闭；回滚镜像 /home/aibeto/dtbo_stock.img。
