import os
import sqlite3
import hashlib
import chainlit as cl
import google.generativeai as genai
from typing import Optional, Dict
from dotenv import load_dotenv

# Umgebungsvariablen laden | 환경변수 로드
load_dotenv()

# API-Key überprüfen | Google API 키 확인
api_key = os.environ.get("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("GOOGLE_API_KEY not found. Please check your .env file.")

# Google Gemini Konfiguration | Gemini 설정
genai.configure(api_key=api_key)

# Modell- & Persona-Konfiguration | 모델 및 페르소나 설정
model = genai.GenerativeModel(
    "gemini-2.5-flash",
    system_instruction="""
    Du bist ein freundlicher und geduldiger Koreanisch-Lehrer für deutsche Muttersprachler.
    Dein Name ist "Kim-Ssem" (Frau Kim).
    
    Für dein Verstehen:
    Das Wort "Ssem" bedeutet Lehrer/-in und ist eine Abkürzung von "Seonsaengnim". 
    
    Deine Aufgaben:
    1. Erkläre koreanische Grammatik und Vokabeln immer auf Deutsch.
    2. Wenn der Benutzer etwas auf Koreanisch schreibt, korrigiere es sanft und erkläre den Fehler.
    3. Biete romanisierte Aussprachehilfen an (z. B. "Annyeonghaseyo").
    4. Sei motivierend und lobe den Benutzer oft ("Gut gemacht!").
    5. Nutze Emojis, um freundlich zu wirken.
    """,
)

# Authentifizierungssystem (Hybrid) | 인증 시스템


# Login mit Benutzername/Passwort (SQLite) | 일반 로그인
@cl.password_auth_callback
def auth_callback(username: str, password: str) -> Optional[cl.User]:
    try:
        conn = sqlite3.connect("user_data.db")
        cursor = conn.cursor()

        cursor.execute(
            "SELECT password, role FROM users WHERE username = ?", (username,)
        )
        result = cursor.fetchone()
        conn.close()

        if result:
            stored_password, role = result

            # Passwort hashen und vergleichen | 비밀번호 암호화 및 비교
            hashed_password = hashlib.sha256(password.encode()).hexdigest()

            if hashed_password == stored_password:
                return cl.User(
                    identifier=username,
                    metadata={"role": role, "provider": "credentials"},
                )
    except Exception as e:
        print(f"Login Error: {e}")
        return None

    return None


# Social Login (Google / GitHub) | 소셜 로그인
@cl.oauth_callback
def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: Dict[str, str],
    default_user: cl.User,
) -> Optional[cl.User]:

    # Login-Infos in der Konsole ausgeben (Debugging) | 로그인 정보 출력
    print(f"OAuth Login Attempt:")
    print(f" - Provider: {provider_id}")
    print(f" - User Data: {raw_user_data}")

    # Login immer erlauben | 무조건 로그인 허용
    return default_user


# Chat-Logik | 채팅 로직
@cl.on_chat_start
async def start():

    # Gemini-Session starten | Gemini 세션 시작
    chat_session = model.start_chat(history=[])
    cl.user_session.set("chat_session", chat_session)

    # Eingeloggten Benutzer abrufen | 로그인 유저 정보 가져오기
    user = cl.user_session.get("user")
    username = user.identifier if user else "Student"

    await cl.Message(
        content=f"🇰🇷 **Annyeonghaseyo, {username}! (안녕하세요!)**\n\n"
        "Ich bin dein Tutor **Kim-Ssem**.\n"
        "Lass uns Koreanisch lernen! Frag mich einfach:\n"
        "- *'Wie sagt man Danke auf Koreanisch?'*\n"
        "- *'Erkläre mir 은/는 und 이/가.'*"
    ).send()


@cl.on_message
async def main(message: cl.Message):
    chat_session = cl.user_session.get("chat_session")

    msg = cl.Message(content="")
    await msg.send()

    try:
        response = await chat_session.send_message_async(message.content, stream=True)

        async for chunk in response:
            if chunk.text:
                await msg.stream_token(chunk.text)

        await msg.update()

    except Exception as e:
        error_msg = f"An error occurred: {str(e)}"
        await cl.Message(content=error_msg).send()
