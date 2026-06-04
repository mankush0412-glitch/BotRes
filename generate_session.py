"""
Run this script ONCE on your local machine to generate SESSION_STRING.
python generate_session.py

Phir jo string mile use Render ke environment variable mein daalo.
"""

from pyrogram import Client
import asyncio

API_ID = int(input("API_ID daalo: "))
API_HASH = input("API_HASH daalo: ")

async def main():
    async with Client("my_session", api_id=API_ID, api_hash=API_HASH) as app:
        session_string = await app.export_session_string()
        print("\n" + "="*60)
        print("✅ Aapka SESSION_STRING:")
        print("="*60)
        print(session_string)
        print("="*60)
        print("\nIs string ko Render ke SESSION_STRING env variable mein daalo.\n")

asyncio.run(main())
