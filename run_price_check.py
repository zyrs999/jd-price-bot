import os
import re
import time
import random
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# 读取配置+飞书Webhook
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
with open("config.txt", "r", encoding="utf-8") as f:
    SERVER_CONFIG = f.read().strip()

def parse_server_config(text):
    """解析服务器配置"""
    result = {}
    brand_model = re.search(r'(戴尔|DELL|R\d{2,3})', text, re.I)
    result['model'] = brand_model.group() if brand_model else ''
    cpu = re.search(r'至强\s*([Ee]-?\d+[^\s|丨]+)', text)
    result['cpu'] = cpu.group() if cpu else ''
    mem = re.search(r'(\d+G)', text)
    result['memory'] = mem.group() if mem else ''
    disk = re.search(r'(\d+\*\d+[GT])', text)
    result['disk'] = disk.group() if disk else ''
    power = re.search(r'(\d+W)', text)
    result['power'] = power.group() if power else ''
    result['search_keyword'] = ' '.join([v for k, v in result.items() if v])
    return result

def jd_search(keyword):
    """京东搜索（防限流）"""
    url = f"https://search.jd.com/Search"
    params = {'keyword': keyword, 'enc': 'utf-8', 'page': 1}
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36"
    ]
    headers = {'User-Agent': random.choice(user_agents), 'Cookie': os.getenv("JD_COOKIE", "")}
    try:
        time.sleep(random.uniform(1, 3))
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'lxml')
        items = []
        for item in soup.select('.gl-i-wrap')[:3]:
            title_elem = item.select_one('.p-name em')
            price_elem = item.select_one('.p-price')
            link_elem = item.select_one('.p-img a')
            if not (title_elem and price_elem and link_elem):
                continue
            title = title_elem.get_text(strip=True)
            price = price_elem.get_text(strip=True)
            link = 'https:' + link_elem['href'] if 'href' in link_elem.attrs else ''
            items.append({'title': title, 'price': price, 'url': link})
        return items
    except Exception as e:
        return [{'title': f'搜索失败：{str(e)}', 'price': '0', 'url': ''}]

def jd_screenshot(url):
    """截图（GitHub Actions兼容）"""
    save_path = "/tmp/screenshot.jpg"
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,2000")
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(url)
        time.sleep(3)
        driver.save_screenshot(save_path)
        driver.quit()
        return save_path
    except Exception as e:
        print(f"截图失败：{e}")
        return None

def send_feishu_msg(content, img_path=None):
    """推送飞书"""
    # 文本消息
    requests.post(FEISHU_WEBHOOK, json={
        "msg_type": "text",
        "content": {"text": content}
    })
    # 图片消息
    if img_path and os.path.exists(img_path):
        upload_resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Content-Type": "application/json"},
            json={"image_type": "message", "request_id": str(int(time.time()))}
        )
        if upload_resp.status_code == 200:
            upload_url = upload_resp.json()['data']['url']
            with open(img_path, 'rb') as f:
                requests.post(upload_url, files={'image': f})
            requests.post(FEISHU_WEBHOOK, json={
                "msg_type": "image",
                "content": {"image_key": upload_resp.json()['data']['image_key']}
            })

# 主逻辑
if __name__ == '__main__':
    # 解析配置
    config = parse_server_config(SERVER_CONFIG)
    # 京东搜索
    items = jd_search(config['search_keyword'])
    # 整理回复
    reply = f"【京东比价结果】\n搜索关键词：{config['search_keyword']}\n\n"
    for i, item in enumerate(items, 1):
        reply += f"商品{i}：\n标题：{item['title'][:50]}...\n价格：{item['price']}\n链接：{item['url']}\n\n"
    # 推送结果
    if items and items[0]['url']:
        screenshot_path = jd_screenshot(items[0]['url'])
        send_feishu_msg(reply, screenshot_path)
    else:
        send_feishu_msg(reply)
