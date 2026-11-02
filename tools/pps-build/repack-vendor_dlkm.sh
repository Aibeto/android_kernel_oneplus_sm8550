#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# 用新编的 oplus_chg.ko 重建可 fastboot flash 的 vendor_dlkm 镜像。
#
# 用法:
#   bash artifacts/build/repack-vendor_dlkm.sh \
#        --ko artifacts/build/out/oplus_chg.ko \
#        --stock /home/aibeto/下载/vendor_dlkm_a.img \
#        --out artifacts/build/out/vendor_dlkm_pps.img \
#        --verify-dir artifacts/build/work/vendor_dlkm/modules
#
#   --fs stock（默认）: 产物与 stock 镜像的 ext4 特性位**完全一致**
#                       （含 shared_blocks），因此设备上只能只读挂载，
#                       行为与出厂镜像相同（不会被改成可 rw 的普通 ext4）。
#   --fs rw          : 去掉 shared_blocks（内核才能 rw 挂载，方便后续 adb push）。
#
# 为什么要绕这么一圈：
#   stock 镜像的 ext4 带 shared_blocks（Google 的去重特性：同一物理块被多个
#   inode 共享，oplus_chg.ko 自己的 742–744 号逻辑位置就复用了共享块 1248）。
#   * 直接 rm+write 会把这些共享块分给新文件，从而**破坏别的模块**
#     （实测 fsck 立刻报“重复引用的块”，且块 1248 同时被 camera.ko、
#       msm_drm.ko、oplus_chg_v2.ko 等 10 个文件引用）；
#   * 所以必须先清掉特性位、用 e2fsck 把共享块克隆成各文件私有，
#     再替换文件；--fs stock 最后再把特性位加回去，使产物参数与 stock 一致。
#
# 校验：末尾 e2fsck -fn 必须干净；--verify-dir 会逐文件 cmp，只有
#       oplus_chg.ko 允许不同。
set -euo pipefail

KO=""; STOCK=""; OUT=""; VERIFY_DIR=""; FS_MODE=stock
LABEL=${LABEL:-u:object_r:vendor_file:s0}
while [ $# -gt 0 ]; do
	case "$1" in
		--ko)          KO=$2; shift 2 ;;
		--stock)       STOCK=$2; shift 2 ;;
		--out)         OUT=$2; shift 2 ;;
		--verify-dir)  VERIFY_DIR=$2; shift 2 ;;
		--label)       LABEL=$2; shift 2 ;;
		--fs)          FS_MODE=$2; shift 2 ;;
		*) echo "未知参数 $1" >&2; exit 1 ;;
	esac
done
[ -f "$KO" ] && [ -f "$STOCK" ] && [ -n "$OUT" ] || {
	echo "用法: $0 --ko <新模块> --stock <stock镜像> --out <输出镜像> [--fs stock|rw]" >&2
	exit 1; }
case "$FS_MODE" in stock|rw) ;; *) echo "--fs 只能是 stock 或 rw" >&2; exit 1 ;; esac

WORK=$(dirname "$OUT"); mkdir -p "$WORK"
LABELFILE=$WORK/.selinux_label.bin
printf '%s\0' "$LABEL" > "$LABELFILE"

echo "== 1/7 复制 stock 镜像（原件不动）"
cp -f "$STOCK" "$OUT"

echo "== 2/7 记录 stock 参数"
stock_size=$(stat -c%s "$STOCK")
stock_feat=$(dumpe2fs -h "$STOCK" 2>/dev/null | sed -n 's/^Filesystem features: *//p')
echo "   size=$stock_size"
echo "   features=$stock_feat"

echo "== 3/7 临时清除 shared_blocks，e2fsck 克隆共享块（否则会污染别的模块）"
debugfs -w -R "feature -shared_blocks" "$OUT" >/dev/null
e2fsck -fy "$OUT" >/dev/null 2>&1 || true

echo "== 4/7 替换 /lib/modules/oplus_chg.ko 并补回 SELinux 标签"
debugfs -w -R "rm /lib/modules/oplus_chg.ko" "$OUT" >/dev/null
debugfs -w -R "write $KO /lib/modules/oplus_chg.ko" "$OUT" >/dev/null
debugfs -w -R "ea_set -f $LABELFILE /lib/modules/oplus_chg.ko security.selinux" "$OUT" >/dev/null

echo "== 5/7 e2fsck 收尾"
e2fsck -fy "$OUT" >/dev/null 2>&1 || true

if [ "$FS_MODE" = stock ]; then
	echo "== 6/7 把 shared_blocks 特性位加回去（与 stock 参数一致：只能 ro 挂载）"
	debugfs -w -R "feature +shared_blocks" "$OUT" >/dev/null
else
	echo "== 6/7 保留可 rw 挂载的状态（无 shared_blocks）"
fi

echo "== 7/7 验收"
e2fsck -fn "$OUT"
echo "-- 参数对比"
out_size=$(stat -c%s "$OUT")
out_feat=$(dumpe2fs -h "$OUT" 2>/dev/null | sed -n 's/^Filesystem features: *//p')
echo "   镜像大小: stock=$stock_size ours=$out_size"
[ "$out_size" = "$stock_size" ] && echo "   => 大小一致" || { echo "   => 大小不一致！" >&2; exit 1; }
if [ "$FS_MODE" = stock ]; then
	echo "   特性位:   stock=$stock_feat"
	echo "             ours =$out_feat"
	[ "$out_feat" = "$stock_feat" ] && echo "   => 特性位与 stock 完全一致（ro 挂载语义不变）" || {
		echo "   => 特性位不一致！" >&2; exit 1; }
else
	echo "   特性位:   ours =$out_feat  （刻意去掉 shared_blocks，可 rw 挂载）"
fi
dumpe2fs -h "$OUT" 2>/dev/null | grep -E "^(Free blocks|Block count)"
debugfs -R "stat /lib/modules/oplus_chg.ko" "$OUT" 2>/dev/null | sed -n '1,8p'
debugfs -R "ea_list /lib/modules/oplus_chg.ko" "$OUT" 2>/dev/null | grep -A1 selinux

if [ -n "$VERIFY_DIR" ]; then
	echo "== 逐文件 cmp（只允许 oplus_chg.ko 不同）"
	tmp=$WORK/.verify_dump.$(date +%s)
	mkdir -p "$tmp"
	debugfs -R "rdump /lib/modules $tmp" "$OUT" >/dev/null 2>&1
	diffn=0; total=0
	for f in "$tmp"/modules/*; do
		b=$(basename "$f")
		total=$((total+1))
		if ! cmp -s "$f" "$VERIFY_DIR/$b"; then echo "DIFF: $b"; diffn=$((diffn+1)); fi
	done
	echo "比对 $total 个文件，不同 $diffn 个（解包对照在 $tmp）"
	echo "-- 镜像里的 oplus_chg.ko 是否等于 --ko 指定的文件:"
	cmp -s "$tmp/modules/oplus_chg.ko" "$KO" && echo "   => 完全一致" || echo "   => 不一致！"
fi
