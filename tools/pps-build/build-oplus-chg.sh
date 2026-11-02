#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# 编译 vendor/oplus/kernel/charger/v1 → oplus_chg.ko（OnePlus 11 / SM8550 / 22811）
#
# 用法:
#   bash artifacts/build/build-oplus-chg.sh            # 全流程（首次约 2 分钟）
#   bash artifacts/build/build-oplus-chg.sh --modules  # 只重编模块（改代码后用，约 1 分钟）
#
# 关键点（详见 artifacts/build/BUILD_RECIPE.md）:
#   * 源码树必须能解析 msm-kernel 的 vendor 相对软链接 —— 由
#     setup-vendor-build-tree.sh 在 artifacts/build/kp/ 建硬链接镜像完成。
#   * CONFIG_MODVERSIONS=y 时模块 __versions 的 CRC 必须等于设备内核的 CRC。
#     这里用「库存 .ko 的 __versions 汇总表」($SYMVERS) 冒充库存内核的
#     Module.symvers，modpost 就会给新模块填上库存内核的 CRC。
#   * LOCALVERSION 传空串：否则 vermagic 会带 git 描述和 -dirty（前缀虽被内核
#     忽略，但会让产物不可复现）。
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
SHIM=$REPO/artifacts/build/kp
FARM=$SHIM/kernel_platform/msm-kernel
OUT=$SHIM/out-vendor
CLANG_DIR=$REPO/.toolchain/clang-r450784e/bin
JOBS=${JOBS:-6}
MODDIR=vendor/oplus/kernel/charger/v1
SYMVERS=${SYMVERS:-$REPO/artifacts/build/work/Module.symvers.vendor}
DEST=$REPO/artifacts/build/out

MAKE_ARGS=(ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu-
           CC="$CLANG_DIR/clang" HOSTCC="$CLANG_DIR/clang" LD=ld.lld
           HOSTLD=ld.lld PAHOLE=/usr/bin/pahole LOCALVERSION=)

[ -x "$CLANG_DIR/clang" ] || { echo "缺少 clang: $CLANG_DIR/clang" >&2; exit 1; }
export PATH="$CLANG_DIR:$PATH"

# 1) 目录布局（幂等，8 秒）
if [ ! -e "$FARM/kernel/oplus_cpu/thermal/Kconfig" ]; then
	echo "== 1/5 建立 kernel_platform 布局镜像"
	bash "$REPO/artifacts/build/setup-vendor-build-tree.sh"
fi

# 2) 配置：gki_defconfig + kalama_GKI.config
if [ ! -f "$OUT/.config" ]; then
	echo "== 2/5 生成 .config（gki_defconfig + kalama_GKI.config）"
	make -s -C "$FARM" O="$OUT" "${MAKE_ARGS[@]}" gki_defconfig
	ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
	  "$FARM/scripts/kconfig/merge_config.sh" -m -O "$OUT" "$OUT/.config" \
	  "$FARM/arch/arm64/configs/vendor/kalama_GKI.config" >/dev/null
	# LTO 模式必须和设备内核一致：库存内核配置是 CONFIG_LTO_CLANG_FULL=y
	# （从 boot.img 里 scripts/extract-ikconfig 抽出来的），gki_defconfig 默认
	# 也是 FULL，所以这里显式钉住 FULL、关掉 THIN。
	# 注意 scripts/build-gki.sh 用的是 THIN —— 那是为了在 15 GB 内存上链接
	# 整个 Image，和「只编模块」不是一回事；模块走 FULL 不需要链接 vmlinux，
	# 实测 59 秒、峰值内存无压力，且符号集与库存模块完全对齐（见 BUILD_RECIPE §2）。
	"$FARM/scripts/config" --file "$OUT/.config" \
	  -e LTO_CLANG -e LTO_CLANG_FULL -d LTO_CLANG_THIN -d LTO_CLANG_NONE
	# 关闭 LOCALVERSION_AUTO，让 vermagic 不依赖 git 状态
	"$FARM/scripts/config" --file "$OUT/.config" -d LOCALVERSION_AUTO --set-str LOCALVERSION ""
	make -s -C "$FARM" O="$OUT" "${MAKE_ARGS[@]}" olddefconfig
	make -s -C "$FARM" O="$OUT" "${MAKE_ARGS[@]}" syncconfig
	for s in CONFIG_OPLUS_CHG=m CONFIG_OPLUS_CHG_V2=m CONFIG_OPLUS_SM8550_CHARGER=y \
	         CONFIG_MODVERSIONS=y CONFIG_MODULE_SIG_PROTECT=y; do
		grep -qx "$s" "$OUT/.config" || { echo "配置缺 $s" >&2; exit 1; }
	done
fi

# 3) 库存内核的符号 CRC 表 -> $OUT/Module.symvers
[ -f "$SYMVERS" ] || {
	echo "缺少 $SYMVERS（用 work/make-symvers.py 从库存 .ko 生成）" >&2; exit 1; }
cp "$SYMVERS" "$OUT/Module.symvers"

# 4) 准备编译环境（首次 ~26 秒，之后增量化）
echo "== 3/5 modules_prepare"
make -s -C "$FARM" O="$OUT" "${MAKE_ARGS[@]}" -j"$JOBS" modules_prepare

# 5) 编模块（首次 ~45 秒；改代码后增量）
echo "== 4/5 make M=$MODDIR modules"
make -C "$FARM" O="$OUT" M="$MODDIR" "${MAKE_ARGS[@]}" -j"$JOBS" modules

KO=$OUT/$MODDIR/oplus_chg.ko
[ -f "$KO" ] || { echo "没有产出 $KO" >&2; exit 1; }

# 6) 去调试信息（库存模块同样不带调试节），存到 artifacts/build/out/
echo "== 5/5 去调试信息并归档"
mkdir -p "$DEST"
#    原始（带调试节、~44 MB）留在 $KO，方便后续用 pahole/gdb 分析
"$CLANG_DIR/llvm-strip" -g -o "$DEST/oplus_chg.ko" "$KO"

echo
echo "== 产物: $DEST/oplus_chg.ko"
ls -l "$DEST/oplus_chg.ko"
sha256sum "$DEST/oplus_chg.ko"
modinfo -F vermagic "$DEST/oplus_chg.ko" | sed 's/^/vermagic: /'
modinfo -F name      "$DEST/oplus_chg.ko" | sed 's/^/name: /'
modinfo -F srcversion "$DEST/oplus_chg.ko" | sed 's/^/srcversion: /'
echo "== __versions 条目数: $(modprobe --dump-modversions "$DEST/oplus_chg.ko" | wc -l)"

# 7) 与库存模块对比 vermagic 后缀（前缀会被内核忽略，后缀必须完全一致）
STOCK=$REPO/artifacts/build/work/stock_oplus_chg.ko
if [ -f "$STOCK" ]; then
	ours=$(modinfo -F vermagic "$DEST/oplus_chg.ko" | cut -d' ' -f2-)
	theirs=$(modinfo -F vermagic "$STOCK" | cut -d' ' -f2-)
	echo "== vermagic 后缀: 新='$ours' 库存='$theirs'"
	[ "$ours" = "$theirs" ] && echo "   => 一致" || echo "   => 不一致！不可加载"
fi
