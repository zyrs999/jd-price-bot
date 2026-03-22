import os
import re
import time
import random
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# 读取配置+飞书Webhook
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
try:
    with open("config.txt", "r", encoding="utf-8") as f:
        SERVER_CONFIG = f.read().strip()
except Exception as e:
    SERVER_CONFIG = ""
    print(f"读取配置文件失败：{str(e)}")

def parse_server_config(text):
    """鲁棒的配置解析"""
    result = {}
    # 品牌+型号
    brand_model = re.search(r'(戴尔|DELL|Dell)\s*[-/]*\s*(R\d{3})|(R\d{3})', text, re.I)
    if brand_model:
        result['model'] = brand_model.group(2) or brand_model.group(3) or brand_model.group(1)
    else:
        result['model'] = ''
    
    # CPU
    cpu = re.search(r'至强\s*([Ee][- ]*\d+[^\s|丨]+)|([Ee][- ]*\d+)', text)
    result['cpu'] = cpu.group().replace(' ', '') if cpu else ''
    
    # 内存
    mem = re.search(r'(\d+[Gg][Bb]?)', text)
    result['memory'] = mem.group().upper() if mem else ''
    
    # 硬盘
    disk = re.search(r'(\d+\s*\*\s*\d+[GTgt])|(\d+[GTgt]\s*\*\s*\d+)', text)
    result['disk'] = disk.group().replace(' ', '').upper() if disk else ''
    
    # 功率
    power = re.search(r'(\d+[Ww]|(\d+)瓦)', text)
    if power:
        result['power'] = f"{power.group(2) if power.group(2) else power.group(1).replace('w','W').replace('W','')}W"
    else:
        result['power'] = ''
    
    # 搜索关键词兜底
    kw_parts = [v.strip() for k, v in result.items() if v and v.strip()]
    result['search_keyword'] = ' '.join(kw_parts) if kw_parts else text
    print(f"【调试】配置解析结果：{result}")
    return result

def jd_search_api(keyword):
    """使用京东API搜索（避开反爬）"""
    # 稳定的京东搜索API（无需Cookie）
    url = "https://api.m.jd.com/client.action"
    params = {
        'functionId': 'search',
        'client': 'wh5',
        'clientVersion': '1.0.0',
        'keyword': keyword,
        'page': 1,
        'pagesize': 3,
        'timestamp': int(time.time() * 1000),
        'uuid': f"{random.randint(10000000, 99999999)}"
    }
    
    headers = {
        'User-Agent': 'jdapp;iPhone;9.4.4;14.0;network/4g;Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148;supportJDSHWK/1',
        'Referer': 'https://search.jd.com/',
        'Accept': 'application/json, text/plain, */*'
    }
    
    try:
        time.sleep(random.uniform(1, 2))
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp_json = resp.json()
        
        # 解析API返回的商品数据
        items = []
        if 'wareInfo' in resp_json and resp_json['wareInfo']:
            for product in resp_json['wareInfo'][:3]:
                title = product.get('wname', '').strip()
                price = f"¥{product.get('jdPrice', '0')}"
                sku = product.get('wareId', '')
                link = f"https://item.jd.com/{sku}.html"
                
                items.append({
                    'title': title[:80],
                    'price': price if price != '¥0' else '¥暂无价格',
                    'url': link
                })
        
        print(f"【调试】API搜索到商品：{items}")
        return items if items else [{'title': '未找到匹配商品', 'price': '0', 'url': ''}]
    
    except Exception as e:
        error_msg = f"API搜索失败：{str(e)}"
        print(f"【调试】API异常：{error_msg}")
        return [{'title': error_msg, 'price': '0', 'url': ''}]

def jd_screenshot(url):
    """商品截图"""
    save_path = "/tmp/screenshot.jpg"
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.set_page_load_timeout(20)
        driver.get(url)
        time.sleep(5)
        driver.save_screenshot(save_path)
        driver.quit()
        return save_path
    except Exception as e:
        print(f"截图失败：{str(e)}")
        return None

def send_feishu_msg(content, img_path=None):
    """推送飞书"""
    if not FEISHU_WEBHOOK:
        print("飞书Webhook未配置")
        return
    
    # 文本消息
    text_data = {
        "msg_type": "text",
        "content": {"text": content}
    }
    try:
        requests.post(FEISHU_WEBHOOK, json=text_data, timeout=10)
    except Exception as e:
        print(f"飞书文本推送失败：{e}")
    
    # 图片消息
    if img_path and os.path.exists(img_path):
        try:
            upload_resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Content-Type": "application/json"},
                json={"image_type": "message", "request_id": str(int(time.time()))},
                timeout=10
            )
            if upload_resp.status_code == 200:
                upload_url = upload_resp.json()['data']['url']
                image_key = upload_resp.json()['data']['image_key']
                with open(img_path, 'rb') as f:
                    requests.post(upload_url, files={'image': f}, timeout=10)
                requests.post(FEISHU_WEBHOOK, json={
                    "msg_type": "image",
                    "content": {"image_key": image_key}
                })
        except Exception as e:
            print(f"飞书图片推送失败：{e}")

# 主逻辑
if __name__ == '__main__':
    print(f"【调试】原始配置：{SERVER_CONFIG}")
    
    # 解析配置
    config = parse_server_config(SERVER_CONFIG)
    if not config['search_keyword']:
        send_feishu_msg("【京东比价结果】\n配置解析失败，请检查输入格式！")
        exit(1)
    
    # 使用API搜索（核心修改）
    items = jd_search_api(config['search_keyword'])
    
    # 整理回复
    reply = f"【京东比价结果】\n搜索关键词：{config['search_keyword']}\n\n"
    for i, item in enumerate(items, 1):
        reply += f"商品{i}：\n标题：{item['title']}\n价格：{item['price']}\n链接：{item['url']}\n\n"
    
    print(f"【调试】回复内容：{reply}")
    
    # 推送飞书
    screenshot_path = None
    if items and items[0]['url'] and items[0]['price'] != '0':
        screenshot_path = jd_screenshot(items[0]['url'])
    
    send_feishu_msg(reply, screenshot_path)
    print("比价任务完成！")
