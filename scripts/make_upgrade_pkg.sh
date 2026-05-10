#!/bin/bash
# ============================================================
# 鸿钧升级包制作脚本
# ============================================================
# 用法：
#   ./make_upgrade_pkg.sh 0.3.0        # 打包当前版本 → v0.3.0.tar.gz
#   ./make_upgrade_pkg.sh 0.3.0 --dir # 用本地目录打包
#
# 升级包格式：
#   upgrades/releases/vX.Y.Z.tar.gz    # tar.gz 打包
#   upgrades/releases/vX.Y.Z/          # 目录打包
#   upgrades/releases/vX.Y.Z.url      # 内容为 HTTP 下载 URL
#
# 打包内容（UPGRADABLE）：
#   src/hongjun/   核心源代码
#   requirements.txt
#   SPEC.md / README.md
#   deploy/
# ============================================================

set -e

VERSION="${1?用法: $0 <版本号> [来源目录]}"
SOURCE_DIR="${2:-}"

UPGRADE_ROOT="/home/asus/hongjun"
RELEASES_DIR="$UPGRADE_ROOT/upgrades/releases"
TARGET_TAR="$RELEASES_DIR/v${VERSION}.tar.gz"
TARGET_DIR="$RELEASES_DIR/v${VERSION}"

mkdir -p "$RELEASES_DIR"

echo "📦 开始制作升级包: v${VERSION}"

if [ -n "$SOURCE_DIR" ]; then
    # 从指定目录打包
    echo "📂 来源目录: $SOURCE_DIR"
    mkdir -p "$TARGET_DIR"
    for item in src/hongjun requirements.txt SPEC.md README.md deploy; do
        if [ -e "$SOURCE_DIR/$item" ]; then
            cp -r "$SOURCE_DIR/$item" "$TARGET_DIR/"
            echo "  ✅ 已复制: $item"
        else
            echo "  ⚠️  跳过（不存在）: $item"
        fi
    done
    echo "✅ 目录包已生成: $TARGET_DIR/"
else
    # 从当前运行目录打包
    echo "📂 来源: 当前源码 ($UPGRADE_ROOT)"
    TEMP_DIR=$(mktemp -d)
    for item in src/hongjun requirements.txt SPEC.md README.md deploy; do
        if [ -e "$UPGRADE_ROOT/$item" ]; then
            cp -r "$UPGRADE_ROOT/$item" "$TEMP_DIR/"
            echo "  ✅ 已复制: $item"
        fi
    done

    # 打包
    cd "$TEMP_DIR"
    tar -czf "$TARGET_TAR" -- *
    cd - > /dev/null
    rm -rf "$TEMP_DIR"
    echo "✅ tar.gz 包已生成: $TARGET_TAR"
fi

echo ""
echo "📋 升级包放置位置："
echo "   $TARGET_TAR"
echo "   $TARGET_DIR/"
echo ""
echo "💡 下一步："
echo "   • 方式1（tar.gz）：直接将 v${VERSION}.tar.gz 放入上述路径即可"
echo "   • 方式2（目录）：将 v${VERSION}/ 目录放入上述路径即可"
echo "   • 方式3（HTTP）：在 $RELEASES_DIR/ 创建 v${VERSION}.url 文件，内容为下载 URL"
echo ""
echo "🧪 测试升级（dry run）："
echo "   curl -s http://localhost:20787/upgrade?version=${VERSION}\&dry_run=true"
