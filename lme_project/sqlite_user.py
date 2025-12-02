import hashlib
import sqlite3

# Verbindung zur Datenbank herstellen | DB 연결
conn = sqlite3.connect("user_data.db")
cursor = conn.cursor()

# Tabelle erstellen, falls nicht vorhanden | 테이블이 없으면 생성
cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        role TEXT NOT NULL
    )
    """
)


# Passwort-Hashing-Funktion | 비밀번호 해싱 함수
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


# Benutzerdaten definieren | 사용자 데이터 정의
# Format: (username, password, role)
users_data = [
    ("admin", "1234", "admin"),
    ("louis", "lme2025", "reviewer"),
]

# Daten einfügen | 데이터 삽입
print("Initializing database...")

for username, password, role in users_data:
    hashed_pw = hash_password(password)
    try:
        # INSERT OR REPLACE: Aktualisieren, falls vorhanden | 존재하면 업데이트
        cursor.execute(
            "INSERT OR REPLACE INTO users VALUES (?, ?, ?)", (username, hashed_pw, role)
        )
        print(f"User added/updated: {username}")
    except sqlite3.Error as e:
        print(f"Error ({username}): {e}")

# Speichern und Schließen | 저장 및 종료
conn.commit()
conn.close()
print("user_data.db creation complete!")
