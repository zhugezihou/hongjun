"""
鸿钧升级系统 (Hongjun Upgrader)
================================

设计原则：
  1. 核心进程（Core Process）绝对保护，升级不触及
  2. 修复进程（Repair Process）独立运行，与升级分离
  3. 大改（breaking）升大版本，小改升小版本，补丁升补丁
  4. 升级前自动备份，失败可回滚
  5. 用户可选择：升级 / 修复 / 回滚 / 卸载

版本规范（语义化版本）：
  MAJOR.MINOR.PATCH
  - MAJOR: 核心架构变更、破坏性变更（不兼容旧版协议/API）
  - MINOR: 新功能、非破坏性新增（六部新工具、新技能）
  - PATCH: Bug 修复、性能改进、文档更新

受保护目录（升级不触及）：
  data/       — 记忆数据库、用户数据
  config/     — 用户配置（hongjun.yaml）
  skills/     — 用户安装的技能
  upgrades/   — 升级管理（历史版本快照）
  .env        — 环境变量（如果存在）

可升级目录（每次升级替换）：
  src/hongjun/ — 核心源代码
  requirements.txt
  SPEC.md / README.md
  deploy/     — systemd 服务文件

修复进程：独立运行，不依赖主进程，不受升级影响
"""

__version__ = "1.0.0"

from .core import HongjunUpgrader
from .version import parse_version, compare_version

__all__ = ["HongjunUpgrader", "parse_version", "compare_version"]
