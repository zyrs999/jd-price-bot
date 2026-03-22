import os
import re
import time
import random
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# 加载环境变量（敏感信息放.env文件，不写死在代码里）
load_dotenv()
app = Flask(__name__)

# 飞书Webhook（从.env文件读取）
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

def parse_server_config(text):
    """鲁棒的服务器配置解析"""
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

def jd_search(keyword):
    """本地版京东搜索（避开反爬，适配家用IP）"""
    url = f"https://search.jd.com/Search"
    params = {
        'keyword': keyword,
        'enc': 'utf-8',
        'page': 1,
        'stock': 1
    }
    
    # 家用IP友好的User-Agent
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0"
    ]
    
    headers = {
        'User-Agent': random.choice(user_agents),
        'Cookie': os.getenv("JD_COOKIE", ""),  # 可选，填了更稳定
        'Referer': 'https://www.jd.com/',
        'Accept-Language': 'zh-CN,zh;q=0.9'
    }
    
    try:
        time.sleep(random.uniform(1, 2))
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'lxml')
        
        # 兼容京东商品容器
        items = []
        product_items = soup.select('.gl-item') or soup.select('.gl-i-wrap')
        print(f"【调试】找到商品数量：{len(product_items)}")
        
        for item in product_items[:3]:
            price_elem = item.select_one('.p-price i') or item.select_one('.p-price strong')
            title_elem = item.select_one('.p-name em') or item.select_one('.p-name a')
            link_elem = item.select_one('.p-img a')
            
            if not (title_elem and price_elem and link_elem):
                continue
            
            title = title_elem.get_text(strip=True).replace('<br/>', ' ')
            price = "¥" + price_elem.get_text(strip=True)
            link = 'https:' + link_elem['href'] if 'href' in link_elem.attrs else ''
            
            items.append({
                'title': title[:80],
                'price': price,
                'url': link
            })
        
        return items if items else [{'title': '未找到匹配商品', 'price': '0', 'url': ''}]
    
    except Exception as e:
        error_msg = f"搜索失败：{str(e)}"
        print(f"【调试】搜索异常：{error_msg}")
        return [{'title': error_msg, 'price': '0', 'url': ''}]

def jd_screenshot(url):
    """本地商品截图"""
    save_path = "screenshot.jpg"
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.set_page_load_timeout(20)
        driver.get(url)
        time.sleep(3)
        driver.save_screenshot(save_path)
        driver.quit()
        return save_path
    except Exception as e:
        print(f"【调试】截图失败：{str(e)}")
        return None

def send_feishu_msg(content, img_path=None):
    """推送飞书消息"""
    if not FEISHU_WEBHOOK:
        print("【调试】飞书Webhook未配置！")
        return
    
    # 发送文本
    text_data = {
        "msg_type": "text",
        "content": {"text": content}
    }
    try:
        requests.post(FEISHU_WEBHOOK, json=text_data, timeout=10)
        print("【调试】飞书文本推送成功")
    except Exception as e:
        print(f"【调试】飞书文本推送失败：{e}")
    
    # 发送图片
    if img_path and os.path.exists(img_path):
        try:
            # 获取上传链接
            upload_resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Content-Type": "application/json"},
                json={"image_type": "message", "request_id": str(int(time.time()))},
                timeout=10
            )
            if upload_resp.status_code == 200:
                upload_url = upload_resp.json()['data']['url']
                image_key = upload_resp.json()['data']['image_key']
                
                # 上传图片
                with open(img_path, 'rb') as f:
                    requests.post(upload_url, files={'image': f}, timeout=10)
                
                # 发送图片
                requests.post(FEISHU_WEBHOOK, json={
                    "msg_type": "image",
                    "content": {"image_key": image_key}
                })
                print("【调试】飞书图片推送成功")
        except Exception as e:
            print(f"【调试】飞书图片推送失败：{e}")

@app.route('/webhook', methods=['POST'])
def feishu_webhook():
    """接收飞书消息的核心接口"""
    try:
        data = request.get_json()
        print(f"【调试】收到飞书消息：{data}")
        
        # 提取飞书发送的配置文本
        if 'event' in data and data['event']['type'] == 'message_received':
            text = data['event']['message']['content']
            # 解析配置
            config = parse_server_config(text)
            # 京东搜索
            items = jd_search(config['search_keyword'])
            # 整理回复
            reply = f"【京东比价结果】\n搜索关键词：{config['search_keyword']}\n\n"
            for i, item in enumerate(items, 1):
                reply += f"商品{i}：\n标题：{item['title']}\n价格：{item['price']}\n链接：{item['url']}\n\n"
            # 截图+推送
            screenshot_path = None
            if items and items[0]['url'] and items[0]['price'] != '0':
                screenshot_path = jd_screenshot(items[0]['url'])
            send_feishu_msg(reply, screenshot_path)
        
        return jsonify({"code": 0, "msg": "success"})
    except Exception as e:
        print(f"【调试】Webhook异常：{e}")
        return jsonify({"code": 1, "msg": str(e)})

if __name__ == '__main__':
    # 启动本地服务（固定端口5000）
    print("✅ 京东比价机器人已启动，监听 http://0.0.0.0:5000/webhook")
    app.run(host='0.0.0.0', port=5000, debug=False)  # debug=False避免重复运行
