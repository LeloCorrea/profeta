import asyncio
from app.verse_service import get_last_verse_for_user
from app.content_service import get_or_create_reflection_content
from app.db import SessionLocal
import traceback

async def main():
    user_id = '123456'
    verse = await get_last_verse_for_user(user_id)
    print('Versículo retornado:', verse)
    try:
        reflection = await get_or_create_reflection_content(SessionLocal, user_id, {**verse, 'telegram_user_id': user_id})
        print('Explicação retornada:', reflection)
    except Exception as e:
        print('--- TRACEBACK COMPLETO ---')
        traceback.print_exc()
        print('--- FIM TRACEBACK ---')

if __name__ == '__main__':
    asyncio.run(main())
