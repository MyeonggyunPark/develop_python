from bs4 import BeautifulSoup
import requests

# 기본 URL / 기본 user-agent
BASE_URL = "https://berlinstartupjobs.com/"
USER_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
}

# 직군별/스택별 검색 키워드 = 최종 검색 키워드
category_keywords = ["engineering", "design-ux"]
skill_keywords = ["python", "typescript", "javascript"]
all_keywords = category_keywords+skill_keywords

urls_list = []

# 카테고리/스킬에 따라 URL 생성 함수
def get_url(keyword):
    if keyword in category_keywords:
        url = BASE_URL+keyword+"/"
    else:
        url = BASE_URL+"skill-areas/"+keyword+"/"
    urls_list.append(url)


# 단일 태그 또는 리스트에서 텍스트 추출 함수
def extract_text(tag):
    if isinstance(tag, list):
        if tag:
            return [t.text.strip() for t in tag]
        else:
            return "No Information"
    elif tag:
        return tag.text.strip()
    else:
        return "No Information"

# 해당 URL의 총 페이지 수 확인 함수
def get_pages(url, headers):
    print(f"Seaching...")

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        print("🟢 Success get all page")
        soup = BeautifulSoup(response.text, "html.parser")
        page_list = soup.find_all("a", class_="page-numbers")

        return len(page_list)
    else:
        print(f"🔴 Erro-{response.status_code}")

# 각 URL에서 공고 정보 추출 함수
infos_list = []
def get_infos(url, headers):
    print(f"Scraping...")

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        print("🟢 Success get all infos")

        soup = BeautifulSoup(response.text, "html.parser")

        jobs_list = soup.find_all("li", class_="bjs-jlid")

        for job in jobs_list:

            title_tag = job.find("h4", class_="bjs-jlid__h")
            company_tag = job.find("a", class_="bjs-jlid__b")
            skills_tag = job.find_all("a", class_="bjs-bl bjs-bl-porcelain")
            description_tag = job.find("div", class_="bjs-jlid__description")

            title = extract_text(title_tag)
            company = extract_text(company_tag)
            required_skills = extract_text(skills_tag)
            description = extract_text(description_tag)
            title_link = title_tag.find("a")["href"]

            infos = {
                "company": company,
                "title": title,
                "skills": required_skills,
                "description": description,
                "link": title_link
            }
            infos_list.append(infos)

    else:
        print(f"🔴 Erro-{response.status_code}")
# 정보 출력 함수
def infos_print(info):
    print("\n======= [📑 INFO] ======")
    for k, v in info.items():
        if isinstance(v, list):
            print(f"{k}: {" / ".join(v)}")
        else:
            print(f"{k}: {v}")

# URL 목록 생성
for keyword in all_keywords:
    get_url(keyword)

# 첫 번째 카테고리 engineering의 페이지 수만큼 순회
for page in range(get_pages(urls_list[0], USER_HEADERS)):
    url = f"{urls_list[0]}page/{page+1}"
    get_infos(url, USER_HEADERS)

# 결과 출력
for info in infos_list:
    infos_print(info)

# skill-based URL들 추출
new_url_list = urls_list[2:]

for url in new_url_list:
    get_infos(url, USER_HEADERS)

# 결과 출력
for info in infos_list:
    infos_print(info)




# 터미널에서 텍스트 색상을 적용하기 위한 라이브러리
from termcolor import colored


# 위의 함수형 구현 코드를 바탕으로 클래스로 구현
class JobsScraper:
    def __init__(self):
        self.BASE_URL = "https://berlinstartupjobs.com/"
        self.USER_HEADERS = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        }
        self.category_keywords = ["engineering", "design-ux"]
        self.skill_keywords = ["python", "typescript", "javascript"]
        self.all_keywords = self.category_keywords + self.skill_keywords
        self.urls_list = []
        self.infos_list = []

    def get_url(self, keyword):
        """카테고리/스킬에 따라 URL 생성"""

        if keyword in self.category_keywords:
            url = self.BASE_URL + keyword + "/"
        else:
            url = self.BASE_URL + "skill-areas/" + keyword + "/"
        self.urls_list.append(url)

    def extract_text(self, tag):
        """단일 태그 또는 리스트에서 텍스트 추출"""

        if isinstance(tag, list):
            return [t.text.strip() for t in tag] if tag else ["No Information"]
        elif tag:
            return tag.text.strip()
        else:
            return "No Information"

    def get_pages(self, url):
        """해당 URL의 총 페이지 수 확인"""

        print(f"📄 Checking pages for: {url}")
        response = requests.get(url, headers=self.USER_HEADERS)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            page_list = soup.find_all("a", class_="page-numbers")
            return len(page_list)
        else:
            print(f"❌ Error fetching pages: {response.status_code}")
            return 0

    def get_infos(self, url):
        """각 URL에서 공고 정보 추출"""

        print(f"🔍 Scraping: {url}")
        response = requests.get(url, headers=self.USER_HEADERS)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            jobs_list = soup.find_all("li", class_="bjs-jlid")

            for job in jobs_list:
                title_tag = job.find("h4", class_="bjs-jlid__h")
                company_tag = job.find("a", class_="bjs-jlid__b")
                skills_tag = job.find_all("a", class_="bjs-bl bjs-bl-porcelain")
                description_tag = job.find("div", class_="bjs-jlid__description")

                link_tag = title_tag.find("a") if title_tag else None
                title_link = (
                    link_tag["href"]
                    if link_tag and "href" in link_tag.attrs
                    else "No Link"
                )

                infos = {
                    "company": self.extract_text(company_tag),
                    "title": self.extract_text(title_tag),
                    "skills": self.extract_text(skills_tag),
                    "description": self.extract_text(description_tag),
                    "link": title_link,
                }

                self.infos_list.append(infos)
        else:
            print(f"❌ Error fetching infos: {response.status_code}")

    def infos_print(self, info):
        """정보 출력 함수"""
        print(colored("\n======= [📑 INFO] ======", "cyan"))
        for k, v in info.items():
            key_label = colored(f"{k}:", "yellow")
            if isinstance(v, list):
                print(f"{key_label} {' / '.join(v)}")
            else:
                print(f"{key_label} {v}")

    def run(self):
        """전체 실행 함수"""

        # URL 목록 생성
        for keyword in self.all_keywords:
            self.get_url(keyword)

        # 첫 번째 카테고리 engineering의 페이지 수만큼 순회
        first_url = self.urls_list[0]
        total_pages = self.get_pages(first_url)

        for page in range(total_pages):
            paged_url = f"{first_url}page/{page + 1}"
            self.get_infos(paged_url)

        # skill-based URL들 추출
        for url in self.urls_list[2:]:
            self.get_infos(url)

        # 결과 출력
        for info in self.infos_list:
            self.infos_print(info)


if __name__ == "__main__":
    scraper = JobsScraper()
    scraper.run()
