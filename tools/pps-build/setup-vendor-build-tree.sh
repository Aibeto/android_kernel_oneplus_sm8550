#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# 在 artifacts/build/kp/ 下重建 OnePlus msm-kernel 源码树所期望的
# kernel_platform 目录布局（硬链接，不复制文件数据）。
#
# 为什么需要它：
#   本仓库（android_kernel_oneplus_sm8550 = OnePlus 发布的 msm-kernel 树）
#   内部大量 vendor 软链接按「树根位于 ws/kernel_platform/msm-kernel、
#   vendor 位于 ws/vendor」的深度写成相对路径，例如
#       kernel/oplus_cpu            -> ../../../vendor/oplus/kernel/cpu
#       drivers/power/oplus         -> ../../../../vendor/oplus/kernel/charger
#   当前工作区把 msm-kernel 直接铺在仓库根，这些链接全部悬空，
#   连 `make gki_defconfig` 都会在 init/Kconfig:886 报
#   can't open file "kernel/oplus_cpu/thermal/Kconfig"。
#
#   本脚本用 cp -al 建一棵硬链接镜像：
#       artifacts/build/kp/                      (= ws)
#         vendor -> <repo>/devicetree/sm8550/vendor
#         oplus  -> <repo>/devicetree/sm8550/kernel_platform/oplus
#         kernel_platform/msm-kernel/            (= msm-kernel 树，硬链接)
#   这样两条相对路径都恰好落到 ws/vendor，且不改动任何源码文件。
#
# 幂等：重复执行只会补齐缺失项；不会删除任何东西。
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
SHIM=$REPO/artifacts/build/kp
FARM=$SHIM/kernel_platform/msm-kernel

mkdir -p "$FARM"

# ws 层的 vendor / oplus（与 scripts/setup-tree.sh 在仓库根做的事等价）
ln -sfn "$REPO/devicetree/sm8550/vendor" "$SHIM/vendor"
ln -sfn "$REPO/devicetree/sm8550/kernel_platform/oplus" "$SHIM/oplus"

# 不进入镜像的顶层项：构建产物、无关子模块、文档、git 元数据
SKIP=" out artifacts .git .dsh-progress devicetree Documentation .gitmodules \
       android_kernel_oneplus_sm8650 android_kernel_oneplus_sm8750 "

shopt -s nullglob
for e in "$REPO"/* "$REPO"/.[!.]*; do
  b=$(basename "$e")
  case "$SKIP" in *" $b "*) continue ;; esac
  if [ -e "$FARM/$b" ] || [ -L "$FARM/$b" ]; then
    continue
  fi
  cp -al "$e" "$FARM/" 2>/dev/null || cp -a "$e" "$FARM/"
done

# 树内 vendor/oplus 指向 ws 层（相对路径按 msm-kernel 树根计算）
ln -sfn ../../vendor "$FARM/vendor"
ln -sfn ../../oplus  "$FARM/oplus"

# lib/Kconfig.debug:2669 会 source "Documentation/Kconfig"，Documentation/ 不在镜像内
mkdir -p "$FARM/Documentation"
ln -sfn "$REPO/Documentation/Kconfig" "$FARM/Documentation/Kconfig"

echo "== shim 就绪: $FARM"
echo "   -> $(readlink -f "$FARM/vendor")"
echo "   -> $(readlink -f "$FARM/oplus")"
test -f "$FARM/kernel/oplus_cpu/thermal/Kconfig" ||
  { echo "kernel/oplus_cpu/thermal/Kconfig 仍不可达" >&2; exit 1; }
test -f "$FARM/drivers/power/oplus/Kconfig" ||
  { echo "drivers/power/oplus/Kconfig 仍不可达" >&2; exit 1; }
echo "== 悬空 vendor 链接已修复（抽查 kernel/oplus_cpu, drivers/power/oplus）"
