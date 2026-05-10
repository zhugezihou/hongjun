#!/usr/bin/env python3
"""
鸿钧 · 每周自我改进分析脚本
每周日凌晨 2:00 运行，分析自身代码改进机会，推送飞书报告。
"""
import sys
import json

sys.path.insert(0, "/home/asus/hongjun/src")

from hongjun.self_improver import SelfImprover


def notify_feishu(message: str) -> bool:
    try:
        import httpx
        # 获取 tenant_access_token（POST application/json）
        token_resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            headers={"Content-Type": "application/json"},
            content=json.dumps({
                "app_id": "cli_a973462926741cba",
                "app_secret": "***REMOVED***",
            }),
            timeout=10,
        )
        token = token_resp.json().get("tenant_access_token", "")
        if not token:
            print("❌ 获取飞书 token 失败")
            return False

        resp = httpx.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            content=json.dumps({
                "receive_id": "oc_d860f9f653e3421db6ea419a81414cf6",
                "msg_type": "text",
                "content": {"text": message},
            }),
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"❌ 飞书推送失败: {e}")
        return False


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
    notify_feishu(f"[鸿钧自我改进报告]\n\n{message}")


if __name__ == "__main__":
    main()
