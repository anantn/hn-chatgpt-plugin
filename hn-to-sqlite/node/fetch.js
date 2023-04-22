import { initializeApp, deleteApp } from "firebase/app";
import { getDatabase, ref, child, get } from "firebase/database";
import sqlite3 from "sqlite3";
import { promisify } from "util";
import ProgressBar from "progress";
import { create } from "domain";

const args = process.argv.slice(2);
const START_ID = parseInt(args[0]);
const END_ID = parseInt(args[1]);
const BATCH_SIZE = 2048;
const NUM_WORKERS = 256;

const firebaseConfig = {
    databaseURL: "https://hacker-news.firebaseio.com",
};

const app = initializeApp(firebaseConfig);
const dbRef = ref(getDatabase(app));

async function fetchItem(id) {
    const snapshot = await get(child(dbRef, `v0/item/${id}`));
    return snapshot.val();
}

async function insertItemsBatch(db, itemsBatch) {
    const insertItem = promisify(db.run).bind(db);
    const insertKid = promisify(db.run).bind(db);

    for (const item of itemsBatch) {
        if (!item) continue;
        const parts = item.parts ? item.parts.join(",") : null;

        await insertItem(`
      INSERT OR IGNORE INTO items (id, deleted, type, by, time, text, dead, parent, poll, url, score, title, parts, descendants)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `, [item.id, item.deleted, item.type, item.by, item.time, item.text, item.dead, item.parent, item.poll, item.url, item.score, item.title, parts, item.descendants]);

        if (item.kids) {
            for (const [order, kidId] of item.kids.entries()) {
                await insertKid(`
          INSERT INTO kids (item, kid, display_order)
          VALUES (?, ?, ?)
        `, [item.id, kidId, order]);
            }
        }
    }
}


function createTables(db) {
    db.run(`
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
  `);

    db.run(`
    CREATE TABLE IF NOT EXISTS kids (
        item INTEGER,
        kid INTEGER,
        display_order INTEGER,
        FOREIGN KEY (item) REFERENCES items (id),
        FOREIGN KEY (kid) REFERENCES items (id)
    );
  `);

    db.run(`
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        created INTEGER,
        karma INTEGER,
        about TEXT,
        submitted TEXT
    );
  `);
}

async function main() {
    if (!START_ID || !END_ID || START_ID < END_ID) {
        console.error("Error: START_ID must be greater than END_ID.");
        process.exit(1);
    }

    const db = new sqlite3.Database("hn_data.db");
    createTables(db);

    const exec = promisify(db.exec).bind(db);
    await exec("PRAGMA synchronous = OFF;");
    await exec("PRAGMA journal_mode = WAL;");
    await exec("PRAGMA cache_size = 1000000;");
    await exec("PRAGMA temp_store = MEMORY;");
    await exec("PRAGMA locking_mode = EXCLUSIVE;");
    await exec("PRAGMA mmap_size = 30000000000;");

    const bar = new ProgressBar("Processing [:bar] :current/:total :percent :rate :elapsed/:etas", {
        total: START_ID - END_ID,
    });

    let itemsBatch = [];
    let itemIds = Array.from({ length: START_ID - END_ID }, (_, i) => START_ID - i);
    let index = 0;

    while (index < itemIds.length) {
        const fetchPromises = [];
        for (let j = 0; j < NUM_WORKERS && index < itemIds.length; j++, index++) {
            fetchPromises.push(fetchItem(itemIds[index]));
        }

        const items = await Promise.all(fetchPromises);
        itemsBatch = itemsBatch.concat(items);

        if (itemsBatch.length >= BATCH_SIZE) {
            await insertItemsBatch(db, itemsBatch.splice(0, BATCH_SIZE));
            bar.tick(BATCH_SIZE);
        }
    }

    // Insert any remaining items in the last batch
    if (itemsBatch.length > 0) {
        await insertItemsBatch(db, itemsBatch);
        bar.tick(itemsBatch.length);
    }

    await db.close();
    await deleteApp(app);
}

await main();
