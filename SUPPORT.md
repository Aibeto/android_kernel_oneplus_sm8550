# 支持说明：一加11 第三方 PPS 55 W 适配

这是一加11的 msm-kernel 树，在上游基础上为其添加 27W PD 和 55W PPS 支持。分支为 `feat/16.0/oneplus_11/pps`

## 一、修改

### 根仓库

| 路径                        | 作用                                                                                                                                                                             |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tools/dtbo/`               | dtbo 二进制的离线改包工具。`dtbo_tool.py` 拆包和重打包 image ，`fdt_setprop.py` 按节点路径改标量属性，`fdt_splice.py` 增改属性并插入独立编译的子树，`mkdtboimg.py` 生成 image 头 |
| `tools/pps-dtbo/`           | PPS 的 DT 载荷与重建脚本。`payload/pps_props.txt` 与 `payload/pps_subtree.dts` 是唯一数据源，`build.sh` 构建 image                                                               |
| `tools/pps-build/`          | 编译 `oplus_chg.ko`、重新构建 `vendor_dlkm` 分区镜像。三个脚本分别负责目录、编译、打包                                                                                           |
| `PPS_HANDOFF.md`            | 交接文档：现状、关键代码事实、阻塞点、下一步                                                                                                                                     |
| `PPS_55W_MIGRATION_PLAN.md` | 最初的迁移方案                                                                                                                                                                   |
| `PPS_RUNTIME_AUDIT.md`      | 运行期审计                                                                                                                                                                       |

### submodule devicetree/sm8550

| 文件                                                                               | 改了什么                                                                                                                     |
| ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `vendor/oplus/kernel/charger/oplus_pps.c`                                          | main                                                                                                                         |
| `vendor/oplus/kernel/charger/oplus_pps.h`                                          | `PPS_3RD_ASK_VOLT_MAX` 从 10500 改成 11000                                                                                   |
| `vendor/oplus/kernel/charger/oplus_charger.c`                                      | 新增 `oplus_chg_set_fcc_current_ma()`，转调 `chg_ops->charging_current_write_fast`                                           |
| `vendor/oplus/kernel/charger/charger_ic/oplus_battery_sm8550.c`                    | 新增 `oplus_chg_get_adsp_usb_current()`、`oplus_chg_set_batt_chg_limit_ma()`，以及调试用的 `/sys/class/qcom-battery/pps_req` |
| `vendor/oplus/kernel/charger/v1/oplus_region_check.c`                              | region 表改成无条件解析                                                                                                      |
| `kernel_platform/qcom/proprietary/devicetree/oplus/oplus_chg/oplus-chg-22811.dtsi` | 打开第三方 PPS，补齐 11 V / 5 A 策略属性；`qcom,pd_input_current_charger_ma` 2000 -> 3000                                    |

`oplus_pps.c` 有7个区域修改：

1. 文件头添加3个 extern：ADSP USB 电流、FCC 写值、电池限流写值。
2. `oplus_pps_get_charging_data()` 开头不再对第三方适配器执行 `oplus_chg_disable_charge()` 加 `oplus_chg_suspend_charger()`。这一套是给 CP 拓扑用的，无 CP 机型上照做等于什么都不充，改成保持主充电器使能，再用 `oplus_chg_set_input_current_without_aicl()` 设定输入电流。
3. 同一个函数里运行 FCC 阶梯。从 5000 mA 起，被 ADSP 拒绝就在下一轮降 1000 ，下限 3000。电池温度超过 40 ℃ 压到 3500，超过 43 ℃ 压到 2500。探到能用的值以后每秒重写一次，因为框架会抢夺重写成 1100 mA 。
4. 补 IBUS。`cp_master_ibus`、`cp_slave_ibus`、`cp_slave_b_ibus` 都是 0 时，先读取 ADSP 的 usb psy `CURRENT_NOW`；读不到就按电池侧估算 `ibus = ibat * vpack / vbus / 0.9`。缺少这段第三方环路会把 VBUS 逐步增加到上限，然后被 `oplus_pps_check_disconnect()` 判断为输出断开，大约 2 秒后断流。
5. 补 VBUS。`ap_input_volt` 为 0 时用 ADSP 读到的电压回填。原来 `oplus_pps_ready_force2svooc_check_work()` 每 320 ms 查一次 (4000, 6000) 区间，条件永远不成立，于是一直重排。
6. `oplus_pps_get_target_current()`：无 CP 时，曲线 + BCC + CP 计算出来的 IBUS 目标只有 700 mA 。第三方加 MCU 模式下直接用 DT 里的 `oplus,pps_strategy_normal_current`（5000）当目标，电池温度高时降到 4000 / 3000。
7. `oplus_pps_check_disconnect()` 在第三方且无 CP 时直接返回， 0 mA 不再断流。另外 `oplus_pps_action_status_start()` 和 `oplus_3rd_pps_action_volt_change()` 在 MCU 模式下用 `vbat0 / 2` 之前先判断 `vbat0 > 0`（ADSP 的 vbat0 恒为 0，会落回电量计电压）；`oplus_pps_ready_force2svooc_check_work()` 遇到第三方适配器直接清零重试计数退出，不再等待 SVOOC。

DT 关键属性：`oplus,pps_support_type = <1>`（1 是 MCU，只有这个值不校验 CP 主从机），`oplus,pps_support_third = <1>`，`oplus,pps_region_list = <0xFFFF>`（单元素表示全放行），曲线用 `oplus,pps_charge_strategy_soc` 和 `oplus,pps_charge_strategy_temp` 各 7 个分界值，配 6 × 6 个五元组档位。

### 新增的内核符号

都不是 `EXPORT_SYMBOL`，只在 `oplus_chg.ko` 内部互相调用：

- `oplus_chg_get_adsp_usb_current()` 读 usb psy 的 `CURRENT_NOW`，返回 mA。
- `oplus_chg_set_fcc_current_ma(int ma)` 写 `CHARGE_CONTROL_LIMIT`。
- `oplus_chg_set_batt_chg_limit_ma(int ma)` 写 `BATT_CHG_CTRL_LIM`。这是 ADSP 跟随的电池限流，前一条即使 ACK 了也经常被覆盖。
- `/sys/class/qcom-battery/pps_req`，只写，格式是 `vbus_mv ibus_ma`。把 PPS 请求下发给 ADSP，用来验证通路是否通。做这一步是因为 debugfs 被 SELinux 挡了。

## 二、怎么构建

需要更新：整个 dtbo 分区和 vendor_dlkm 分区的 `oplus_chg.ko`

环境条件：clang 工具链在 `.toolchain/clang-r450784e`，`pahole` 要在 PATH 里，`e2fsprogs`、`dtc` 需要装好。16 GB 内存设备并行度推荐 6。

### 1. 模块与 vendor_dlkm 镜像

```bash
bash tools/pps-build/build-oplus-chg.sh            # 全流程，首次约 2 分钟
bash tools/pps-build/build-oplus-chg.sh --modules  # 改完代码只重编模块，约 11 秒
```

产物在 `artifacts/build/out/oplus_chg.ko`。再打成能刷的分区镜像：

```bash
bash tools/pps-build/repack-vendor_dlkm.sh \
    --ko    artifacts/build/out/oplus_chg.ko \
    --stock /path/to/vendor_dlkm_stock.img \
    --out   artifacts/build/out/vendor_dlkm_pps.img \
    --fs stock
```

`--fs stock` 保留出厂的文件系统属性（`shared_blocks` 特性位、`0644 root:root`、`u:object_r:vendor_file:s0`），产物与原厂镜像只差一个文件。脚本最后会跑 `e2fsck -fn`；再加上 `--verify-dir <解开后的 stock 目录>`，会把 392 个文件逐个 `cmp`，只允许 `oplus_chg.ko` 不同。

不能直接 push 到设备 `/vendor_dlkm` 。

### 2. dtbo

```bash
bash tools/pps-dtbo/build.sh 1   # 只插标量和字符串属性
bash tools/pps-dtbo/build.sh 2   # 标量加曲线子树，当前在用
```

产物在 `artifacts/dtbo/out/stage2_scalars_curve/dtbo.img`。

源码树编出来的 dtbo 用不了。cpp 加 dtc 只有 371 个 fragment、114 KB，而出厂 blob 是 783 个 fragment、438 到 459 KB，是 OPPO 把 oplus、audio、display、camera 四个 overlay 合并后的结果。所以改法是在出厂 blob 上原地动刀：只碰匹配本机的 02/04/08 三个 entry，另外 6 个 entry 逐字节不变，脚本里有 `cmp` 校验。

插入点也不能按 fragment 索引写。`&battery_charger` 在 entry 02/04 里位于 `/fragment@47/__overlay__`，在 entry 08 里位于 `/fragment@42/__overlay__`（08 少了 7 个根 fragment）。按编号写会静默落到 `target = <&soc>` 的 overlay 上。脚本用 `--anchor-prop qcom,pd_input_current_charger_ma` 定位，并断言唯一命中。

出厂镜像路径可以用 `STOCK_IMG=` 覆盖，脚本只读它

### 3. AK3 整包（可选，和 PPS 无关）

```bash
bash scripts/build-gki.sh        # 出 out/arch/arm64/boot/Image
bash scripts/pack_anykernel3.sh  # 出 artifacts/AnyKernel3_stock_sm8550_<tag>.zip
```

`build-gki.sh` 编的是 `common/` 子模块的 GKI Image（gki_defconfig + ThinLTO），`pack_anykernel3.sh` 把 Image 和 `scripts/anykernel.sh` 拼进 `third_party/AnyKernel3` 。这次的改动用不到这两条，只有要重刷 boot 分区时才走。

### 4. 刷写

dtbo 在 bootloader 模式刷，vendor_dlkm 在 fastboot 模式刷：

```bash
adb reboot bootloader
fastboot flash dtbo        artifacts/dtbo/out/stage2_scalars_curve/dtbo.img
fastboot reboot fastboot
fastboot flash vendor_dlkm artifacts/build/out/vendor_dlkm_pps.img
fastboot reboot
```

## 三、已知限制

电流比预期低。FCC 阶梯停在 4000 mA，再增加会被 ADSP 静默拒绝（glink 超时，`rc=-110`）。三种可能还没区分开：ADSP 按 SOC 降流、4 A 附近存在本地上限、框架认为没进快充（日志里 `chg_type=0x0`、`fastchg_start:0`，所以不走 `pd_temp_*` 那几张表）。

`PPS_3RD_ASK_VOLT_MAX` 现在是 11000。第三方 PPS 的请求电压被这个常量 clamp，再调高也没有意义，ADSP 的 PDO 本来就到 11 V。

`oplus_chg_set_pps_config()` 返回 0 只代表 glink ACK。ADSP 到底执行了没有，要看 VBUS 的实际变化，`handle_message` 把 SET 响应丢掉了。

## 四、文档索引

> 下方文档存在大量 AI 编写且可能未经人工审查

- [PPS_HANDOFF.md](PPS_HANDOFF.md)：现状、代码事实、下一步
- [PPS_RUNTIME_AUDIT.md](PPS_RUNTIME_AUDIT.md)：逐条审计，带行号
- [PPS_55W_MIGRATION_PLAN.md](PPS_55W_MIGRATION_PLAN.md)：最初的迁移方案
- [tools/pps-build/BUILD_RECIPE.md](tools/pps-build/BUILD_RECIPE.md)：构建与安装的全部细节
- [tools/pps-dtbo/README.md](tools/pps-dtbo/README.md)：DT 载荷与校验
- [tools/dtbo/README.md](tools/dtbo/README.md)：dtbo 改包工具
