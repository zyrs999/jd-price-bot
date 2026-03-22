import os
import re
import time
import random
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# 读取配置+飞书Webhook（从环境变量/文件读取）
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
try:
    with open("config.txt", "r", encoding="utf-8") as f:
        SERVER_CONFIG = f.read().strip()
except Exception as e:
    SERVER_CONFIG = ""
    print(f"读取配置文件失败：{str(e)}")

def parse_server_config(text):
    """【修复版】鲁棒的服务器配置解析（兼容各种分隔符和格式）"""
    result = {}
    # 1. 品牌+型号（匹配戴尔/DELL + Rxxx，兼容空格/特殊字符）
    brand_model = re.search(r'(戴尔|DELL|Dell)\s*[-/]*\s*(R\d{3})|(R\d{3})', text, re.I)
    if brand_model:
        result['model'] = brand_model.group(2) or brand_model.group(3) or brand_model.group(1)
    else:
        result['model'] = ''
    
    # 2. CPU（匹配至强E-xxxx/至强 E xxxx/E-xxxx等格式）
    cpu = re.search(r'至强\s*([Ee][- ]*\d+[^\s|丨]+)|([Ee][- ]*\d+)', text)
    result['cpu'] = cpu.group().replace(' ', '') if cpu else ''
    
    # 3. 内存（匹配xxG/XXGB，忽略大小写）
    mem = re.search(r'(\d+[Gg][Bb]?)', text)
    result['memory'] = mem.group().upper() if mem else ''
    
    # 4. 硬盘（匹配x*xxT/x*xxG/xxT*x等格式，去空格）
    disk = re.search(r'(\d+\s*\*\s*\d+[GTgt])|(\d+[GTgt]\s*\*\s*\d+)', text)
    result['disk'] = disk.group().replace(' ', '').upper() if disk else ''
    
    # 5. 功率（匹配xxW/XX瓦）
    power = re.search(r'(\d+[Ww]|(\d+)瓦)', text)
    if power:
        result['power'] = f"{power.group(2) if power.group(2) else power.group(1).replace('w','W').replace('W','')}W"
    else:
        result['power'] = ''
    
    # 6. 搜索关键词（兜底：解析不全则用原文本）
    kw_parts = [v.strip() for k, v in result.items() if v and v.strip()]
    result['search_keyword'] = ' '.join(kw_parts) if kw_parts else text
    print(f"【调试】配置解析结果：{result}")  # 打印解析日志，方便排查
    return result

def jd_search(keyword):
    """【稳定版】京东搜索（兼容新版页面+防限流）"""
    url = f"https://search.jd.com/Search"
    params = {
        'keyword': keyword,
        'enc': 'utf-8',
        'page': 1,
        'stock': 1  # 只搜有货商品
    }
    
    # 更多User-Agent，降低限流概率
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Linux; Ubuntu 22.04; x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ]
    
    headers = {
        'User-Agent': random.choice(user_agents),
        'Cookie': os.getenv("JD_COOKIE", ""),
        'Referer': 'https://www.jd.com/',
        'Accept-Language': 'zh-CN,zh;q=0.9'
    }
    
    try:
        # 延长延迟（3-5秒），降低限流风险
        time.sleep(random.uniform(3, 5))
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'lxml')
        
        # 兼容京东新版/旧版商品容器
        items = []
        product_items = soup.select('.gl-item') or soup.select('.gl-i-wrap')
        print(f"【调试】京东搜索到商品数量：{len(product_items)}")
        
        for item in product_items[:3]:  # 取前3个商品
            # 精准匹配价格（新版页面价格在 .p-price i 里）
            price_elem = item.select_one('.p-price i') or item.select_one('.p-price strong')
            # 精准匹配标题（去多余标签）
            title_elem = item.select_one('.p-name em') or item.select_one('.p-name a')
            # 精准匹配链接
            link_elem = item.select_one('.p-img a')
            
            if not (title_elem and price_elem and link_elem):
                continue
            
            title = title_elem.get_text(strip=True).replace('<br/>', ' ')
            price = "¥" + price_elem.get_text(strip=True)
            link = 'https:' + link_elem['href'] if 'href' in link_elem.attrs else ''
            
            items.append({
                'title': title[:80],  # 标题截断，避免飞书消息过长
                'price': price,
                'url': link
            })
        
        print(f"【调试】有效商品信息：{items}")
        return items if items else [{'title': '未找到匹配商品', 'price': '0', 'url': ''}]
    
    except Exception as e:
        error_msg = f"搜索失败：{str(e)}"
        print(f"【调试】京东搜索异常：{error_msg}")
        return [{'title': error_msg, 'price': '0', 'url': ''}]

def jd_screenshot(url):
    """【兼容版】商品页截图（适配GitHub Actions环境）"""
    save_path = "/tmp/screenshot.jpg"
    options = webdriver.ChromeOptions()
    # GitHub Actions 无头模式关键配置
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-images")  # 禁用图片加载，加快速度
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    
    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        driver.set_page_load_timeout(20)
        driver.get(url)
        time.sleep(5)  # 延长加载时间，确保页面完整
        driver.save_screenshot(save_path)
        driver.quit()
        print(f"【调试】截图成功，保存路径：{save_path}")
        return save_path
    except Exception as e:
        print(f"【调试】截图失败：{str(e)}")
        return None

def send_feishu_msg(content, img_path=None):
    """【稳定版】推送结果到飞书（文本+图片）"""
    if not FEISHU_WEBHOOK:
        print("【调试】飞书Webhook未配置，跳过推送")
        return
    
    # 1. 发送文本消息
    text_data = {
        "msg_type": "text",
        "content": {
            "text": content
        }
    }
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=text_data, timeout=10)
        print(f"【调试】飞书文本推送响应：{resp.status_code} {resp.text}")
    except Exception as e:
        print(f"【调试】飞书文本推送失败：{str(e)}")
    
    # 2. 发送图片消息（如果有截图）
    if img_path and os.path.exists(img_path):
        try:
            # 第一步：获取图片上传链接
            upload_resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Content-Type": "application/json"},
                json={
                    "image_type": "message",
                    "request_id": str(int(time.time()))
                },
                timeout=10
            )
            if upload_resp.status_code != 200:
                print(f"【调试】获取图片上传链接失败：{upload_resp.text}")
                return
            
            upload_url = upload_resp.json()['data']['url']
            image_key = upload_resp.json()['data']['image_key']
            
            # 第二步：上传图片
            with open(img_path, 'rb') as f:
                upload_img_resp = requests.post(upload_url, files={'image': f}, timeout=10)
                print(f"【调试】图片上传响应：{upload_img_resp.status_code}")
            
            # 第三步：发送图片
            img_data = {
                "msg_type": "image",
                "content": {
                    "image_key": image_key
                }
            }
            requests.post(FEISHU_WEBHOOK, json=img_data, timeout=10)
            print(f"【调试】飞书图片推送成功")
        
        except Exception as e:
            print(f"【调试】飞书图片推送失败：{str(e)}")

# 主逻辑（核心执行入口）
if __name__ == '__main__':
    print(f"【调试】原始配置文本：{SERVER_CONFIG}")
    
    # 1. 解析配置
    config = parse_server_config(SERVER_CONFIG)
    if not config['search_keyword']:
        send_feishu_msg("【京东比价结果】\n配置解析失败，请检查输入格式！")
        exit(1)
    
    # 2. 京东搜索
    items = jd_search(config['search_keyword'])
    
    # 3. 整理回复内容
    reply = f"【京东比价结果】\n搜索关键词：{config['search_keyword']}\n\n"
    for i, item in enumerate(items, 1):
        reply += f"商品{i}：\n标题：{item['title']}\n价格：{item['price']}\n链接：{item['url']}\n\n"
    
    print(f"【调试】最终回复内容：{reply}")
    
    # 4. 推送飞书（文本+截图）
    screenshot_path = None
    if items and items[0]['url'] and items[0]['price'] != '0':
        screenshot_path = jd_screenshot(items[0]['url'])
    
    send_feishu_msg(reply, screenshot_path)
    print("【调试】比价任务执行完成！")
