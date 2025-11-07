import time
import csv
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def scrape_wanted_jobs(search_keyword, scroll_times):
    """원티드 채용공고 페이지에서 공고를 스크래핑하는 함수"""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        # 검색 페이지로 이동
        search_url = (
            f"https://www.wanted.co.kr/search?query={search_keyword}&tab=position"
        )
        page.goto(search_url)
        page.wait_for_load_state("load")

        # 페이지 스크롤 (공고 전체 로드)
        for _ in range(scroll_times):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

        # HTML 코드 추출
        content = page.content()
        browser.close()

    print("✅ HTML 코드 추출 완료")
    return content


def parse_jobs_from_html(html):
    """BeautifulSoup으로 HTML 파싱하여 채용공고 데이터 추출"""
    soup = BeautifulSoup(html, "html.parser")
    jobs_db = []

    jobs_list = soup.find_all("div", class_="JobCard_container__zQcZs")

    for job in jobs_list:
        title = job.find("strong", class_="JobCard_title___kfvj").text.strip()
        company_name = job.find(
            "span",
            class_="CompanyNameWithLocationPeriod_CompanyNameWithLocationPeriod__company__ByVLu",
        ).text.strip()
        required_experience = job.find(
            "span",
            class_="CompanyNameWithLocationPeriod_CompanyNameWithLocationPeriod__location__4_w0l",
        ).text.strip()
        link = f"https://www.wanted.co.kr/{job.find('a')['href']}"

        jobs_db.append(
            {
                "title": title,
                "company_name": company_name,
                "experience": required_experience,
                "link": link
            }
        )

    print(f"✅ {len(jobs_db)}개 공고 데이터 파싱 완료")
    return jobs_db


def save_jobs_to_csv(jobs_db, filename):
    """채용공고 데이터를 CSV로 저장"""
    with open(filename, "w", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=jobs_db[0].keys())
        writer.writeheader()
        writer.writerows(jobs_db)

    print(f"✅ {filename} 파일로 저장 완료")


if __name__ == "__main__":
    
    search_keywords = ["Python", "JavaScript", "Java"]
    
    for keyword in search_keywords:
        html = scrape_wanted_jobs(keyword, 4)
        jobs = parse_jobs_from_html(html)
        save_jobs_to_csv(jobs, f"{keyword}_jobs.csv")
