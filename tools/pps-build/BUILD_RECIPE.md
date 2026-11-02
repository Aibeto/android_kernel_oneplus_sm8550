# OPLUS `oplus_chg.ko` 重建与安装配方

> 目标：在 `/home/aibeto/code/android_kernel_oneplus_sm8550`
> （分支 `feat/16.0/oneplus_11/pps`，OnePlus 11 / SM8550 / 项目 22811）里，
> 可复现地编出**能加载到零售机 stock 内核**上的 `oplus_chg.ko`，并安全地装进去。
>
> 本文所有结论都在本机实测过（2026-11-02），命令、行号、输出均为真实证据。
> 长构建（`make Image`）**不需要**跑：本配方只编模块，全流程约 2 分钟。

---

## 0. 速览

```bash
cd /home/aibeto/code/android_kernel_oneplus_sm8550

# ① 一次性：建立目录布局镜像（硬链接，8 秒，不改任何源码）
bash artifacts/build/setup-vendor-build-tree.sh

# ② 编译（首次 ~2 分钟；之后改代码只需 20–60 秒增量）
bash artifacts/build/build-oplus-chg.sh
#    -> artifacts/build/out/oplus_chg.ko

# ③ 打包成可 fastboot 刷的 vendor_dlkm 镜像
bash artifacts/build/repack-vendor_dlkm.sh \
  --ko artifacts/build/out/oplus_chg.ko \
  --stock /home/aibeto/下载/vendor_dlkm_a.img \
  --out artifacts/build/out/vendor_dlkm_pps.img
```

三次构建的产物哈希一致（可复现）：

| 产物 | 大小 | sha256 |
|---|---|---|
| `artifacts/build/out/oplus_chg.ko` | 6 945 736 B | `49be3bc071be8fd6c45811e786d5f78c471f1f9f6edcaf800cfa0c0cb1d500ef` |
| `artifacts/build/out/vendor_dlkm_pps.img`（**默认，原参数**） | 172 703 744 B | `19558a8e490d6b7f7feb32aec9c9df83e6a1033251bb246e500f380a2679251a` |
| `artifacts/build/out/vendor_dlkm_pps_rw.img`（可选，可 rw 挂载） | 172 703 744 B | `086bef9f55c9948d850e24fa720a5a72d5c3c42aaf1cad6087981fa853d0e0e8` |

两份镜像的区别只有 ext4 特性位：

```
vendor_dlkm_pps.img     … extra_isize shared_blocks     <- 与 stock 逐字一致，设备只能只读挂载
vendor_dlkm_pps_rw.img  … extra_isize                   <- 去掉去重特性，可在设备上 rw 挂载
```

（`vendor_dlkm_pps.img` 的 md5 是 `a94cd5de3fff6c4e241bc0196bae41e1`。）

**开工前必须先问用户三件事**（见 §6.13）：

1. `adb shell uname -r`（用来确认 vermagic，虽然前缀其实被忽略，见 §3.1）；
2. `adb shell mount | grep vendor_dlkm` + `adb shell ls -l /dev/block/by-name | grep vendor_dlkm`
   （决定装法：push 还是刷镜像，以及刷哪个槽）；
3. 机器是 Magisk / KernelSU 还是纯 root（决定能否用 overlay 装法，§4.4）。

---

## 1. 配置合并（不是猜的，是跑出来的）

### 1.1 障碍：本工作区的目录布局和源码树的期望不一致

`msm-kernel` 树里大量 **git 跟踪的 vendor 软链接**是按 OnePlus 官方三仓布局写的相对路径：

| 链接（git 中的 symlink） | 目标 | 从链接所在目录往上数 |
|---|---|---|
| `kernel/oplus_cpu` | `../../../vendor/oplus/kernel/cpu` | 3 级 |
| `drivers/power/oplus` | `../../../../vendor/oplus/kernel/charger` | 4 级 |

这两条路径只有在 **树根 = `ws/kernel_platform/msm-kernel` 且 vendor = `ws/vendor`** 时才同时成立
（3 级和 4 级都落到 `ws/`）。而本工作区把 `msm-kernel` 直接铺在仓库根，于是
`repo_root/vendor`、`repo_root/oplus`（`scripts/setup-tree.sh` 建的）距离不对，链接全部悬空。

实测后果（在仓库根直接跑）：

```
$ make -C . O=out-vendor ARCH=arm64 gki_defconfig
init/Kconfig:886: can't open file "kernel/oplus_cpu/thermal/Kconfig"
```

`init/Kconfig:886` 是 `source "kernel/oplus_cpu/thermal/Kconfig"`（:889 还有 sched 一条）。
`common/` 树能过（`scripts/setup-tree.sh` 为它单独建了 `common/kernel/oplus_cpu` 链接），
但 `common/drivers/power/Kconfig` 里没有 `OPLUS_CHG` —— 所以 **common 树选不上这个模块**，
必须是 msm-kernel 根树。

### 1.2 解决：`artifacts/build/kp/` 硬链接镜像（不改源码、不占空间）

`artifacts/build/setup-vendor-build-tree.sh` 用 `cp -al` 在 `artifacts/build/kp/` 下重建官方布局：

```
artifacts/build/kp/                       <= 相当于官方布局的 ws/
  vendor -> <repo>/devicetree/sm8550/vendor
  oplus  -> <repo>/devicetree/sm8550/kernel_platform/oplus
  kernel_platform/msm-kernel/             <= 仓库根的硬链接镜像（目录是新的，文件是同一 inode）
```

实测：**8.2 秒**完成；文件与仓库是同一 inode（`init/Kconfig` 两边都是 `16876061`），
所以几乎不占额外磁盘（只有目录项 + `out-vendor` 的 146 MB）。
镜像里 `vendor`/`oplus` 两个条目指向 `../../vendor`、`../../oplus`，正好落到 `kp/`。

> ⚠️ 因为文件是硬链接，**不要在镜像里编辑源码**（会改到仓库本体）。
> 所有编译输出都走 `O=artifacts/build/kp/out-vendor`，源码树只读。

### 1.3 生成 `.config`（实测命令 + 证据）

`FARM=artifacts/build/kp/kernel_platform/msm-kernel`，`OUT=artifacts/build/kp/out-vendor`。

```bash
export CLANG_DIR=$PWD/.toolchain/clang-r450784e/bin PATH=$PWD/.toolchain/clang-r450784e/bin:$PATH
MA="ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
    CC=$CLANG_DIR/clang HOSTCC=$CLANG_DIR/clang HOSTLD=ld.lld PAHOLE=/usr/bin/pahole"

make -s -C $FARM O=$OUT $MA gki_defconfig                        # 5.1 s，207 220 B
ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  $FARM/scripts/kconfig/merge_config.sh -m -O $OUT $OUT/.config \
  $FARM/arch/arm64/configs/vendor/kalama_GKI.config              # 221 235 B
$FARM/scripts/config --file $OUT/.config \
  -e LTO_CLANG -e LTO_CLANG_FULL -d LTO_CLANG_THIN -d LTO_CLANG_NONE
$FARM/scripts/config --file $OUT/.config -d LOCALVERSION_AUTO --set-str LOCALVERSION ""
make -s -C $FARM O=$OUT $MA olddefconfig                         # 3.4 s
make -s -C $FARM O=$OUT $MA syncconfig                           # 必须！见 §2.5 的坑
```

`merge_config.sh` 的 `-m` = 只合并不跑 make；`-O` 指向 `O=` 目录。
用到的工具链就是 `scripts/build-gki.sh` 那套：`clang 14.0.7 (r450784e)` + `ld.lld`，
`PAHOLE=/usr/bin/pahole`，`LLVM=1 LLVM_IAS=1`。

**证据（`$OUT/.config`，行号即文件行）**

```
:3505  CONFIG_OPLUS_CHG=m                    :849  CONFIG_MODVERSIONS=y
:3506  CONFIG_OPLUS_SM8550_CHARGER=y         :852  CONFIG_MODULE_SIG=y
:3517  CONFIG_OPLUS_CHG_DYNAMIC_CONFIG=y     :854  CONFIG_MODULE_SIG_PROTECT=y
:3519  CONFIG_OPLUS_CHG_DRM_PANEL_NOTIFY=y   :853  # CONFIG_MODULE_SIG_FORCE is not set
:3522  CONFIG_OPLUS_CHG_V2=m                 :855  # CONFIG_MODULE_SIG_ALL is not set
:108   CONFIG_PREEMPT=y                      :846  # CONFIG_MODULE_FORCE_LOAD is not set
:340   CONFIG_SMP=y                          :33   CONFIG_LOCALVERSION=""
:847   CONFIG_MODULE_UNLOAD=y                :34   # CONFIG_LOCALVERSION_AUTO is not set
:786   CONFIG_LTO_CLANG_FULL=y               :789  CONFIG_CFI_CLANG=y
```

### 1.4 配置保真度（和 stock 对不对得上）

* **vendor 片段只有一个**：`arch/arm64/configs/vendor/kalama_GKI.config`
  （`CONFIG_OPLUS_CHG=m` 在 :622，`CONFIG_OPLUS_CHG_V2=m` 在 :633）。
  同目录还有 `kalama_consolidate.config`，但那是 consolidate 调试配置
  （含 `CONFIG_LOCALVERSION="-gki-consolidate"`、`CONFIG_LKDTM=m`），零售机不用。
* **LTO 模式必须 FULL**：从 stock `boot_a.img` 抽出的内核配置里是
  `CONFIG_LTO_CLANG_FULL=y` / `CONFIG_CFI_CLANG=y`，`gki_defconfig` 默认也是 FULL。
  `scripts/build-gki.sh:73` 把 LTO 改成 THIN 是为了在 15 GB 内存上链接整个 Image，
  **和只编模块是两件事**；模块走 FULL 不需要链接 vmlinux，实测 59 秒、内存无压力。
* **符号级比对**（我们的产物 vs stock `vendor_dlkm` 里的 `oplus_chg.ko`）：

  | 指标 | stock | 本次重建 |
  |---|---|---|
  | 全局已定义符号 | 2434 | 2435（多 `oplus_chg_get_adsp_usb_current`） |
  | 未定义符号 | 312 | 313（多 `schedule_timeout`，由上面那个函数用 `wait_event_interruptible_timeout` 引入） |
  | 我们缺 stock 的符号 | — | **0 个** |

  即：**没有功能被配置掉**；差异仅来自「源码版本不同」（设备 stock 是 5.15.180
  `g1ad4d96fefe7` 时代的构建，本仓库是 5.15.197）——顺便说明 THIN 编出来的符号集
  差很多（5717 vs 2434），那是 LTO 模式不对的征兆。

---

## 2. 模块编译

### 2.1 命令（`artifacts/build/build-oplus-chg.sh` 的实质）

```bash
FARM=$PWD/artifacts/build/kp/kernel_platform/msm-kernel
OUT=$PWD/artifacts/build/kp/out-vendor
MODDIR=vendor/oplus/kernel/charger/v1

cp artifacts/build/work/Module.symvers.vendor $OUT/Module.symvers   # ★ 库存 CRC 表（§3.3）

make -s -C $FARM O=$OUT $MA -j6 modules_prepare                      # 26 s（首次）
make -C $FARM O=$OUT M=$MODDIR $MA -j6 modules                       # 43–59 s（首次）
```

产物落地：`$OUT/vendor/oplus/kernel/charger/v1/oplus_chg.ko`
（未去调试信息时 **40.5–44.6 MB**），归档前进 `llvm-strip -g`：

```bash
$PWD/.toolchain/clang-r450784e/bin/llvm-strip -g \
  -o artifacts/build/out/oplus_chg.ko \
  $OUT/vendor/oplus/kernel/charger/v1/oplus_chg.ko     # -> 6 945 736 B
```

`modules_prepare` + `make M=...` **够用**，不需要 `make modules`（全树）、更不需要 `Image`。
`M=` 的目标只用 94 个 `.o` 就够。

### 2.2 为什么 `M=` 必须是**相对 srctree** 的 `vendor/oplus/kernel/charger/v1`

`vendor/oplus/kernel/charger/v1/Makefile:9` 是

```make
subdir-ccflags-y += -I$(srctree)/$(src)
```

`$(src)` 就是你传给 `M=` 的字符串。用绝对路径会让 `-I` 变成 `$srctree//abs/path`，
头文件找不到；所以要在 `make -C $FARM` 的工作目录（= srctree）下用相对路径。
同文件 `:10` 还有 `-Wframe-larger-than=3042`，`make` 时报的
`stack frame size (4208) exceeds limit (3042) in oplus_get_bcc_parameters_from_adsp`
是**原生告警**（stock 构建同样会报），不是我们引入的。

模块入口链：`vendor/oplus/kernel/charger/Makefile:23` `obj-$(CONFIG_OPLUS_CHG) += v1/`
→ `v1/Makefile:363` `obj-$(CONFIG_OPLUS_CHG) := oplus_chg.o`。

### 2.3 耗时 / 资源 / 增量

| 步骤 | 首次 | 增量（改 1 个 .c） | 说明 |
|---|---|---|---|
| `setup-vendor-build-tree.sh` | 8.2 s | 0（幂等） | 只在 `kp/` 不存在时跑 |
| `gki_defconfig` + merge + olddefconfig | ~10 s | 0（`.config` 已存在就跳过） | |
| `modules_prepare` | 26.1 s | ~2 s | 产物落在 `O=`，不进源码树 |
| `make M=... modules` | 43 s（THIN）/ 59 s（FULL） | 20–60 s | 改 `.h` 会重编受影响的所有 `.o` |
| `llvm-strip -g` | <1 s | <1 s | |

**JOBS/内存**：编模块用 `-j6` 足够，峰值内存远低于 15 GB（实测无 swap、无 OOM）。
只有「链接整个 `Image`」才需要 `JOBS<=6` + `THINLTO_JOBS=2` + `LD` 包
`--thinlto-jobs=2`（`scripts/build-gki.sh:51-54` 的 `ld-thin.sh`）——本配方不涉及。

### 2.4 只编改动部分

`make M=...` 是标准 kbuild 增量：改 `v1/charger_ic/oplus_battery_sm8550.c` 只重编它 +
LTO 重链（~20 s）；改 `oplus_pps.h`（`PPS_3RD_ASK_VOLT_MAX` 在 `oplus_pps.h:141`）会
重编所有包含它的 `.o`（~40–60 s）。

### 2.5 坑：改完 `.config` 必须 `syncconfig`；新符号要 `KBUILD_MODPOST_WARN`

* `.config` 改完只跑 `olddefconfig` **不会**刷新 `include/config/auto.conf`，
  `make` 会拿旧配置编（我实测踩过：改了 LTO 模式，`make` 4.8 秒“完成”什么都没重编）。
  改配置后补一条 `make -s -C $FARM O=$OUT $MA syncconfig`。
* 如果新代码引用了**库存 CRC 表里没有的符号**，modpost 会报
  `ERROR: modpost: "xxx" [...] undefined!` 并失败。两个解法：
  1. 补 CRC（首选）：把更多 stock `.ko` 加进 `make-symvers.py` 的输入；
  2. 应急：`make ... KBUILD_MODPOST_WARN=1 modules` —— 实测会降级为
     `WARNING: modpost: ... undefined!` 并产出 `.ko`；该符号不会写进 `__versions`，
     加载时内核只打印一次 `no symbol version for xxx` 然后**放行**（见 §3.3）。

---

## 3. vermagic / CRC / 签名 —— 能不能加载的关键

### 3.1 vermagic 由什么组成，为什么前缀无所谓

`include/linux/vermagic.h`：

```c
#define VERMAGIC_STRING  UTS_RELEASE " " \
    MODULE_VERMAGIC_SMP MODULE_VERMAGIC_PREEMPT \
    MODULE_VERMAGIC_MODULE_UNLOAD MODULE_VERMAGIC_MODVERSIONS \
    MODULE_ARCH_VERMAGIC          /* arch/arm64/include/asm/vermagic.h:7 = "aarch64" */
```

`kernel/module.c:1317-1325`：

```c
/* First part is kernel version, which we ignore if module has crcs. */
static inline int same_magic(const char *amagic, const char *bmagic, bool has_crcs)
{
        if (has_crcs) { amagic += strcspn(amagic, " "); bmagic += strcspn(bmagic, " "); }
        return strcmp(amagic, bmagic) == 0;
}
```

`:3325` 调用它时传的是 `info->index.vers`（有 `__versions` 节就是 true）。我们的模块
带 314 条 `__versions`，所以 **前缀（5.15.180-g… vs 5.15.197）完全不参与比较**，
只有 `SMP preempt mod_unload modversions aarch64` 要一致：

```
新模块 vermagic: 5.15.197  SMP preempt mod_unload modversions aarch64
stock  vermagic: 5.15.180-g1ad4d96fefe7 SMP preempt mod_unload modversions aarch64
                                 ↑ 从第一个空格开始完全一致
```

> 顺带一提：设备上 stock `oplus_chg.ko` 的前缀（5.15.180-g1ad4d96fefe7）和内核 Image
> 自己的 `uname -r`（`5.15.180-android13-8-o-01178-gfc2079499f31`）本来就不同 ——
> 这本身就是“前缀不重要”的现场证据。

### 3.2 `LOCALVERSION` / `LOCALVERSION_AUTO` 在本树的表现（实测）

`UTS_RELEASE = $(VERSION).$(PATCHLEVEL).$(SUBLEVEL)$(CONFIG_LOCALVERSION)$(scm 部分)`，
`KERNELRELEASE` 由 `Makefile:1416-1422` 调 `scripts/setlocalversion` 生成。
本树 `Makefile:3-5` 是 `5.15.197`。实测：

| 设置 | `make kernelrelease` |
|---|---|
| 默认（`LOCALVERSION_AUTO=y`） | `5.15.197-g26c49bff7753-dirty`（吃 git 状态，**不可复现**） |
| `AUTO=n`，未传 `LOCALVERSION` | `5.15.197+`（末尾加 `+`） |
| `AUTO=n` + 命令行 `LOCALVERSION=` | `5.15.197` ✅（本配方采用） |
| `AUTO=n` + `LOCALVERSION=-android13-8-o-01178-gfc2079499f31` | `5.15.197-android13-8-…` |

**通用配方（用户贴来 `uname -r` 后怎么让 vermagic 完全等于它）**：
`uname -r` = `基础版本` + `后缀`。设设备是 `5.15.180-android13-8-o-01178-gfc2079499f31`，
就显式覆盖三个版本号和后缀（实测输出正好是目标串）：

```bash
make -C $FARM O=$OUT ... VERSION=5 PATCHLEVEL=15 SUBLEVEL=180 \
     LOCALVERSION=-android13-8-o-01178-gfc2079499f31 kernelrelease
# => 5.15.180-android13-8-o-01178-gfc2079499f31
```

**但这一步是可选的**：§3.1 已证明前缀被忽略。真正必须一致的是
①flag 后缀（由 `CONFIG_SMP/PREEMPT/MODULE_UNLOAD/MODVERSIONS` 决定，已核对一致）、
②**每个符号的 CRC**。所以默认配方只做「`AUTO=n` + `LOCALVERSION=`」保证可复现。

### 3.3 `CONFIG_MODVERSIONS=y`：CRC 从哪来，为什么能对上 stock 内核

内核侧（`kernel/module.c:1246-1292` `check_version`）：

* 内核这个符号没有 CRC → 直接放行；
* 模块没有 `__versions` 节 → `try_to_force_load()`，本内核没开 `CONFIG_MODULE_FORCE_LOAD`
  → `-ENOEXEC`，**拒绝**；
* 模块 `__versions` 里没有这个符号 → `pr_warn_once("%s: no symbol version for %s")` 后**放行**（:1286）；
* 有但 CRC 不同 → `pr_warn("%s: disagrees about version of symbol %s")`（:1290）→ 加载失败。

其中 `module_layout` 是硬门槛（`:1295-1313` `check_modstruct_version`）——它由
`struct module` 布局 + 配置算出，**构建内核时**用 genksyms 生成，等于内核 ABI 指纹。

**做法：用库存内核自己的 CRC 表冒充 `Module.symvers`。**
`CONFIG_MODVERSIONS` 编译出的每个模块，`__versions` 节都记录了它引用的每个导出符号的
CRC（`struct modversion_info { u64 crc; char name[56]; }`，64 B/条），这些 CRC 就是
**库存内核 `__kcrctab` 里的值**。把 stock `vendor_dlkm` 里 386 个 `.ko` 的 `__versions`
汇总，就得到库存内核的符号表：

```bash
# 抽出 stock vendor_dlkm 全部模块（debugfs 可读，镜像 172 703 744 B / ext4）
debugfs -R "rdump /lib/modules artifacts/build/work/vendor_dlkm" /home/aibeto/下载/vendor_dlkm_a.img
# 汇总 CRC 表（脚本在 artifacts/build/work/make-symvers.py）
python3 artifacts/build/work/make-symvers.py \
    artifacts/build/work/Module.symvers.vendor \
    --exclude-ko artifacts/build/work/stock_oplus_chg.ko \
    artifacts/build/work/vendor_dlkm artifacts/build/work/stock_oplus_chg.ko
# => 扫描 388 个含 __versions 的 .ko，汇总 4042 个符号，CRC 冲突 0 处
```

* 输出格式必须是 **5 列**（`crc \t name \t module \t export \t namespace`），
  `scripts/mod/modpost.c:2372-2419` 的 `read_dump()` 少一列就
  `FATAL: modpost: parse error in symbol dump file`（踩过）。
* `--exclude-ko`：把 **模块自己导出的 81 个符号**剔掉，否则 modpost 认为 vmlinux 也导出
  它们，报 `'register_chg_glink_notifier_v1' exported twice`（踩过）。
* 汇总时 **0 处 CRC 冲突** —— 说明这些库存模块来自同一内核，表是自洽的。

**结果核对（对 stock `oplus_chg.ko` 的 313 条逐条比对）**

```
新模块 __versions 条目: 314
缺 stock 条目: 0
多出条目: 0x8ddd8aad  schedule_timeout      （§1.4 那个多出来的源码差异）
```

即 **313/313 条 CRC 完全相同**，包括 `0x0222dd63 module_layout`。

**反向核对（别人依赖我们的符号）**：stock 里有 5 个模块引用了 `oplus_chg.ko` 导出的符号，
我们的重建模块对这 5 个符号导出的 CRC 也必须一致，否则那些模块会拒绝加载：

| 符号 | stock CRC | 本次重建 | 引用方 |
|---|---|---|---|
| `qti_battery_charger_get_prop` | `0x47653d56` | `0x47653d56` ✅ | `leds-qti-flash.ko` |
| `register_chg_glink_notifier_v1` | `0xa8d1ec47` | `0xa8d1ec47` ✅ | `oplus_chg_symbol.ko` |
| `unregister_chg_glink_notifier_v1` | `0xd76b10c1` | `0xd76b10c1` ✅ | `oplus_chg_symbol.ko` |
| `register_hboost_event_notifier` | `0x696c9e5f` | `0x696c9e5f` ✅ | `qcom-hv-haptics.ko`, `swr_haptics_dlkm.ko` |
| `unregister_hboost_event_notifier` | `0x16d662d9` | `0x16d662d9` ✅ | 同上 |

5/5 一致 ⇒ 我们这棵树和 stock 在**这些接口的 struct 布局/原型上没有漂移**，
另外 4 个模块不会因为我们替换 `oplus_chg.ko` 而崩（这是“源码 5.15.197 vs 设备 5.15.180”
这个风险的直接证据）。

### 3.4 签名：`CONFIG_MODULE_SIG_PROTECT=y` 会放行未签名模块

* `kernel/module.c:274-283`：只有 `CONFIG_MODULE_SIG && !CONFIG_MODULE_SIG_PROTECT`
  才有 `sig_enforce`；本内核有 `MODULE_SIG_PROTECT=y` ⇒ `#define sig_enforce false`。
* `:2918-2981` `module_sig_check()`：`-ENODATA/-ENOPKG/-ENOKEY`（未签名/无算法/无密钥）
  非致命；`is_module_sig_enforced()` 为 false 时不走 `-EKEYREJECTED`；
  最后 `#ifdef CONFIG_MODULE_SIG_PROTECT … return 0;` —— **放行**。
* 加载后 `:4099-4105` 只打一条通知并加 taint：

```
oplus_chg: module verification failed: signature and/or required key missing - tainting kernel
```

  所以**不要签名、不要改 key**。反过来，如果我们给模块签了别的 key（`MODULE_SIG_ALL=y`
  会用它自己的 `certs/signing_key.pem`），`mod_verify_sig` 返回 `-EKEYREJECTED`（致命，
  `:2958-2966` 走 `default` 分支）——**签名反而会害了你**。本配方 `MODULE_SIG_ALL` 未设，
  产物不含 `~Module signature appended~`（已核对，和 stock 模块一样都是未签名）。

### 3.5 GKI 保护符号：不会挡我们

* `kernel/module.c:2354-2357`（引用未定义符号时）：
  `if (!mod->sig_ok && gki_is_module_protected_symbol(name))` →
  `pr_err("%s: is not an Android GKI signed module. It can not access protected symbol: %s")` + `-EACCES`。
* `:2279-2283`（导出符号时）`gki_is_module_exported_symbol()` 同理。
* 名单来源 `kernel/gki_module.c`，由 `scripts/gen_gki_modules_headers.sh` 从
  `android/abi_gki_modules_protected`（访问保护）与 `android/abi_gki_modules_exports`（导出保护）生成；
  **本树这两个文件都只有一行 `[abi_symbol_list]`（空表）**。
* 更有力的现场证据：设备上 stock `oplus_chg.ko` **本来就是未签名的**却加载正常 ⇒
  它用的那 313 个符号不在保护名单里；我们的符号集是它的超集（多 1 个
  `schedule_timeout`，也是 vmlinux 常规导出，不在 GKI 保护名单）。

---

## 4. 安装方案（按风险从低到高）

前提：bootloader 已解锁、AVB 已关、有 fastboot、root 可用。
镜像事实：`/home/aibeto/下载/vendor_dlkm_a.img` 是 **ext4**，172 703 744 B，
`/lib/modules/` 下**平铺** 386 个 `.ko` + `modules.dep`(58 855 B) + `modules.load`(9 148 B)，
416 个 inode 只用掉 409 个，空闲块 **127**，且带 **`shared_blocks`**（= Google 去重特性，
多个 inode 共享物理块）。

### 4.1 方案 a：直接 root push 到 `/vendor_dlkm/lib/modules/` —— **当前镜像下不可行**

`shared_blocks` 这一个 bit 就让 RW 挂载被内核拒绝。本机内核源码：

* `fs/ext4/ext4.h:2169-2181` `EXT4_FEATURE_RO_COMPAT_SUPP` **不含** `0x4000`；
* 镜像 `ro_compat = 0x0000407b`（`0x4000` 就是 shared_blocks）；
* `fs/ext4/super.c:3194-3200`：

```c
if (ext4_has_unknown_ext4_ro_compat_features(sb)) {
        ext4_msg(sb, KERN_ERR, "couldn't mount RDWR because of unsupported optional features (%x)", ...);
        return 0;
}
```

⇒ `mount -o remount,rw /vendor_dlkm` 会失败，dmesg 出现

```
EXT4-fs: couldn't mount RDWR because of unsupported optional features (4000)
```

请用户在机器上实测这两条确认（只需要它们，不用刷机）：

```bash
adb shell mount | grep vendor_dlkm
adb shell 'mount -o remount,rw /vendor_dlkm; echo rc=$?; dmesg | tail -3'
```

如果确认不可写，**跳过本方案**；如果用户某次刷过“去掉 shared_blocks”的镜像
（即 `--fs rw` 的 `vendor_dlkm_pps_rw.img`），之后这个方案就可用了——那是后续快速迭代的正路
（默认的 `vendor_dlkm_pps.img` 刻意保持 stock 参数，仍然只能只读挂载）：

```bash
adb push artifacts/build/out/oplus_chg.ko /data/local/tmp/
adb shell 'mount -o remount,rw /vendor_dlkm'
adb shell 'cat /data/local/tmp/oplus_chg.ko > /vendor_dlkm/lib/modules/oplus_chg.ko'   # 原地覆盖，保留 inode/SELinux 标签
adb shell 'sync; ls -l /vendor_dlkm/lib/modules/oplus_chg.ko'
adb reboot
```

* 不要 `rm` 再 `cp`：`cat >` 覆盖保留 inode、权限、`security.selinux=u:object_r:vendor_file:s0`。
* 不需要 `depmod`：路径没变，`modules.dep` 里 `oplus_chg.ko` 的依赖项仍然有效
  （我们的模块引用符号是 stock 的超集，且新增的那条是 vmlinux 导出，不产生新的模块依赖）。
* 回滚：`cat /data/local/tmp/oplus_chg.ko.stock > /vendor_dlkm/lib/modules/oplus_chg.ko`（事先备份）。
* 失败症状：`insmod`/开机 dmesg 里 `version magic ... should be ...`、`disagrees about version
  of symbol` 或 `Unknown symbol`；模块没加载 ⇒ 充电管理缺失（能开机，但充电/电量显示异常）。

### 4.2 方案 b：重建 `vendor_dlkm.img` 再 fastboot 刷 —— **已在本机全流程实测**

脚本：`artifacts/build/repack-vendor_dlkm.sh`（步骤与理由见脚本头注释）。
默认 `--fs stock`：产物 ext4 特性位与 stock **逐字一致**（含 `shared_blocks`），
所以设备上仍然只能只读挂载、行为与出厂镜像相同；加 `--fs rw` 才会去掉去重特性位
（那是为了后续 adb 直推方便，非必需）。
本配方不改镜像的**文件系统类型**：dump 出来的 `vendor_dlkm_a.img` 实测就是 ext4
（superblock magic `53ef`、卷标 `vendor_dlkm`），这里只是原位替换其中一个文件的内容，
其余 391 个文件逐字节不变。

```bash
bash artifacts/build/repack-vendor_dlkm.sh \
  --ko artifacts/build/out/oplus_chg.ko \
  --stock /home/aibeto/下载/vendor_dlkm_a.img \
  --out artifacts/build/out/vendor_dlkm_pps.img \
  --verify-dir artifacts/build/work/vendor_dlkm/modules
```

它做的事（每一步都有必要性）：

1. 复制 stock 镜像（**不动原件**）；
2. `debugfs -w -R "feature -shared_blocks"`：去掉去重特性，否则设备上永远只能只读挂载；
3. `e2fsck -fy`：把被多个 inode 共享的块**克隆**成各自私有（stock 里 `camera.ko`、
   `oplus_chg.ko`、`oplus_chg_v2.ko` 等 15 个 inode 共享块 1248/2825/12280/…），
   否则后续替换会**污染别的模块**（我第一次这么做就把新文件写进了共享块，fsck 立刻报
   “重复引用的块”）；
4. `debugfs rm` 旧文件 + `write` 新文件（旧文件 7.36 MB → 新文件 6.95 MB，腾出的块够用）；
5. `ea_set -f <26 字节含 NUL 的文件> … security.selinux`：**补回 SELinux 标签**
   （`u:object_r:vendor_file:s0`，必须是带结尾 `\0` 的 26 字节，`-f` 才能保证）；
6. `e2fsck -fy` + `e2fsck -fn`（必须干净）。

实测输出要点：`409/416 文件`、`Free blocks 180`、features 里已无 `shared_blocks`、
`security.selinux (26) = "u:object_r:vendor_file:s0\000"`、`e2fsck -fn` 无报错、
逐文件比对 **只有 `oplus_chg.ko` 不同**（其它 391 个文件字节级一致 ⇒ 没破坏别的东西）。

刷入（分区名必须先确认，见 §6.13）：

```bash
adb reboot bootloader
fastboot getvar current-slot          # 确认是 a 还是 b
fastboot flash vendor_dlkm_a artifacts/build/out/vendor_dlkm_pps.img
fastboot reboot
```

* 回滚：`fastboot flash vendor_dlkm_a /home/aibeto/下载/vendor_dlkm_a.img`
  （原件必须留好，别覆盖）。
* 副作用（好事）：刷过之后该分区可以在机器上 RW 挂载，后续迭代走 §4.1 的 push 就够。
* 失败症状：刷错分区/槽 → 开机缺 vendor_dlkm 模块（基带、显示、充电一堆模块缺失，
  通常卡 logo 或进不去系统）；分区尺寸必须完全一致（都是 172 703 744 B，直接 raw flash）。
* 未验证项：AVB 关闭后分区是否还有别的校验（dm-verity 表）、`fastboot flash` 是否要求
  先 `erase`；这些只能靠用户实测（见 §6）。

### 4.3 方案 c：连内核一起刷（自洽，但代价最大）

只有当方案 a/b 都走不通、或者必须改内核侧代码时才考虑。关键点：

* 自建内核的 `Module.symvers` 由**我们自己**的 genksyms 生成，与 stock 的 CRC 表
  （`work/Module.symvers.vendor`，4042 条）不一定相同。**刷内核前必须核对**：

  ```bash
  # 构建 vmlinux（用户自己跑；这是唯一需要长构建的路径）
  make -C $FARM O=$OUT $MA -j6 Image modules      # 15 GB 内存：JOBS<=6 + THINLTO_JOBS=2 + ld-thin.sh
  # 拿我们内核的 CRC 表对比库存表
  diff <(sort $OUT/Module.symvers) <(sort artifacts/build/work/Module.symvers.vendor) | head
  ```

  只要有一条 CRC 不同，**stock 的某个 vendor 模块就会加载失败**（`disagrees about version
  of symbol`），386 个模块里任何一个崩都可能表现为充电/显示/基带异常。
* 也就是说：方案 c 的正确性判据 = 「我们的 CRC 表 ⊇ stock 的 CRC 表且值相同」。
  `oplus_chg.ko` 这种“只换一个模块”的需求，方案 b 风险低得多。
* 打包走现成流程：`scripts/pack_anykernel3.sh`（`third_party/AnyKernel3`），
  产物 `artifacts/AnyKernel3_stock_sm8550_<tag>.zip`。
* 回滚：刷回 stock `boot_a.img`（`/home/aibeto/下载/boot_a.img`）。
* 失败症状：内核起来但大量 vendor 模块加载失败（dmesg 刷 `disagrees about version`），
  或者 `version magic` 不符 —— 前者是 CRC 不匹配，后者是 flag/前缀问题。

### 4.4 方案 d：Magisk / KernelSU overlay（如果机器这么 root 的，风险最低）

思路：不碰分区，用一个 Magisk 模块把新 `.ko` overlay 到
`/vendor_dlkm/lib/modules/oplus_chg.ko`，删除模块即回滚（`rm -rf /data/adb/modules/<id>` + 重启）。
待确认：用户的 Magisk/KernelSU 版本是否支持 `/vendor_dlkm` 的 magic mount（不是所有版本都覆盖
这个分区）；确认命令见 §6.13。这条我没法在本机验证，**不建议作为首选**，但如果成立，
它是唯一“零分区写入、秒级回滚”的方案。

---

### 4.5 一次性刷入 PPS 支持的完整步骤（推荐路径）

前提：`vendor_dlkm_pps.img`（默认原参数版）已经生成，且 `oplus_chg.ko` 里已含
`PPS_3RD_ASK_VOLT_MAX=11000` 与 `/sys/class/qcom-battery/pps_req` 两处改动；
DT 侧（PPS 能力/区域/电流那部分）在 **dtbo** 里，需另刷（见第 7 步）。

```bash
# ── 第 0 步：留好退路（务必做，回滚全靠它）────────────────────────────
cp /home/aibeto/下载/vendor_dlkm_a.img  artifacts/build/out/ROLLBACK_vendor_dlkm_a.img
adb shell 'cp /vendor_dlkm/lib/modules/oplus_chg.ko /data/local/tmp/oplus_chg.ko.stock'

# ── 第 1 步：确认槽位与分区（输出里的 a/b 决定后面 flash 哪个）────────
adb shell getprop ro.boot.slot_suffix
adb shell 'ls -l /dev/block/by-name | grep vendor_dlkm'
adb reboot bootloader
fastboot getvar current-slot          # 例如输出 a

# ── 第 2 步：刷 vendor_dlkm（约 172 MB，几十秒）──────────────────────
fastboot flash vendor_dlkm_a artifacts/build/out/vendor_dlkm_pps.img
#   若当前槽是 b：把 vendor_dlkm_a 换成 vendor_dlkm_b
#   若分区名不带槽：直接 fastboot flash vendor_dlkm …

# ── 第 3 步：刷 DT（PPS 能力/区域/电流在 dtbo 里）─────────────────────
fastboot flash dtbo_a artifacts/<你改好的 dtbo>.img     # 槽位同上
#   若 dtbo 已经刷过并验证过（27 W 阶段），这步可跳过

# ── 第 4 步：重启并验证模块真的换上了 ────────────────────────────────
fastboot reboot
adb wait-for-device
adb shell 'cat /sys/module/oplus_chg/srcversion'    # 必须等于 49be3bc… 那次构建的 srcversion
adb shell 'cat /sys/module/oplus_chg/taint'         # 含 O（out-of-tree）= 我们的模块
adb shell 'dmesg | grep -iE "oplus_chg.*(verification failed|taints kernel)"'
adb shell 'ls /sys/module/oplus_chg/parameters | wc -l'   # 21

# ── 第 5 步：验证 PPS 生效 ──────────────────────────────────────────
adb shell 'ls -l /sys/class/qcom-battery/pps_req'   # 新加的 class 属性应存在
adb shell 'dmesg | grep -iE "pps|ufcs|vooc"' | tail -40
adb shell 'cat /sys/class/power_supply/battery/voltage_now /sys/class/power_supply/battery/current_now'
#   插上 55 W PPS 充电器，观察协商后的电压/电流是否进入 PPS 区间

# ── 回滚（任何一步不满意）───────────────────────────────────────────
fastboot flash vendor_dlkm_a artifacts/build/out/ROLLBACK_vendor_dlkm_a.img
#   或者不刷机、只回滚模块（若分区可写）：cat /data/local/tmp/oplus_chg.ko.stock > /vendor_dlkm/lib/modules/oplus_chg.ko
```

失败症状对照：卡 logo / 进不去系统 → 多半刷错分区或槽位（刷回 ROLLBACK 镜像）；
开机但 `srcversion` 没变 → 镜像没刷进去（`fastboot flash` 看是否报 size/partition 错误）；
`dmesg` 有 `disagrees about version of symbol` → CRC 不符（说明 `Module.symvers` 不是这台设备的，
按附录 A 重新生成）；有 `version magic … should be` → 用了别的配置编的模块。

## 5. 刷前验证清单（照抄即可）

```bash
cd /home/aibeto/code/android_kernel_oneplus_sm8550
NEW=artifacts/build/out/oplus_chg.ko
STOCK=artifacts/build/work/stock_oplus_chg.ko

# 1) vermagic 后缀必须一致（前缀会被内核忽略）
diff <(modinfo -F vermagic $NEW | cut -d' ' -f2-) <(modinfo -F vermagic $STOCK | cut -d' ' -f2-) && echo "vermagic OK"

# 2) CRC 表：必须“缺 0 条”，多出来的只能是源码差异
comm -23 <(modprobe --dump-modversions $STOCK | sort) \
         <(modprobe --dump-modversions $NEW   | sort) | wc -l      # 期望 0

# 3) 我们导出的符号 CRC 不能被 stock 依赖方判为不一致（§3.3 那 5 条）
modprobe --dump-modversions $NEW | grep -E "register_chg_glink_notifier_v1|unregister_chg_glink_notifier_v1"

# 4) 基本形态
readelf -h $NEW | grep -E "Class|Type|Machine"        # ELF64 / REL / AArch64
readelf -S $NEW | grep -cE "\.debug"                  # 期望 0（已 strip）
modinfo $NEW | grep -E "^(name|vermagic|srcversion|depends)"
stat -c '%s %n' $NEW                                  # ~6.9 MB；stock 7.36 MB，差额主要是 stock 的 .BTF（417 133 B）

# 5) 镜像（如果用方案 b）
e2fsck -fn artifacts/build/out/vendor_dlkm_pps.img     # 必须无报错
# 默认镜像：ext4 特性位必须与 stock 逐字一致（含 shared_blocks ⇒ 只读语义不变）
diff <(dumpe2fs -h artifacts/build/out/vendor_dlkm_pps.img 2>/dev/null | sed -n 's/^Filesystem features: *//p') \
     <(dumpe2fs -h /home/aibeto/下载/vendor_dlkm_a.img  2>/dev/null | sed -n 's/^Filesystem features: *//p') \
  && echo "特性位与 stock 一致"
stat -c '%s' artifacts/build/out/vendor_dlkm_pps.img    # 必须 = 172703744
```

**上机后怎么证明“跑的是我们编的那个模块”**（三个独立证据）：

```bash
adb shell 'cat /sys/module/oplus_chg/srcversion'         # 应等于 modinfo -F srcversion 的值
adb shell 'dmesg | grep -iE "oplus_chg.*(verification failed|taints kernel)"'
#   未签名 + out-of-tree -> 会有 module verification failed 与 “loading out-of-tree module taints kernel”
adb shell 'ls /sys/module/oplus_chg/parameters | wc -l'  # 期望 21 个参数（我们模块实测 21 个）
adb shell 'cat /sys/module/oplus_chg/taint'              # 含 O（out-of-tree）：stock 模块是 intree:Y，所以 O 就是我们的指纹
```

改完代码后 `srcversion` 会变（它由源码/配置算出），这也是验证“新模块真的在里面”的可靠锚点。

---

## 6. 风险与未知项（每条附“什么证据能定论”）

| # | 风险 | 现状 / 证据 | 定论方式 |
|---|---|---|---|
| 6.1 | 源码版本漂移（本树 5.15.197 vs 设备 5.15.180）：可能有结构体/接口变化导致运行时行为异常 | 符号集差 1 个（`oplus_chg_get_adsp_usb_current`/`schedule_timeout`）；被依赖的 5 个导出符号 CRC 5/5 一致 | 用 `work/make-symvers.py` 收更多分区模块补 CRC；或构建 vmlinux 后 `diff Module.symvers` 到 0 条差异；或对比 stock `.ko` 的 `.BTF` 与我们的 `.debug_info` 里关键 struct 尺寸 |
| 6.2 | vendor 配置片段可能不全（只知道 `kalama_GKI.config`；官方构建可能还合 `*_oplus.config`） | 本工作区里 kalama 只有这一个片段；`oplus/config/` 下只有 wapio 的 `build.config.msm.wapio.oplus` + `waipio_oplus[_do].config`（sm8450 用） | 让用户 `adb shell zcat /proc/config.gz`（stock 内核 `CONFIG_IKCONFIG_PROC=y`）比对内核侧配置；模块侧配置只能靠符号集比对（我们未缺 stock 任何全局符号） |
| 6.3 | `.BTF` 缺失（stock 有 417 KB） | BTF 需要 vmlinux；kbuild 直接跳过：`Skipping BTF generation … unavailability of vmlinux` | 加载不需要 BTF；若某工具依赖，只能构建 vmlinux |
| 6.4 | `depends:` 为空、缺 `intree: Y` | `M=` 外部模块构建的固有结果；stock 的 `depends` 有 12 个模块 | 不影响加载：modprobe 用 `modules.dep`（路径没变），内核只看符号是否已解析；`intree` 只影响 taint 标记 |
| 6.5 | 未签名 → taint `TAINT_UNSIGNED_MODULE` | 已证明放行（§3.4） | 若设备内核配置与 `boot_a.img` 里的不同（例如后来换了内核），`/proc/version` + `zcat /proc/config.gz` 可确认 |
| 6.6 | `/vendor_dlkm` 不可 RW 挂载（shared_blocks） | 内核源码拒绝（§4.1）+ 镜像 flag `0x4000` | 用户机器上跑 §4.1 的两条命令看 dmesg |
| 6.7 | fastboot 分区名/槽位搞错（`vendor_dlkm_a` vs `vendor_dlkm`，A/B 槽） | 未知 | `fastboot getvar current-slot` + `ls /dev/block/by-name | grep vendor_dlkm` |
| 6.8 | 设备上是否还有别的 `oplus_chg.ko` 副本（first-stage / vendor_boot ramdisk） | 未知；只确认了 vendor_dlkm 里有 | `adb shell 'find / -name oplus_chg.ko 2>/dev/null'` |
| 6.9 | 刷镜像后 AVB/其它校验 | AVB 已关；vendor_dlkm 是否另有 verity 表未知 | 看 `adb shell 'cat /proc/mounts | grep vendor_dlkm'` 与 `dmesg | grep -i verity` |
| 6.10 | 第一次替换若不先清 shared_blocks 会污染其它模块 | 我实测过：fsck 报“重复引用的块”（块 1248 被 10 个模块共享） | 已在脚本里固化为“先清特性 + fsck 克隆”的顺序，并用 391 个文件的 md5 比对证明无损 |
| 6.11 | 内存/时间：15 GB + systemd-oomd | 编模块 `-j6` 实测安全（59 s 峰值无压力） | 只有 `Image` 链接才需要 `THINLTO_JOBS=2` |
| 6.12 | 新代码引入新符号 → modpost 失败 | 有 `KBUILD_MODPOST_WARN=1` 兜底（实测降级为 warning 并产出 .ko） | 首选补 CRC；实在没有就让内核打 `no symbol version` 放行 |
| 6.13 | 需要用户提供的信息 | — | ① `uname -r`；② `mount \| grep vendor_dlkm` + 分区名/槽位；③ root 方式（Magisk/KernelSU/纯 root） |

---

## 附录 A：从零复现（干净机器上）

```bash
# 0) 前提：.toolchain/clang-r450784e 已就位（scripts/fetch-clang.sh）
# 1) 目录布局镜像
bash artifacts/build/setup-vendor-build-tree.sh                  # 8 s
# 2) 配置 + 编译 + 归档（脚本内含 gki_defconfig/merge/LTO/symvers/prepare/modules/strip）
bash artifacts/build/build-oplus-chg.sh                          # 首次 ~105 s
# 3) 镜像
bash artifacts/build/repack-vendor_dlkm.sh --ko artifacts/build/out/oplus_chg.ko \
     --stock /home/aibeto/下载/vendor_dlkm_a.img \
     --out artifacts/build/out/vendor_dlkm_pps.img
# 4) 验证（§5）
```

若要重建 CRC 表（换了一台设备 / 换了 stock 固件之后）：

```bash
debugfs -R "rdump /lib/modules artifacts/build/work/vendor_dlkm" <stock vendor_dlkm.img>
cp <stock 镜像里的>oplus_chg.ko artifacts/build/work/stock_oplus_chg.ko
python3 artifacts/build/work/make-symvers.py artifacts/build/work/Module.symvers.vendor \
        --exclude-ko artifacts/build/work/stock_oplus_chg.ko \
        artifacts/build/work/vendor_dlkm artifacts/build/work/stock_oplus_chg.ko
```

## 附录 B：本次产出文件

| 路径 | 说明 |
|---|---|
| `artifacts/build/setup-vendor-build-tree.sh` | 建 `kp/` 硬链接镜像（修悬空 vendor 链接 + `Documentation/Kconfig`） |
| `artifacts/build/build-oplus-chg.sh` | 配置 → prepare → 编模块 → strip → 归档，含 vermagic 自检 |
| `artifacts/build/repack-vendor_dlkm.sh` | 重建可刷 vendor_dlkm 镜像（清特性/克隆块/换文件/补 SELinux/fsck） |
| `artifacts/build/work/make-symvers.py` | 从库存 `.ko` 的 `__versions` 汇总库存内核 CRC 表（5 列格式、支持 `--exclude-ko`） |
| `artifacts/build/work/Module.symvers.vendor` | 4042 条库存内核符号 CRC 表（本设备） |
| `artifacts/build/work/stock_oplus_chg.ko` | 从 stock 镜像抽出的原模块（7 357 432 B，对照基准） |
| `artifacts/build/work/stock_kernel.img` / `stock_kernel_config` | stock `boot_a.img` 里抽出的内核与其内嵌配置（LTO/CFI/签名/qmod 依据） |
| `artifacts/build/out/oplus_chg.ko` | **最终模块** 6 945 736 B |
| `artifacts/build/out/vendor_dlkm_pps.img` | **最终镜像（默认，ext4 参数与 stock 一致、只读语义）** 172 703 744 B |
| `artifacts/build/out/vendor_dlkm_pps_rw.img` | 可选镜像（去掉 `shared_blocks`，可 rw 挂载）172 703 744 B |
| `artifacts/build/kp/**` | 编译用的布局镜像 + `out-vendor/` 构建目录（可随时删除重建） |
