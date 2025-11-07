import time
import csv
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    # page.goto("https://www.wanted.co.kr")
    page.goto("https://www.wanted.co.kr/search?query=Python&tab=position")

    page.wait_for_load_state("load")

    # ⛔ 가리는 iframe 제거 (첫 번째 가림 요소 제거)
    # page.evaluate(
    #     """
    #     const modal = document.querySelector('iframe.ab-in-app-message');
    #     if (modal) modal.remove();
    # """
    # )
    # print("✅가리는 iframe 제거 완료")

    # # 👉 '다음' 버튼 클릭
    # try:
    #     next_btn = page.get_by_role("button", name="다음")
    #     next_btn.first.click()
    #     print("✅'다음' 버튼 클릭 성공")
    #     page.wait_for_load_state("load")
    # except Exception as e:
    #     print("⛔'다음' 버튼 클릭 실패:", e)
    # time.sleep(3)

    # try:
    #     search_btn = page.get_by_role("button", name="검색")
    #     search_btn.click()
    #     print("✅'검색' 버튼 클릭 성공")
    #     page.wait_for_load_state("load")
    # except Exception as e:
    #     print("⛔'검색' 버튼 클릭 실패:", e)
    # time.sleep(3)

    # page.get_by_role("searchbox", name="검색어를 입력해 주세요").fill("Python")
    # time.sleep(2)

    # page.keyboard.down("Enter")
    # page.wait_for_load_state("load")
    # time.sleep(3)

    # try:
    #     tab_btn = page.get_by_role("tab", name="포지션(99+)")
    #     tab_btn.click()
    #     page.wait_for_load_state("load")
    #     print("✅'탭' 버튼 클릭 성공")
    # except Exception as e:
    #     print("⛔'탭' 버튼 클릭 실패:", e)
    # page.locator("body").click()
    # time.sleep(3)

    for _ in range(5):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_load_state("load")
        time.sleep(4)

    content = page.content()
    if content:
        print("✅ HTML Code 추출 성공")

    browser.close()
    print("✅ 브라우저 종료")

soup = BeautifulSoup(content, "html.parser")

jobs_db = []

jobs_list = soup.find_all("div", class_="JobCard_container__zQcZs")

for job in jobs_list:

    title = job.find("strong", class_="JobCard_title___kfvj").text
    company_name = job.find(
        "span",
        class_="CompanyNameWithLocationPeriod_CompanyNameWithLocationPeriod__company__ByVLu",
    ).text
    required_experience = job.find(
        "span",
        class_="CompanyNameWithLocationPeriod_CompanyNameWithLocationPeriod__location__4_w0l",
    ).text
    link = f"https://www.wanted.co.kr/{job.find('a')['href']}"

    job_info = {
        "title": title,
        "company name": company_name,
        "experience": required_experience,
        "link": link,
    }
    jobs_db.append(job_info)


with open("jobs.csv", "w", encoding="utf-8") as f:
    writter = csv.writer(f)
    writter.writerow(job_info.keys())

    for job in jobs_db:
        writter.writerow(job.values())
