#!/usr/bin/env python3
"""
鸿钧 · 每周自我改进分析脚本
每周日凌晨 2:00 运行，分析自身代码改进机会，推送飞书报告。
"""
import sys
from pathlib import Path

# 确保可以导入 hongjun
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hongjun.self_improver import SelfImprover
from hongjun.feishu_client import FeishuClient, GROUP_CHAT_ID, APP_ID as _app_id, APP_SECRET as _app_secret


def main():
    improver = SelfImprover()
    suggestions = improver.analyze()

    stats = improver.get_stats()
    print(f"[鸿钧自我改进] 分析完成，共 {len(suggestions)} 条建议")
    print(f"历史改进: {stats['applied']} 次，通过率 {stats['pass_rate']:.0%}")

    if suggestions:
        lines = [f"📋 自我改进建议 ({len(suggestions)} 条):\n"]
        for s in suggestions[:8]:
            pri = "🔴" if s.priority == 1 else "🟡" if s.priority == 2 else "🟢"
            lines.append(f"{pri} [{s.module}] {s.description[:60]}")
            lines.append(f"   类型:{s.improvement_type} | 置信度:{s.confidence:.0%} | 工作量:{s.effort}\n")
        message = "".join(lines)
    else:
        message = "✅ 本周无重大改进建议，系统状态良好"

    print(message)

    # 飞书推送
    import asyncio
    async def _notify():
        client = FeishuClient(_app_id, _app_secret)
        try:
            result = await client.send_text_to_chat(GROUP_CHAT_ID, f"[鸿钧自我改进报告]\n\n{message}")
            if result.get("code") == 0:
                print("✅ 飞书推送成功")
            else:
                print(f"❌ 飞书推送失败: {result}")
        except Exception as e:
            print(f"❌ 飞书推送异常: {e}")
        finally:
            await client.close()

    asyncio.run(_notify())


if __name__ == "__main__":
    main()
