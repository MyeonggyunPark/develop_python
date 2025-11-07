# HTTP 요청을 보내기 위한 모듈 / For sending HTTP requests
import requests

# 보기 좋게 출력하기 위한 모듈 / Pretty-print dictionary data
from pprint import pprint  

# 조회할 영화 ID 목록 / List of movie IDs to fetch
movie_ids = [238, 680, 550, 185, 641, 515042, 152532, 120467, 872585, 906126, 840430]

# 영화 정보 API 기본 URL / Base URL for movie info API
movie_url = "https://nomad-movies.nomadcoders.workers.dev/movies/"

# 영화 정보를 저장할 딕셔너리 / Dictionary to store movie data
movies_infos = {}

# 상태 코드 200(성공) 카운터 / Counter for successful(200 OK) responses
get_200_count = 0

# 각 영화 ID에 대해 API 요청 / Loop over movie IDs and send requests
for id in movie_ids:

    # 영화 ID를 붙여 전체 URL 생성 / Construct full URL with movie ID
    url = movie_url + str(id) 

    # GET 요청 전송 / Send GET request
    response = requests.get(url)  

    # 요청 성공 시 / If request is successful
    if response.status_code == 200:  
        get_200_count += 1  
        data = response.json()  

        # 딕셔너리 키를 'id-영화 아이디' 형식으로 지정 / Set dictionary key like 'id-movie's id'
        dict_key = f"id-{id}"  

        # 필요한 영화 정보만 저장 / Store only selected movie fields
        movies_infos[dict_key] = {
            "title": data.get("title"),  
            "overview": data.get("overview"),  
            "vote_average": data.get("vote_average"), 
        }

    else:
        # 실패한 경우 상태 코드 출력 / Print error message for failed requests
        print(f"[Error] - Status {response.status_code} for ID {id}")

# 모든 요청이 성공했는지 확인 / Check if all requests were successful
if len(movie_ids) == get_200_count:

    # 전체 성공 / All success
    print(f"\n✅ Successfully collected information for {get_200_count} movies!\n")  
else:
    # 실패한 영화 수 계산 / Calculate number of failed movies
    failed_count = (len(movie_ids) - get_200_count)  
    print(f"\n⚠️ Only {get_200_count} out of {len(movie_ids)} movies were collected successfully.")
    print(f"❌ {failed_count} movie(s) failed to load.\n")

# 최종 결과 출력 / Print collected movie info
pprint(movies_infos)

