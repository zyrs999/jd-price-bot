import os
import re
import time
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from flask import Flask, request, jsonify

app = Flask(__name__)

# 飞书Webhook（从GitHub Secrets读取）
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

def parse_server_config(text):
    """解析服务器配置，提取核心关键词"""
    result = {}
    # 品牌+型号
    brand_model = re.search(r'(戴尔|DELL|R\d{2,3})', text, re.I)
    result['model'] = brand_model.group() if brand_model else ''
    # CPU
    cpu = re.search(r'至强\s*([Ee]-?\d+[^\s|丨]+)', text)
    result['cpu'] = cpu.group() if cpu else ''
    # 内存
    mem = re.search(r'(\d+G)', text)
    result['memory'] = mem.group() if mem else ''
    # 硬盘
    disk = re.search(r'(\d+\*\d+[GT])', text)
    result['disk'] = disk.group() if disk else ''
    # 功率
    power = re.search(r'(\d+W)', text)
    result['power'] = power.group() if power else ''
    # 搜索关键词
    kw_parts = [v for k, v in result.items() if v]
    result['search_keyword'] = ' '.join(kw_parts)
    return result

def jd_search(keyword):
    """京东搜索，返回前3个匹配商品"""
    url = f"https://search.jd.com/Search"
    params = {
        'keyword': keyword,
        'enc': 'utf-8',
        'page': 1
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Cookie': os.getenv("JD_COOKIE", "")  # 可选，避免限流
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'lxml')
        items = []
        # 提取商品信息
        for item in soup.select('.gl-i-wrap')[:3]:
            title_elem = item.select_one('.p-name em')
            price_elem = item.select_one('.p-price')
            link_elem = item.select_one('.p-img a')
            if not (title_elem and price_elem and link_elem):
                continue
            title = title_elem.get_text(strip=True)
            price = price_elem.get_text(strip=True)
            link = 'https:' + link_elem['href'] if 'href' in link_elem.attrs else ''
            items.append({
                'title': title,
                'price': price,
                'url': link
            })
        return items
    except Exception as e:
        return [{'title': f'搜索失败：{str(e)}', 'price': '0', 'url': ''}]

def jd_screenshot(url, save_path="/tmp/screenshot.jpg"):
    """商品页截图（GitHub Actions兼容路径）"""
    options = webdriver.ChromeOptions()
    # GitHub Actions 无头模式配置
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,2000")
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(url)
        time.sleep(3)  # 等待页面加载
        driver.save_screenshot(save_path)
        driver.quit()
        return save_path
    except Exception as e:
        print(f"截图失败：{e}")
        return None

def send_feishu_msg(content, img_path=None):
    """推送结果到飞书（支持文本+图片）"""
    # 文本消息
    text_data = {
        "msg_type": "text",
        "content": {
            "text": content
        }
    }
    requests.post(FEISHU_WEBHOOK, json=text_data)
    
    # 图片消息（如果有截图）
    if img_path and os.path.exists(img_path):
        # 第一步：获取图片上传链接
        upload_resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Content-Type": "application/json"},
            json={
                "image_type": "message",
                "request_id": str(int(time.time()))
            }
        )
        if upload_resp.status_code == 200:
            upload_url = upload_resp.json()['data']['url']
            # 第二步：上传图片
            with open(img_path, 'rb') as f:
                requests.post(upload_url, files={'image': f})
                # 第三步：发送图片
                img_data = {
                    "msg_type": "image",
                    "content": {
                        "image_key": upload_resp.json()['data']['image_key']
                    }
                }
                requests.post(FEISHU_WEBHOOK, json=img_data)

@app.route('/webhook', methods=['POST'])
def feishu_webhook():
    """接收飞书消息的Webhook入口"""
    data = request.get_json()
    # 提取飞书发送的配置文本
    if 'event' in data and data['event']['type'] == 'message_received':
        text = data['event']['message']['content']
        # 解析配置
        config = parse_server_config(text)
        # 京东搜索
        items = jd_search(config['search_keyword'])
        # 整理回复内容
        reply = f"【京东比价结果】\n搜索关键词：{config['search_keyword']}\n\n"
        for i, item in enumerate(items, 1):
            reply += f"商品{i}：\n标题：{item['title'][:50]}...\n价格：{item['price']}\n链接：{item['url']}\n\n"
        # 截图（取第一个商品）
        if items and items[0]['url']:
            screenshot_path = jd_screenshot(items[0]['url'])
            send_feishu_msg(reply, screenshot_path)
        else:
            send_feishu_msg(reply)
    return jsonify({"code": 0})

if __name__ == '__main__':
    # 本地测试用，GitHub Actions用不到
    app.run(host='0.0.0.0', port=5000)
