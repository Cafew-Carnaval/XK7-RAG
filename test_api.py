from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

llm = ChatOpenAI(model="gpt-4o")
print(llm.invoke("Dis bonjour en une phrase.").content)