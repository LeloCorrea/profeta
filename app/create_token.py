import asyncio
from app.token_service import create_activation_token

async def main():
    token = await create_activation_token()
    print(token)

if __name__ == "__main__":
    asyncio.run(main())
