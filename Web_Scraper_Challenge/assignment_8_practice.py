from bs4 import BeautifulSoup
import requests

url = "https://weworkremotely.com/categories/remote-full-stack-programming-jobs#job-listings"
response = requests.get(url)


if response.status_code == 200:
    print("🟢Request is success")

    soup = BeautifulSoup(response.text, "html.parser")

    job_listings = []
    for li in soup.find_all("li"):
        classes = li.get("class", [])
        if 1 <= len(classes) <= 2 and set(classes).issubset(
            {"new-listing-container", "feature"}
        ):
            job_listings.append(li)

    def extract_text(tag):
        if isinstance(tag, list):
            return [t.text.strip() for t in tag]
        elif tag:
            return tag.text.strip()
        else:
            return "No Information"
        
    def extract_link(tag, base="https://weworkremotely.com"):
        if tag and "href" in tag.attrs:
            return base + tag["href"]
        return "No Information"

    for job in job_listings:
        title_tag = job.find("h3", class_="new-listing__header__title")
        company_tag = job.find("p", class_="new-listing__company-name")
        location_tag = job.find("p", class_="new-listing__company-headquarters")
        category_tags = job.find_all("p", class_="new-listing__categories__category")
        url_tag = job.find("a", class_="listing-link--unlocked")

        title = extract_text(title_tag)
        company = extract_text(company_tag)
        location = extract_text(location_tag)
        categories = extract_text(category_tags)
        url = extract_link(url_tag)

        filtered_categories = []
        for category in categories:
            if category not in ["Featured", "Top 100"] and not category.startswith("$"):
                filtered_categories.append(category)
        work_type = filtered_categories[0] 
        work_locations = ", ".join(filtered_categories[1:])  
        if not work_locations:
            work_locations = "No Information"

        print(f"🧑‍💻 직무명: {title}")
        print(f"🏢 회사명: {company}")
        print(f"📍 회사 위치: {location}")
        print(f"🏷️ 근무 형태: {work_type}")
        print(f"🌍 근무 가능 국가: {work_locations}")
        print(f"🌐 링크: {url}")
        print("-" * 40)


else:
    print(f"🔴 [Error] - {response.status_code}")
