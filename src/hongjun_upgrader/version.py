"""
鸿钧版本解析与比较工具
"""

import re
from typing import Tuple, Optional


def parse_version(v: str) -> Optional[Tuple[int, int, int]]:
    """
    解析版本字符串为 (major, minor, patch) 元组。
    支持格式：1.2.3, 1.2.3-beta.1, 1.2.3+build, 1.2.3-beta.1+build
    """
    pattern = r"^(\d+)\.(\d+)\.(\d+)"
    m = re.match(pattern, v.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def compare_version(a: str, b: str) -> int:
    """
    比较两个版本字符串。
    返回：-1 (a < b), 0 (a == b), 1 (a > b)
    """
    va = parse_version(a)
    vb = parse_version(b)
    if va is None or vb is None:
        raise ValueError(f"Invalid version format: {a!r} or {b!r}")
    if va < vb:
        return -1
    elif va > vb:
        return 1
    return 0


def bump_version(version: str, level: str) -> str:
    """
    将版本号按指定级别递增。

    level: 'major' | 'minor' | 'patch'
    '1.2.3' + 'major' = '2.0.0'
    '1.2.3' + 'minor' = '1.3.0'
    '1.2.3' + 'patch' = '1.2.4'
    """
    v = parse_version(version)
    if v is None:
        raise ValueError(f"Invalid version: {version}")
    major, minor, patch = v
    if level == "major":
        return f"{major + 1}.0.0"
    elif level == "minor":
        return f"{major}.{minor + 1}.0"
    elif level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError(f"Unknown bump level: {level}")
