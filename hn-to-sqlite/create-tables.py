import asyncio
import aiosqlite


async def create_tables(db):
    async with db.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            deleted BOOLEAN,
            type TEXT,
            by TEXT,
            time INTEGER,
            text TEXT,
            dead BOOLEAN,
            parent INTEGER,
            poll INTEGER,
            url TEXT,
            score INTEGER,
            title TEXT,
            parts TEXT,
            descendants INTEGER
        ) WITHOUT ROWID;
    """):
        await db.commit()

    async with db.execute("""
        CREATE TABLE IF NOT EXISTS kids (
            item INTEGER,
            kid INTEGER,
            display_order INTEGER,
            FOREIGN KEY (item) REFERENCES items (id),
            FOREIGN KEY (kid) REFERENCES items (id)
        );
    """):
        await db.commit()

    async with db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            created INTEGER,
            karma INTEGER,
            about TEXT,
            submitted TEXT
        );
    """):
        await db.commit()


async def main():
    async with aiosqlite.connect("hn_data.db") as db:
        await create_tables(db)

if __name__ == "__main__":
    asyncio.run(main())
