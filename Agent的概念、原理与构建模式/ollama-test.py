import asyncio
from ollama import AsyncClient

async def chat():
  message = {'role': 'user', 'content': 'Why is the sky blue?'}
  response = await AsyncClient().chat(model='deepseek-r1', messages=[message])
  print(response['message']['content'])
  # or access fields directly from the response object
  print(response.message.content)

asyncio.run(chat())