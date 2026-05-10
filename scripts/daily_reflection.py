#!/usr/bin/env python3
"""
鸿钧 · 每日反思脚本
====================

每天 09:00 定时运行，执行全量复盘：
1. 加载 evolution_memory 中的近期经验
2. 运行反思引擎全面复盘
3. 更新经验模式权重
4. 推送反思摘要到朝堂群（如果有重大发现）

Usage:
    python daily_reflection.py              # 仅运行反思
    python daily_reflection.py --notify    # 反思并推送飞书
    python daily_reflection.py --dry-run   # 不写入，只输出结果
"""

import argparse
import sys
import time
from pathlib import Path

# 确保可以导入 hongjun
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hongjun.reflection_engine import get_reflection_engine
from hongjun.evolution_memory import EvolutionMemory
from hongjun.logging_config import get_logger

logger = get_logger("hongjun.daily_reflection")


def run_reflection(dry_run: bool = False) -> dict:
    """执行每日反思"""
    engine = get_reflection_engine()
    result = engine.daily_reflection(dry_run=dry_run)
    return result


def format_summary(result) -> str:
    """格式化反思摘要"""
    lines = ["🧠 **鸿钧每日反思报告**"]

    if hasattr(result, "summary"):
        lines.append(result.summary)

    # 经验统计
    try:
        mem = EvolutionMemory()
        stats = mem.get_stats()
        lines.append(f"\n📊 **经验统计**：")
        lines.append(f"  总任务：{stats.get('total_tasks', 0)}")
        lines.append(f"  成功率：{stats.get('success_rate', 0)}%")
        patterns = stats.get("top_patterns", [])
        if patterns:
            lines.append(f"  高频技能：{', '.join(p.get('keyword', '') for p in patterns[:3])}")
    except Exception:
        pass

    return "\n".join(lines)


def send_feishu(message: str):
    """推送飞书通知到朝堂群"""
    try:
        import httpx
        import json
        from pathlib import Path

        config_path = Path.home() / ".config" / "hongjun" / "config.yaml"
        if not config_path.exists():
            return
        import yaml
        config = yaml.safe_load(config_path.read_text())
        feishu = config.get("feishu", {})
        app_id = feishu.get("app_id", "")
        app_secret = feishu.get("app_secret", "")
        if not app_id or not app_secret:
            return

        # 获取 token
        token_resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        token = token_resp.json().get("tenant_access_token", "")

        # 发送到朝堂群
        httpx.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": "oc_d860f9f653e3421db6ea419a81414cf6",
                "msg_type": "text",
                "content": json.dumps({"text": message}),
            },
            timeout=10,
        )
    except Exception as e:
        logger.error(f"飞书通知失败: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="鸿钧每日反思")
    parser.add_argument("--notify", action="store_true", help="推送到飞书")
    parser.add_argument("--dry-run", action="store_true", help="不写入，只输出")
    args = parser.parse_args()

    print("🧠 开始每日反思...")
    result = run_reflection(dry_run=args.dry_run)
    summary = format_summary(result)
    print(summary)

    if args.notify:
        send_feishu(summary)

    if args.dry_run:
        print("\n（dry-run 模式，未写入任何更改）")
