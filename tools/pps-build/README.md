# tools/pps-build — 只替换 oplus_chg.ko 的 vendor_dlkm 镜像

一加11 的 PPS 引擎在 `/vendor_dlkm/lib/modules/oplus_chg.ko` 里。整套流程的目标是
**只换这一个模块**，其余 391 个文件与出厂镜像逐字节相同。

```bash
bash tools/pps-build/build-oplus-chg.sh              # 全流程（首次约 2 分钟）
bash tools/pps-build/build-oplus-chg.sh --modules    # 只重编模块（改代码后用，约 11 秒）

bash tools/pps-build/repack-vendor_dlkm.sh \
    --ko artifacts/build/out/oplus_chg.ko \
    --out artifacts/build/out/vendor_dlkm_pps.img \
    --fs stock
```

## 为什么不能直接在仓库根 make

msm-kernel 的 `vendor` 软链是按 `ws/kernel_platform/msm-kernel` 这套深度写死的
（`kernel/oplus_cpu → ../../../vendor`），仓库根布局下全部悬空，configure 阶段就报
`init/Kconfig:886: can't open file "kernel/oplus_cpu/thermal/Kconfig"`。
`setup-vendor-build-tree.sh` 用 `cp -al` 在 `artifacts/build/kp/` 建一份硬链接镜像，
不改动源码、可重复执行，然后在这个镜像里按 vendor 布局配置和编译。

## 三个技术前提（详见 BUILD_RECIPE.md）

1. **Module.symvers**：用 `work/make-symvers.py` 从 388 个出厂 `.ko` 的 `__versions`
   汇总出 4042 条 CRC（无冲突），所以新模块的 `__versions` 与出厂模块逐条 CRC 相同。
2. **vermagic**：CONFIG_MODVERSIONS 下 `same_magic()` 只比较第一个空格之后的
   `SMP preempt mod_unload modversions aarch64`，`5.15.197` 与设备 `5.15.180` 的
   前缀差异不影响加载。
3. **签名**：`CONFIG_MODULE_SIG_PROTECT=y` 但 `MODULE_SIG_FORCE` 未开，未签名模块
   只是被 taint，不会拒绝加载。

## 镜像

`repack-vendor_dlkm.sh --fs stock` 保持出厂的文件系统属性（`shared_blocks` 特性位、
`0644 root:root`、`u:object_r:vendor_file:s0`）。带 `shared_blocks` 的分区不能在设备上
remount 成 rw（本内核 `EXT4_FEATURE_RO_COMPAT_SUPP` 不含它），所以要走刷镜像而不是 push。

刷写：`dtbo.img` 在 bootloader 模式刷，`vendor_dlkm` 在 fastboot 模式刷，都不带槽后缀
（默认当前槽）。

```bash
adb reboot bootloader
fastboot flash dtbo       artifacts/dtbo/out/stage2_scalars_curve/dtbo.img
fastboot reboot bootloader
fastboot flash vendor_dlkm artifacts/build/out/vendor_dlkm_pps.img
fastboot reboot
```
