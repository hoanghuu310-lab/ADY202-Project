import json
import time
import os
import random
import threading
import math
from concurrent.futures import ThreadPoolExecutor
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# --- CẤU HÌNH ---
MAX_WORKERS = 2       # Số luồng (An toàn nhất là 2)
TARGET_REVIEWS = 50   # Số review mỗi quán
DATA_FOLDER = "data_by_region" # Folder mới chứa data đã phân loại
HISTORY_FILE = "history_crawled.txt"

# Định vị thư mục
os.chdir(os.path.dirname(os.path.abspath(__file__)))
if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)

# --- CÁC KHÓA AN TOÀN (QUAN TRỌNG) ---
history_lock = threading.Lock() # Khóa để ghi lịch sử
file_write_lock = threading.Lock() # Khóa để ghi data (Tránh xung đột)

# --- BẢN ĐỒ VÙNG MIỀN ---
REGION_MAPPING = {
    "MienBac": ["ha-noi", "hai-phong", "quang-ninh", "bac-ninh", "thai-nguyen"],
    "MienTrung": ["da-nang", "hue", "khanh-hoa", "nha-trang", "quy-nhon", "vinh", "binh-dinh", "quang-nam"],
    "MienNam": ["ho-chi-minh", "can-tho", "dong-nai", "binh-duong", "vung-tau", "long-an"]
}

class ReviewItem:
    def __init__(self, review_id, restaurant_name, city, user_name, comment, rating):
        self.review_id = review_id
        self.restaurant_name = restaurant_name
        self.city = city
        self.user_name = user_name
        self.comment = comment
        self.rating = rating

    def to_json_line(self):
        return json.dumps(self.__dict__, ensure_ascii=False)

def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_window_size(1000, 800)
    return driver

def detect_region(url):
    """Hàm soi Link để biết quán thuộc miền nào"""
    clean_url = url.replace("https://www.foody.vn/", "").replace("http://www.foody.vn/", "")
    parts = clean_url.split("/")
    if len(parts) < 1: return "Khac", "unknown"
    
    city_slug = parts[0]
    found_region = "Khac" # Mặc định
    
    for region, cities in REGION_MAPPING.items():
        if city_slug in cities:
            found_region = region
            break
            
    return found_region, city_slug

def scroll_human_like(driver, target_count):
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(15): 
        elems = driver.find_elements(By.XPATH, "//div[contains(@class, 'review-item')] | //li[contains(@class, 'review-item')]")
        if len(elems) >= target_count: break 
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(2, 4)) 
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height: break 
        last_height = new_height

def mark_as_done(url):
    with history_lock:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(url + "\n")

def worker_crawl(thread_id, list_urls):
    print(f"🤖 Worker {thread_id}: Bắt đầu nhiệm vụ...")
    driver = setup_driver()
    
    for url in list_urls:
        try:
            # 1. Xác định vùng miền NGAY TỪ ĐẦU
            region, city = detect_region(url)
            
            # File đích tương ứng (Ví dụ: data_by_region/reviews_MienNam.jsonl)
            output_file = os.path.join(DATA_FOLDER, f"reviews_{region}.jsonl")
            
            driver.get(url)
            time.sleep(random.uniform(3, 5))
            
            scroll_human_like(driver, TARGET_REVIEWS)
            
            review_elements = driver.find_elements(By.XPATH, "//div[contains(@class, 'review-item')] | //li[contains(@class, 'review-item')]")
            items_to_take = review_elements[:TARGET_REVIEWS]
            
            if not items_to_take:
                mark_as_done(url)
                continue

            # --- ĐOẠN NÀY DÙNG KHÓA ĐỂ GHI FILE AN TOÀN ---
            # Gom dữ liệu vào list trước
            lines_to_write = []
            for idx, element in enumerate(items_to_take):
                try:
                    try: user = element.find_element(By.CSS_SELECTOR, ".ru-username").text.strip()
                    except: user = "Anonymous"
                    try: comment = element.find_element(By.CSS_SELECTOR, ".rd-des").text.strip()
                    except: comment = ""
                    try: 
                        rating_text = element.find_element(By.CSS_SELECTOR, ".review-points span").text
                        rating = float(rating_text)
                    except: rating = 0.0
                    
                    if comment:
                        item = ReviewItem(
                            review_id=f"{city}_{random.randint(10000,99999)}",
                            restaurant_name=url.split("/")[-1],
                            city=city,
                            user_name=user,
                            comment=comment,
                            rating=rating
                        )
                        lines_to_write.append(item.to_json_line())
                except: continue
            
            # MỞ KHÓA -> GHI VÀO FILE CHUNG -> ĐÓNG KHÓA
            if lines_to_write:
                with file_write_lock:
                    with open(output_file, 'a', encoding='utf-8') as f:
                        for line in lines_to_write:
                            f.write(line + "\n")
            
            mark_as_done(url)
            print(f"   ✅ Worker {thread_id}: Xong {len(lines_to_write)} reviews -> Vào file {region}")
            time.sleep(random.uniform(3, 6))
            
        except Exception as e:
            print(f"   ❌ Lỗi: {url}")
            
    driver.quit()

if __name__ == "__main__":
    file_link = "list_links.txt"
    if not os.path.exists(file_link):
        print("❌ Chưa có file list_links.txt!")
        exit()
        
    with open(file_link, "r", encoding="utf-8") as f:
        all_links = list(set([line.strip() for line in f if line.strip()]))
    
    # Check lịch sử
    done_links = set()
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            done_links = set([line.strip() for line in f if line.strip()])
    
    todo_links = [url for url in all_links if url not in done_links]
    
    if not todo_links:
        print("🎉 Đã xong hết rồi!")
        exit()

    chunk_size = math.ceil(len(todo_links) / MAX_WORKERS)
    link_chunks = [todo_links[i:i + chunk_size] for i in range(0, len(todo_links), chunk_size)]
    
    print(f"🚀 Bắt đầu crawl và tự động chia Miền Bắc/Trung/Nam...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for i, chunk in enumerate(link_chunks):
            executor.submit(worker_crawl, i+1, chunk)
