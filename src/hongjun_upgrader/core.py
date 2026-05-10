"""
鸿钧升级器 — HongjunUpgrader
=============================

职责：
  1. 版本检测与升级策略判定
  2. 升级前完整备份
  3. 原子性升级（失败回滚）
  4. 核心进程（Core Process）保护
  5. 修复 / 回滚 / 卸载
  6. 升级历史记录

保护 zones：
  data/       → 完全不动
  config/     → 合并保留（hongjun.yaml 用户配置）
  skills/     → 保留（用户技能）
  upgrades/   → 升级历史记录
  .env        → 保留
"""

import os
import re
import shutil
import tarfile
import json
import datetime
import subprocess
import hashlib
from pathlib import Path
from typing import Literal, Optional, List, Dict

from .version import parse_version, bump_version

# ============================================================
# 常量
# ============================================================

HONGJUN_ROOT = Path("/home/asus/hongjun")
UPGRADE_DIR = HONGJUN_ROOT / "upgrades"
PROTECTED_ZONES = {
    "data": HONGJUN_ROOT / "data",
    "config": HONGJUN_ROOT / "config",
    "skills": HONGJUN_ROOT / "skills",
    "upgrades": UPGRADE_DIR,
    ".env": HONGJUN_ROOT / ".env",
}
UPGRADABLE = [
    "src/hongjun",
    "requirements.txt",
    "SPEC.md",
    "README.md",
    "deploy",
]
CURRENT_VERSION_FILE = UPGRADE_DIR / "current_version.json"
CHANGELOG_FILE = UPGRADE_DIR / "changelog.md"
UPGRADE_LOG = UPGRADE_DIR / "upgrade.log"


# ============================================================
# 升级策略判定
# ============================================================

class VersionLevel:
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


def determine_upgrade_level(
    from_ver: str,
    to_ver: str,
) -> Literal["major", "minor", "patch"]:
    """判定从 from_ver 到 to_ver 是什么级别的升级。"""
    f = parse_version(from_ver)
    t = parse_version(to_ver)
    if f is None or t is None:
        raise ValueError(f"Invalid version: {from_ver} -> {to_ver}")
    if t[0] > f[0]:
        return "major"
    elif t[1] > f[1]:
        return "minor"
    elif t[2] > f[2]:
        return "patch"
    return "patch"


# ============================================================
# 备份与恢复
# ============================================================

def _calc_dir_md5(root: Path) -> Dict[str, str]:
    """计算目录下所有文件的 MD5（用于变更检测）。"""
    md5s = {}
    if not root.exists():
        return md5s
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root)
            try:
                md5s[str(rel)] = hashlib.md5(p.read_bytes()).hexdigest()
            except Exception:
                pass
    return md5s


def create_backup(version_label: str) -> Path:
    """
    创建完整备份，保存到 upgrades/<version_label>-backup-<timestamp>.tar.gz
    返回备份文件路径。
    """
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"backup-{version_label}-{ts}.tar.gz"
    backup_path = UPGRADE_DIR / backup_name

    UPGRADE_DIR.mkdir(parents=True, exist_ok=True)

    with tarfile.open(backup_path, "w:gz") as tar:
        # 备份可升级目录
        for item in UPGRADABLE:
            p = HONGJUN_ROOT / item
            if p.exists():
                tar.add(p, arcname=item)
        # 备份保护目录中的 config（用户配置）
        cfg = HONGJUN_ROOT / "config" / "hongjun.yaml"
        if cfg.exists():
            tar.add(cfg, arcname="config/hongjun.yaml")

    _log(f"[BACKUP] Created: {backup_path}")
    return backup_path


def restore_backup(backup_path: Path) -> bool:
    """从备份文件恢复到升级前状态。"""
    if not backup_path.exists():
        _log(f"[ERROR] Backup not found: {backup_path}")
        return False
    try:
        # 先清理当前可升级目录
        for item in UPGRADABLE:
            p = HONGJUN_ROOT / item
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
        # 解压备份
        with tarfile.open(backup_path, "r:gz") as tar:
            tar.extractall(HONGJUN_ROOT)
        _log(f"[RESTORE] Restored from: {backup_path}")
        return True
    except Exception as e:
        _log(f"[ERROR] Restore failed: {e}")
        return False


# ============================================================
# 升级日志
# ============================================================

def _log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    UPGRADE_DIR.mkdir(parents=True, exist_ok=True)
    with open(UPGRADE_DIR / "upgrade.log", "a") as f:
        f.write(line + "\n")


# ============================================================
# 代码替换引擎
# ============================================================

def _safe_replace_file(src: Path, dest: Path):
    """原子性替换：写临时文件再 rename，防止中途崩溃导致损坏。"""
    tmp = dest.parent / f"._{dest.name}.tmp"
    shutil.copy2(src, tmp)
    tmp.rename(dest)  # atomic on POSIX


def _apply_tarball_upgrade(tarball_path: Path) -> tuple[bool, str]:
    """
    从 tar.gz 包应用升级。
    包内结构应为：src/hongjun/... 或直接是 hongjun/... 文件。
    返回 (成功, 错误信息)。
    """
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            with tarfile.open(tarball_path, "r:gz") as tar:
                tar.extractall(tmpdir)

            # 查找源码根目录（兼容两种打包格式）
            src_root = None
            for candidate in [tmpdir / "src" / "hongjun", tmpdir / "hongjun"]:
                if candidate.exists() and candidate.is_dir():
                    src_root = candidate
                    break

            if src_root is None:
                return False, "tar.gz 内未找到 src/hongjun/ 目录"

            # 逐文件替换（只替换 UPGRADABLE 范围内的文件）
            replaced = []
            for item in UPGRADABLE:
                src_item = tmpdir / item
                dest_item = HONGJUN_ROOT / item
                if src_item.exists():
                    if dest_item.is_dir():
                        shutil.rmtree(dest_item)
                    elif dest_item.exists():
                        dest_item.unlink()
                    if src_item.is_dir():
                        shutil.copytree(src_item, dest_item)
                    else:
                        dest_item.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_item, dest_item)
                    replaced.append(item)

            # 处理 requirements.txt 等独立文件
            for item in ["requirements.txt", "SPEC.md", "README.md"]:
                src_file = tmpdir / item
                if src_file.exists():
                    dest_file = HONGJUN_ROOT / item
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)
                    replaced.append(item)

            _log(f"[APPLY] Replaced {len(replaced)} items: {replaced}")
            return True, ""
    except Exception as e:
        return False, str(e)


def _apply_directory_upgrade(source_dir: Path) -> tuple[bool, str]:
    """
    从本地目录应用升级（直接复制替换）。
    目录结构同 UPGRADABLE。
    返回 (成功, 错误信息)。
    """
    try:
        replaced = []
        for item in UPGRADABLE:
            src_item = source_dir / item
            dest_item = HONGJUN_ROOT / item
            if src_item.exists():
                if dest_item.is_dir():
                    shutil.rmtree(dest_item)
                elif dest_item.exists():
                    dest_item.unlink()
                if src_item.is_dir():
                    shutil.copytree(src_item, dest_item)
                else:
                    dest_item.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_item, dest_item)
                replaced.append(item)

        for item in ["requirements.txt", "SPEC.md", "README.md"]:
            src_file = source_dir / item
            if src_file.exists():
                dest_file = HONGJUN_ROOT / item
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                replaced.append(item)

        _log(f"[APPLY] Replaced {len(replaced)} items from directory: {replaced}")
        return True, ""
    except Exception as e:
        return False, str(e)


# ============================================================
# 升级执行
# ============================================================

class UpgradeResult:
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    NO_OP = "no_op"


class HongjunUpgrader:
    """
    鸿钧升级器。

    使用方式：
        upgrader = HongjunUpgrader()
        result = upgrader.upgrade(target_version="1.1.0", changelog="...")
        result = upgrader.repair()
        result = upgrader.rollback()
        result = upgrader.uninstall()
        info = upgrader.status()
    """

    def __init__(self, hongjun_root: Path = HONGJUN_ROOT):
        self.root = hongjun_root
        self.upgrade_dir = UPGRADE_DIR
        self.protected = PROTECTED_ZONES

    # ----------------------------------------------------------
    # 状态查询
    # ----------------------------------------------------------

    def get_current_version(self) -> Optional[str]:
        """读取当前版本号（来自 __init__.py 或 current_version.json）。"""
        # 优先读 current_version.json
        if CURRENT_VERSION_FILE.exists():
            try:
                d = json.loads(CURRENT_VERSION_FILE.read_text())
                return d.get("version")
            except Exception:
                pass
        # 回退：从源码读
        init = self.root / "src" / "hongjun" / "__init__.py"
        if init.exists():
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init.read_text())
            if m:
                return m.group(1)
        return None

    def get_installed_version(self) -> Optional[str]:
        """读取已安装/最新可用版本（来自源码）。"""
        return self.get_current_version()

    def status(self) -> dict:
        """返回完整状态信息。"""
        current = self.get_current_version()
        changelog = self._read_changelog()
        last_upgrade = self._last_upgrade_info()
        backups = sorted(self._list_backups())

        return {
            "current_version": current,
            "protected_zones": {k: str(v) for k, v in self.protected.items()},
            "upgradable_dirs": UPGRADABLE,
            "last_upgrade": last_upgrade,
            "available_backups": [str(b) for b in backups],
            "changelog": changelog,
        }

    def _read_changelog(self) -> str:
        if CHANGELOG_FILE.exists():
            return CHANGELOG_FILE.read_text()
        return ""

    def _list_backups(self) -> List[Path]:
        if not UPGRADE_DIR.exists():
            return []
        return sorted(
            UPGRADE_DIR.glob("backup-*.tar.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    def _last_upgrade_info(self) -> Optional[dict]:
        log = UPGRADE_DIR / "upgrade.log"
        if not log.exists():
            return None
        lines = log.read_text().strip().split("\n")
        if not lines:
            return None
        last = lines[-1]
        m = re.match(r"\[(.{19})\] \[(\w+)\] (.+)", last)
        if m:
            return {"time": m.group(1), "action": m.group(2), "detail": m.group(3)}
        return {"raw": last}

    # ----------------------------------------------------------
    # 核心升级
    # ----------------------------------------------------------

    def pre_upgrade_check(self) -> dict:
        """
        升级前检查。
        返回 {healthy: bool, issues: list, warnings: list}
        """
        issues = []
        warnings = []

        # 检查核心目录是否存在
        core_src = self.root / "src" / "hongjun"
        if not core_src.exists():
            issues.append(f"核心源码目录不存在: {core_src}")

        # 检查受保护目录可写
        for name, path in self.protected.items():
            if name == "upgrades":
                continue  # upgrades 自己
            if path.exists() and not os.access(path, os.W_OK):
                issues.append(f"受保护目录不可写: {name} ({path})")

        # 检查磁盘空间（需要至少 200MB 可用）
        try:
            stat = shutil.disk_usage(self.root)
            if stat.free < 200 * 1024 * 1024:
                issues.append(f"磁盘空间不足: {stat.free // (1024*1024)}MB 可用，需要 200MB")
        except Exception as e:
            warnings.append(f"无法检查磁盘空间: {e}")

        # 检查 Python 环境
        try:
            subprocess.run(
                ["python3", "-c", "import sys; sys.exit(0)"],
                capture_output=True, check=True,
            )
        except Exception:
            issues.append("Python3 不可用")

        return {"healthy": len(issues) == 0, "issues": issues, "warnings": warnings}

    def upgrade(
        self,
        target_version: Optional[str] = None,
        changelog: str = "",
        bump: Optional[Literal["major", "minor", "patch"]] = None,
        dry_run: bool = False,
    ) -> dict:
        """
        执行升级。

        参数：
          target_version: 目标版本（如 "1.3.0"），若为 None 则自动 bump
          changelog: 本次升级的变更说明
          bump: 手动指定升级级别（major/minor/patch），优先级高于 target_version
          dry_run: True 则只检查不执行

        返回：
          {success: bool, result: str, version: str, detail: str}
        """
        _log("=" * 50)
        _log(f"[UPGRADE] Start — target={target_version or 'bump:'+(bump or 'auto')}")

        # 前置检查
        check = self.pre_upgrade_check()
        if not check["healthy"]:
            _log(f"[UPGRADE] Failed pre-check: {check['issues']}")
            return {
                "success": False,
                "result": UpgradeResult.FAILED,
                "detail": f"前置检查失败: {check['issues']}",
            }

        current = self.get_current_version() or "0.0.0"

        # 确定目标版本
        if bump:
            target = bump_version(current, bump)
        elif target_version:
            target = target_version
        else:
            target = bump_version(current, "patch")  # 默认 patch

        level = determine_upgrade_level(current, target)
        _log(f"[UPGRADE] {current} -> {target} ({level})")

        if dry_run:
            _log("[UPGRADE] Dry run complete (no changes made)")
            return {
                "success": True,
                "result": UpgradeResult.NO_OP,
                "version": target,
                "level": level,
                "detail": "Dry run",
            }

        # 创建备份
        backup_label = f"v{current}"
        backup_path = create_backup(backup_label)
        _log(f"[UPGRADE] Backup: {backup_path}")

        # 执行代码替换（支持三种来源，依次尝试）
        fetch_ok = False
        fetch_error = ""

        # 优先级 1：本地 tar.gz 包（upgrades/releases/vX.Y.Z.tar.gz）
        local_tarball = UPGRADE_DIR / "releases" / f"v{target}.tar.gz"
        if local_tarball.exists():
            _log(f"[UPGRADE] Found local package: {local_tarball}")
            fetch_ok, fetch_error = _apply_tarball_upgrade(local_tarball)
        else:
            # 优先级 2：本地目录（upgrades/releases/vX.Y.Z/）
            local_dir = UPGRADE_DIR / "releases" / f"v{target}"
            if local_dir.exists():
                _log(f"[UPGRADE] Found local directory: {local_dir}")
                fetch_ok, fetch_error = _apply_directory_upgrade(local_dir)
            else:
                # 优先级 3：HTTP URL（支持 GitHub Releases / 自建 HTTP 服务器）
                # 格式：upgrades/releases/vX.Y.Z.url 文件，内容为下载 URL
                url_marker = UPGRADE_DIR / "releases" / f"v{target}.url"
                if url_marker.exists():
                    download_url = url_marker.read_text().strip()
                    _log(f"[UPGRADE] Downloading from: {download_url}")
                    tmp_tarball = UPGRADE_DIR / f"_download_v{target}.tar.gz"
                    try:
                        subprocess.run(
                            ["curl", "-fsSL", "-o", str(tmp_tarball), download_url],
                            capture_output=True, check=True,
                        )
                        fetch_ok, fetch_error = _apply_tarball_upgrade(tmp_tarball)
                        try:
                            tmp_tarball.unlink()
                        except Exception:
                            pass
                    except subprocess.CalledProcessError as e:
                        fetch_error = f"下载失败: {e.stderr.decode() if e.stderr else str(e)}"
                else:
                    fetch_error = (
                        f"未找到升级源。请将 v{target} 的代码包放入以下任一位置：\n"
                        f"  1. {local_tarball}  （tar.gz 打包）\n"
                        f"  2. {local_dir}/  （目录）\n"
                        f"  3. {url_marker}  （文件内容为下载 URL）"
                    )

        if not fetch_ok:
            _log(f"[UPGRADE] Fetch failed: {fetch_error}")
            _log("[UPGRADE] Restoring backup (upgrade failed)")
            restore_backup(backup_path)
            return {
                "success": False,
                "result": UpgradeResult.ROLLED_BACK,
                "detail": f"升级失败，已回滚。错误：{fetch_error}",
            }

        _log("[UPGRADE] Source applied successfully")

        # 更新当前版本记录
        UPGRADE_DIR.mkdir(parents=True, exist_ok=True)
        CURRENT_VERSION_FILE.write_text(
            json.dumps(
                {
                    "version": target,
                    "upgraded_at": datetime.datetime.now().isoformat(),
                    "previous": current,
                    "level": level,
                    "changelog": changelog,
                    "backup": str(backup_path),
                },
                indent=2,
                ensure_ascii=False,
            )
        )

        # 更新 changelog
        self._append_changelog(target, level, changelog)

        # 重启服务（如有 systemd）
        self._restart_services()

        _log(f"[UPGRADE] Success: {current} -> {target} ({level})")
        return {
            "success": True,
            "result": UpgradeResult.SUCCESS,
            "version": target,
            "level": level,
            "backup": str(backup_path),
            "detail": f"已从 v{current} 升级到 v{target}（{level}）",
        }

    # ----------------------------------------------------------
    # 修复（Repair Process）
    # ----------------------------------------------------------

    def repair(self) -> dict:
        """
        修复进程：独立于升级运行。
        检查核心文件完整性，尝试自动修复，重启服务。
        """
        _log("[REPAIR] Starting repair process...")
        repaired = []
        failed = []
        warnings = []

        # 检查核心文件完整性
        core_files = [
            "src/hongjun/__init__.py",
            "src/hongjun/orchestrator.py",
            "src/hongjun/memory.py",
            "src/hongjun/tools.py",
            "src/hongjun/executor.py",
        ]

        for rel in core_files:
            p = self.root / rel
            if not p.exists():
                # 尝试从最新备份恢复
                backups = self._list_backups()
                if backups:
                    _log(f"[REPAIR] Missing {rel}, attempting restore from backup...")
                    ok = self._extract_file_from_backup(rel, backups[0])
                    if ok:
                        repaired.append(rel)
                    else:
                        failed.append(rel)
                else:
                    failed.append(f"{rel} (no backup available)")

        # 检查受保护目录
        for name, path in self.protected.items():
            if name == "upgrades":
                continue
            if not path.exists():
                _log(f"[REPAIR] Warning: protected zone '{name}' missing at {path}")
                warnings.append(name)  # 将在返回中体现

        # 重启服务
        restart_ok = self._restart_services()

        result = "repaired" if not failed else "partial"
        _log(f"[REPAIR] Complete: {result}, repaired={len(repaired)}, failed={len(failed)}")

        return {
            "success": len(failed) == 0,
            "result": result,
            "repaired": repaired,
            "failed": failed,
            "services_restarted": restart_ok,
            "detail": f"修复 {'成功' if not failed else '部分成功'}，重启{'成功' if restart_ok else '失败'}",
        }

    def _extract_file_from_backup(self, rel_path: str, backup_path: Path) -> bool:
        """从备份包中提取单个文件。"""
        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                # 列出所有成员
                for member in tar.getmembers():
                    if member.name == rel_path or member.name.endswith(rel_path):
                        # 解压到临时位置再移动
                        tmp = UPGRADE_DIR / f"_repair_{Path(rel_path).name}"
                        tar.extract(member, UPGRADE_DIR)
                        dest = self.root / rel_path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(UPGRADE_DIR / member.name), str(dest))
                        _log(f"[REPAIR] Restored: {rel_path}")
                        return True
            return False
        except Exception as e:
            _log(f"[REPAIR] Extract failed: {e}")
            return False

    # ----------------------------------------------------------
    # 回滚（Rollback）
    # ----------------------------------------------------------

    def rollback(self, backup_path: Optional[Path] = None) -> dict:
        """
        回滚到上一个备份。
        如果 backup_path 为 None，自动找最新备份。
        """
        if backup_path is None:
            backups = self._list_backups()
            if not backups:
                return {"success": False, "result": "no_backup", "detail": "没有可用备份"}
            backup_path = backups[0]

        _log(f"[ROLLBACK] Rolling back to: {backup_path}")
        ok = restore_backup(backup_path)
        if ok:
            self._restart_services()
            v = self.get_current_version()
            _log(f"[ROLLBACK] Success, now at v{v}")
            return {
                "success": True,
                "result": UpgradeResult.ROLLED_BACK,
                "version": v,
                "detail": f"已回滚到 v{v}",
            }
        return {"success": False, "result": UpgradeResult.FAILED, "detail": "回滚失败"}

    # ----------------------------------------------------------
    # 卸载（Uninstall）
    # ----------------------------------------------------------

    def uninstall(self, keep_data: bool = True) -> dict:
        """
        卸载鸿钧（保留用户数据或一并删除）。
        """
        _log(f"[UNINSTALL] Starting uninstall (keep_data={keep_data})...")

        # 停止服务
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", "hongjun.service"],
                capture_output=True,
            )
            subprocess.run(
                ["systemctl", "--user", "stop", "hongjun-gateway.service"],
                capture_output=True,
            )
            _log("[UNINSTALL] Services stopped")
        except Exception as e:
            _log(f"[UNINSTALL] Warning: could not stop services: {e}")

        # 删除可升级目录
        for item in UPGRADABLE:
            p = self.root / item
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                _log(f"[UNINSTALL] Removed: {item}")

        # 删除或保留用户数据
        if not keep_data:
            for name in ["data", "skills", "config", ".env"]:
                p = HONGJUN_ROOT / name
                if p.exists():
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
                    _log(f"[UNINSTALL] Removed data: {name}")

        _log("[UNINSTALL] Complete")
        return {
            "success": True,
            "result": "uninstalled",
            "detail": f"卸载{'（保留用户数据）' if keep_data else '（已删除所有数据）'}",
        }

    # ----------------------------------------------------------
    # 内部工具
    # ----------------------------------------------------------

    def _append_changelog(self, version: str, level: str, changelog: str):
        """追加 changelog。"""
        ts = datetime.datetime.now().strftime("%Y-%m-%d")
        entry = f"\n## v{version} ({ts}) [{level.upper()}]\n\n{changelog}\n"
        if CHANGELOG_FILE.exists():
            old = CHANGELOG_FILE.read_text()
        else:
            old = "# 鸿钧升级日志\n"
        CHANGELOG_FILE.write_text(old + entry)

    def _restart_services(self) -> bool:
        """重启鸿钧相关 systemd 服务。"""
        services = ["hongjun.service", "hongjun-gateway.service"]
        all_ok = True
        for svc in services:
            try:
                r = subprocess.run(
                    ["systemctl", "--user", "restart", svc],
                    capture_output=True,
                )
                if r.returncode != 0:
                    _log(f"[WARN] Failed to restart {svc}: {r.stderr.decode()}")
                    all_ok = False
                else:
                    _log(f"[RESTART] {svc} restarted")
            except FileNotFoundError:
                _log("[WARN] systemctl not available")
                all_ok = False
                break
            except Exception as e:
                _log(f"[WARN] Restart {svc} error: {e}")
                all_ok = False
        return all_ok
