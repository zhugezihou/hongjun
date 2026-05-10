"""
天气查询 skill — wttr.in 免费 API，无需 API key
"""

import urllib.request
import urllib.parse
import urllib.error
import json


def _desc(val):
    """从 {'value': 'Sunny'} 或 'Sunny' 里提取描述文字"""
    if isinstance(val, list) and len(val) > 0:
        return val[0].get("value", "未知").strip()
    if isinstance(val, dict):
        return val.get("value", "未知").strip()
    return str(val).strip()


def get_weather(location: str = "Yiwu") -> str:
    """
    查询天气（使用 wttr.in 免费 API）
    Args:
        location: 地点（中文/英文），默认 Yiwu（义乌）
    Returns:
        格式化的天气预报字符串
    """
    location_map = {
        "义乌": "Yiwu",
        "杭州": "Hangzhou",
        "上海": "Shanghai",
        "北京": "Beijing",
        "深圳": "Shenzhen",
        "广州": "Guangzhou",
        "成都": "Chengdu",
    }
    query_location = location_map.get(location, location)

    try:
        url = f"https://wttr.in/{urllib.parse.quote(query_location)}?format=j1"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        current = (data.get("current_condition") or [{}])[0]
        weather_data = data.get("weather", [])

        # 当前天气
        temp_c = current.get("temp_C", "N/A")
        feels_like = current.get("FeelsLikeC", "N/A")
        humidity = current.get("humidity", "N/A")
        wind_speed = current.get("windspeedKmph", "N/A")
        wind_dir = current.get("winddir16Point", "N/A")
        uv_index = current.get("uvIndex", "N/A")
        current_desc = _desc(current.get("weatherDesc", "未知"))

        lines = [f"🌤️ **{location} 当前天气**"]
        lines.append(f"天气：{current_desc}")
        lines.append(f"气温：{temp_c}°C（体感 {feels_like}°C）")
        lines.append(f"湿度：{humidity}%")
        lines.append(f"风速：{wind_speed} km/h（{wind_dir}）")
        lines.append(f"UV指数：{uv_index}")

        # 今日预报（中午）
        if len(weather_data) >= 1:
            today = weather_data[0]
            max_temp = today.get("maxtempC", "N/A")
            min_temp = today.get("mintempC", "N/A")
            today_noon = (today.get("hourly") or [{}])[4]
            today_desc = _desc(today_noon.get("weatherDesc", []))
            lines.append(f"\n📅 **今天**（{today.get('date', '今天')}）")
            lines.append(f"天气：{today_desc}，最高 {max_temp}°C / 最低 {min_temp}°C")

        # 明天预报（中午）
        if len(weather_data) >= 2:
            tomorrow = weather_data[1]
            max_temp = tomorrow.get("maxtempC", "N/A")
            min_temp = tomorrow.get("mintempC", "N/A")
            tomorrow_noon = (tomorrow.get("hourly") or [{}])[4]
            tomorrow_desc = _desc(tomorrow_noon.get("weatherDesc", []))
            lines.append(f"\n📅 **明天**（{tomorrow.get('date', '明天')}）")
            lines.append(f"天气：{tomorrow_desc}，最高 {max_temp}°C / 最低 {min_temp}°C")

        # 后天
        if len(weather_data) >= 3:
            day3 = weather_data[2]
            max_temp = day3.get("maxtempC", "N/A")
            min_temp = day3.get("mintempC", "N/A")
            day3_noon = (day3.get("hourly") or [{}])[4]
            day3_desc = _desc(day3_noon.get("weatherDesc", []))
            lines.append(f"\n📅 **后天**（{day3.get('date', '后天')}）")
            lines.append(f"天气：{day3_desc}，最高 {max_temp}°C / 最低 {min_temp}°C")

        return "\n".join(lines)

    except urllib.error.HTTPError as e:
        return f"❌ 天气查询失败：HTTP {e.code}"
    except urllib.error.URLError as e:
        return f"❌ 网络错误：{e.reason}"
    except Exception as e:
        return f"❌ 天气查询异常：{str(e)}"


if __name__ == "__main__":
    print(get_weather("义乌"))
