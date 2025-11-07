import time
import csv
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


class WantedScraper:
    """Wanted 채용공고 스크래퍼 클래스"""

    def __init__(self, keyword, scroll_times, headless=False):
        self.keyword = keyword
        self.scroll_times = scroll_times
        self.headless = headless
        self.filename = f"{keyword}.csv"
        self.jobs = []

    def run(self):
        """전체 스크래핑 실행"""
        html = self.get_page_source()
        self.jobs = self.parse_jobs(html)
        self.save_to_csv(self.jobs, self.filename)
        print("🎯 전체 프로세스 완료")

    def get_page_source(self):
        """Playwright로 페이지 열고 HTML 추출"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()

            search_url = (
                f"https://www.wanted.co.kr/search?query={self.keyword}&tab=position"
            )
            page.goto(search_url)
            page.wait_for_load_state("load")

            for _ in range(self.scroll_times):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

            content = page.content()
            browser.close()

        print("✅ HTML 코드 추출 완료")
        return content

    def parse_jobs(self, html):
        """BeautifulSoup으로 HTML 파싱"""
        soup = BeautifulSoup(html, "html.parser")
        jobs_list = soup.find_all("div", class_="JobCard_container__zQcZs")
        jobs_db = []

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
                    "link": link,
                }
            )

        print(f"✅ {len(jobs_db)}개 공고 파싱 완료")
        return jobs_db

    def save_to_csv(self, jobs, filename):
        """CSV 파일로 저장"""
        with open(filename, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=jobs[0].keys())
            writer.writeheader()
            writer.writerows(jobs)

        print(f"✅ CSV 저장 완료: {filename}")


if __name__ == "__main__":
    
    search_keywords = ["Python", "JavaScript", "Java"]

    for keyword in search_keywords:
        scraper = WantedScraper(keyword, 4, headless=False)
        scraper.run()
