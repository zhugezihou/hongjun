import re, json

def strip_thinking(text):
    """Remove <think>... content and extract final JSON answer"""
    # Remove all <think>... content
    text = re.sub(r'<think>[\s\S]*?</think>', '', text)
    return text

# Test with actual responses
responses = {
    '展示你的各项系统模块': '''<think>
用户要求我分类的消息是："展示你的各项系统模块"

让我分析这个消息：
- 用户要求"展示"系统模块
- 这类似于"查看系统状态"、"检查系统状态"
- 属于查看系统运行状态的请求

根据候选类别：
- system_status: 用户要求查看系统模块状态、健康检查、运行状态

这个消息最符合 system_status 类别。

置信度：1.0

---

```json
{"intent": "system_status", "confidence": 1.0}
```''',
    '搜索 Python 趋势': '''<think>
用户消息是"搜索 Python 趋势"，意图是搜索趋势信息。这是一个搜索意图。
</think>

```json
{"intent": "search", "confidence": 0.95}
```''',
    '帮我写一个快速排序': '''<think>
用户要求分类的消息是："帮我写一个快速排序"

让我分析这个消息：
- 用户要求编写快速排序算法
- 属于代码生成类别

```json
{"intent": "code_generation", "confidence": 0.95}
```''',
}

for msg, content in responses.items():
    cleaned = strip_thinking(content)
    print(f'=== {msg} ===')
    print(f'Cleaned: {cleaned[:300]!r}')
    
    # Try to find JSON block
    m = re.search(r'```json\s*(\{[^}]*"intent"[^}]*\})\s*```', cleaned)
    if m:
        try:
            parsed = json.loads(m.group(1))
            print(f'Parsed: {parsed}')
        except Exception as e:
            print(f'JSON match but failed: {e}, text={m.group(1)!r}')
    else:
        # Try bare JSON
        m2 = re.search(r'\{[^{}]*"intent"[^{}]*\}', cleaned)
        if m2:
            try:
                parsed = json.loads(m2.group())
                print(f'Parsed (bare): {parsed}')
            except:
                print(f'Bare JSON match but failed: {m2.group()!r}')
        else:
            print('No JSON found')
    print()
