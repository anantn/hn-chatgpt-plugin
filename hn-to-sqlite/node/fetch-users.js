import sqlite3 from "sqlite3";
import { promisify } from "util";
import { initializeApp, deleteApp } from "firebase/app";
import { getDatabase, ref, child, get } from "firebase/database";
import ProgressBar from "progress";

const args = process.argv.slice(2);
const DB_PATH = args[0];
const BATCH_SIZE = 256;

const firebaseConfig = {
    databaseURL: "https://hacker-news.firebaseio.com",
};

const app = initializeApp(firebaseConfig);
const dbRef = ref(getDatabase(app));

async function fetchUser(id) {
    const snapshot = await get(child(dbRef, `v0/user/${id}`));
    return snapshot.val();
}

async function insertUserBatch(db, usersBatch) {
    const insertUser = promisify(db.run).bind(db);

    for (const user of usersBatch) {
        if (!user) continue;
        await insertUser(`
            INSERT OR REPLACE INTO users (id, created, karma, about, submitted)
            VALUES (?, ?, ?, ?, ?)
        `, [user.id, user.created, user.karma, user.about, JSON.stringify(user.submitted)]);
    }
}

async function main() {
    if (!DB_PATH) {
        console.error("Usage: node fetch-users.js <db>");
        process.exit(1);
    }

    const db = new sqlite3.Database(DB_PATH);

    //const exec = promisify(db.exec).bind(db);
    const all = promisify(db.all).bind(db);

    /*
    await exec("PRAGMA synchronous = OFF;");
    await exec("PRAGMA journal_mode = WAL;");
    await exec("PRAGMA cache_size = 1000000;");
    await exec("PRAGMA temp_store = MEMORY;");
    await exec("PRAGMA locking_mode = EXCLUSIVE;");
    await exec("PRAGMA mmap_size = 30000000000;");
    */

    // Get unique users from the items table
    const users = await all("SELECT DISTINCT by as id FROM items WHERE by IS NOT NULL");

    // Get user IDs already in the users table
    const existingUserIds = await all("SELECT id FROM users");
    const existingUserIdsSet = new Set(existingUserIds.map((row) => row.id));

    // Filter out the existing user IDs
    const missingUsers = users.filter((user) => !existingUserIdsSet.has(user.id));

    const bar = new ProgressBar("Processing [:bar] :current/:total :percent :rate :elapsed/:etas", {
        total: missingUsers.length,
    });

    let userBatch = [];
    let index = 0;

    while (index < missingUsers.length) {
        const fetchPromises = [];

        for (let j = 0; j < BATCH_SIZE && index < missingUsers.length; j++, index++) {
            fetchPromises.push(fetchUser(missingUsers[index].id));
        }

        const userDataBatch = await Promise.all(fetchPromises);
        userBatch = userBatch.concat(userDataBatch);

        if (userBatch.length >= BATCH_SIZE) {
            await insertUserBatch(db, userBatch.splice(0, BATCH_SIZE));
            bar.tick(BATCH_SIZE);
        }
    }

    // Insert any remaining users in the last batch
    if (userBatch.length > 0) {
        await insertUserBatch(db, userBatch);
        bar.tick(userBatch.length);
    }

    db.close();
    await deleteApp(app);
}

await main();
