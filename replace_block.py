#!/usr/bin/env python3
"""Replace the local-executor elif block in orchestrator.py"""

content = open('/home/asus/hongjun/src/hongjun/orchestrator.py').read()

# Find the elif line (with leading spaces)
target = '                elif best_skill.name == "local-executor":'
idx = content.find(target)
if idx < 0:
    print(f"❌ 未找到目标行")
    exit(1)
print(f"找到目标行于位置: {idx}")

# old_block 从 elif 这行开头开始
# 找上一个换行
line_start = content.rfind('\n', 0, idx) + 1  # +1 to include the newline char
old_block = content[line_start:]

# Find end: "if skill_result:" 
end_marker = '                if skill_result:'
end_idx = content.find(end_marker, idx)
if end_idx < 0:
    print(f"❌ 未找到结束标记")
    exit(1)
old_block = content[line_start:end_idx]
print(f"旧块长度: {len(old_block)} 字符")
print(f"旧块开头: {repr(old_block[:100])}")

new_block = '''                elif best_skill.name == "local-executor":
                    # ═══════════════════════════════════════════════════════
                    #  Intent-Guided Dispatch（替代纯关键词 fallback）
                    # ═══════════════════════════════════════════════════════
                    from .intent_classifier import classify_intent, intent_to_handler_key

                    req = state["user_request"]
                    funcs = best_skill.functions

                    # Step 1：LLM 意图分类
                    intent_info = classify_intent(req)
                    intent = intent_info["intent"]
                    confidence = intent_info["confidence"]
                    is_shell_safe = intent_info["is_shell_safe"]

                    # Step 2：低置信度 / 不安全 → 要求澄清，不执行 shell
                    if intent == "unclear" or (not is_shell_safe and confidence < 0.6):
                        skill_result = (
                            "🤖 我不确定您的意图，请更明确地说明您想要的操作类型：\\n"
                            "  • 执行 Git 操作 → 说「git commit/push/branch」\\n"
                            "  • 查看系统状态 → 说「系统状态」或「系统模块」\\n"
                            "  • 执行命令 → 说「运行 xxx 命令」\\n"
                            "  • 写代码 → 说「写代码：...」\\n"
                            "  • 搜索 → 说「搜索 xxx」"
                        )
                    else:
                        # Step 3：按意图路由到对应 handler
                        handler_key = intent_to_handler_key(intent)

                        if handler_key == "system_status":
                            parts = []
                            parts.append("=== Git ===")
                            parts.append(funcs["git_status"]())
                            parts.append("\\n=== Cron 调度器 ===")
                            parts.append(funcs["systemctl"](action="status", unit="cron.service"))
                            parts.append("\\n=== 进程列表(top 5) ===")
                            parts.append(funcs["process_list"]())
                            skill_result = "\\n".join(parts)

                        elif handler_key == "git_operation":
                            if "status" in req.lower() or "工作区" in req:
                                skill_result = funcs["git_status"]()
                            elif "commit" in req.lower() or "提交" in req:
                                m = re.search(r'["\u0027"](.+?)["\u0027"]', req.split("提交")[-1])
                                msg = m.group(1) if m else "更新"
                                skill_result = funcs["git_commit"](message=msg)
                            elif "push" in req.lower() or "推送" in req:
                                skill_result = funcs["git_push"]()
                            elif "branch" in req.lower() or "分支" in req:
                                m = re.search(r'[分支|-b]\\s*(\\S+)', req)
                                name = m.group(1) if m else "feature/new"
                                skill_result = funcs["git_branch"](name=name)
                            elif "checkout" in req.lower() or "切换" in req:
                                m = re.search(r'(?:checkout|切换)\\s+(\\S+)', req)
                                branch = m.group(1) if m else "main"
                                git_checkout = funcs.get("git_checkout")
                                skill_result = (git_checkout(branch=branch) if git_checkout
                                                else funcs["git_branch"](name=branch))
                            else:
                                skill_result = funcs["git_status"]()

                        elif handler_key == "shell_command":
                            warn = "⚠️ 低置信度命令执行：" if confidence < 0.7 else ""
                            cmd_m = re.search(r'["\u0027"](.+?)["\u0027"]', req)
                            if cmd_m:
                                skill_result = warn + funcs["run_command"](cmd=cmd_m.group(1))
                            else:
                                skill_result = warn + funcs["run_command"](cmd=req)

                        elif handler_key == "file_operation":
                            if any(k in req for k in ["写文件", "写入", "创建文件"]):
                                m = re.search(r'(/\S+)\\s*[:：]\\s*(.+)', req)
                                skill_result = (funcs["write_file"](path=m.group(1), content=m.group(2))
                                                if m else "[错误] 格式：/path/file.ext : 内容")
                            elif any(k in req for k in ["读文件", "读取文件", "cat "]):
                                m = re.search(r'(/\S+)', req)
                                skill_result = funcs["read_file"](path=m.group(1)) if m else "[错误] 格式：/path/file"
                            else:
                                parts = req.split()
                                skill_result = (funcs["read_file"](path=parts[-1]) if parts
                                                else "[错误] 格式：/path/file")

                        elif handler_key == "deploy":
                            m = re.search(r'([/\w_-]+\\.sh)', req)
                            skill_result = (funcs["deploy_script"](script_path=m.group(1)) if m
                                            else "[错误] 格式：/path/to/script.sh")

                        else:
                            # 意图识别但无对应 handler → 要求澄清
                            cmd_m = re.search(r'["\u0027"](.+?)["\u0027"]', req)
                            if cmd_m:
                                skill_result = funcs["run_command"](cmd=cmd_m.group(1))
                            else:
                                skill_result = (
                                    "🤖 我将这条消息识别为「" + intent + "」意图（置信度 " +
                                    str(round(confidence * 100)) + "%），"
                                    "但当前不支持自动执行。请明确说明您想要的操作。"
                                )

'''

if old_block in content:
    new_content = content.replace(old_block, new_block, 1)
    open('/home/asus/hongjun/src/hongjun/orchestrator.py', 'w').write(new_content)
    print("✅ 替换成功")
else:
    print("❌ 未找到目标代码块")
    print(f"OLD BLOCK ({len(old_block)} chars):")
    print(repr(old_block[:300]))
