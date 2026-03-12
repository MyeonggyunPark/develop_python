import os
from dotenv import load_dotenv
from openai import OpenAI
from langchain.chat_models import init_chat_model

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")

model = init_chat_model("gpt-5-mini")

prop = "랭체인이란 무엇인가요?"
response = model.invoke(prop)

print(response.content)
