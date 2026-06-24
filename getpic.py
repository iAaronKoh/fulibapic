#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
福利吧 福利汇总栏目图片采集器 - 多线程加速版
用法：pip install requests beautifulsoup4
      python fuliba_spider.py
"""

import requests
from bs4 import BeautifulSoup
import json
import re
import time
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# ==================== 配置区 ====================
BASE_URL = "https://fuliba.net"
CATEGORY_PATH = "/flhz/"
OUTPUT_FILE = "fuliba_images.json"
MAX_WORKERS = 16          # 并发线程数，根据带宽/CPU 调整 (建议 10~20)

# 如需代理，取消下面注释
# PROXIES = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
PROXIES = None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 线程安全计数器
_counter_lock = threading.Lock()
_counter = 0
_total = 0
# =================================================

def fetch(url, session):
    """带重试的通用请求"""
    try:
        resp = session.get(url, proxies=PROXIES, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return resp.text
    except Exception as e:
        print(f"  [请求失败] {url} | {e}")
        return None

def get_total_pages(session):
    """获取栏目列表的总页数"""
    html = fetch(urljoin(BASE_URL, CATEGORY_PATH), session)
    if not html:
        return 1
    soup = BeautifulSoup(html, "html.parser")
    pagination = soup.find("div", class_="pagination")
    if not pagination:
        return 1

    text = pagination.get_text()
    m = re.search(r"共\s*(\d+)\s*页", text)
    if m:
        return int(m.group(1))

    max_page = 1
    for a in pagination.find_all("a", href=True):
        m = re.search(r"/page/(\d+)", a["href"])
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page

def get_article_urls(page_num, session):
    """从栏目列表页提取文章详情页URL"""
    if page_num == 1:
        url = urljoin(BASE_URL, CATEGORY_PATH)
    else:
        url = urljoin(BASE_URL, f"{CATEGORY_PATH}page/{page_num}")

    html = fetch(url, session)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()

    for article in soup.find_all("article", class_="excerpt"):
        a = article.find("a", class_="focus")
        if a and a.get("href"):
            full_url = urljoin(BASE_URL, a["href"])
            if full_url not in seen:
                seen.add(full_url)
                articles.append(full_url)
    return articles

def get_article_page_urls(article_url, session):
    """获取一篇文章的所有分页URL（含第1页）"""
    html = fetch(article_url, session)
    if not html:
        return [article_url]

    soup = BeautifulSoup(html, "html.parser")
    paging = soup.find("div", class_="article-paging")
    pages = [article_url]

    if paging:
        for a in paging.find_all("a", class_="post-page-numbers"):
            href = a.get("href")
            if href:
                full = urljoin(BASE_URL, href)
                if full not in pages:
                    pages.append(full)

    def sort_key(u):
        m = re.search(r"/(\d+)$", u)
        return int(m.group(1)) if m else 0

    pages.sort(key=sort_key)
    return pages

def extract_images(page_url, session):
    """从单页提取 .article-content 区域内的所有图片链接"""
    html = fetch(page_url, session)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("article", class_="article-content")
    if not content:
        return []

    images = []
    for img in content.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        src = src.replace("&amp;", "&")
        if src.startswith("http"):
            images.append(src)
        else:
            images.append(urljoin(BASE_URL, src))
    return images

def process_article(article_url):
    """处理单篇文章：每个线程独立 Session，避免锁竞争"""
    global _counter

    # 每个线程独立 Session
    session = requests.Session()
    session.headers.update(HEADERS)

    pages = get_article_page_urls(article_url, session)
    article_imgs = []

    for pg in pages:
        imgs = extract_images(pg, session)
        article_imgs.extend(imgs)
        time.sleep(0.15)  # 单文章内分页间隔，避免请求过快

    # 去重并保持顺序
    seen = set()
    unique_imgs = []
    for img in article_imgs:
        if img not in seen:
            seen.add(img)
            unique_imgs.append(img)

    with _counter_lock:
        _counter += 1
        print(f"[{_counter}/{_total}] {article_url} | {len(unique_imgs)} 张图片 (分页: {len(pages)} 页)")

    return article_url, unique_imgs

def main():
    global _total, _counter

    print("=" * 60)
    print("福利吧 - 福利汇总栏目图片采集 (多线程加速版)")
    print(f"并发线程: {MAX_WORKERS}")
    print("=" * 60)

    # 1. 获取栏目总页数
    main_session = requests.Session()
    main_session.headers.update(HEADERS)

    total_pages = get_total_pages(main_session)
    print(f"\n栏目列表共 {total_pages} 页，开始采集文章链接...")

    # 2. 遍历所有列表页，收集文章URL
    all_articles = []
    for p in range(1, total_pages + 1):
        print(f"  读取列表页 {p}/{total_pages} ...", end="")
        urls = get_article_urls(p, main_session)
        print(f" 发现 {len(urls)} 篇")
        all_articles.extend(urls)
        time.sleep(0.3)

    # 去重并保持顺序
    all_articles = list(dict.fromkeys(all_articles))
    _total = len(all_articles)
    print(f"\n去重后共有 {_total} 篇文章，启动 {MAX_WORKERS} 线程并行采集...\n")

    # 3. 多线程处理文章
    result = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(process_article, url): url for url in all_articles}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                _, imgs = future.result()
                result[url] = imgs
            except Exception as e:
                print(f"  [异常] {url} | {e}")
                result[url] = []

    # 4. 保存 JSON（按原始顺序）
    ordered_result = {url: result[url] for url in all_articles}
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(ordered_result, f, ensure_ascii=False, indent=2)

    total_imgs = sum(len(v) for v in ordered_result.values())
    print("\n" + "=" * 60)
    print("采集完成！")
    print(f"输出文件: {OUTPUT_FILE}")
    print(f"文章总数: {len(ordered_result)}")
    print(f"图片总数: {total_imgs}")
    print("=" * 60)

if __name__ == "__main__":
    main()