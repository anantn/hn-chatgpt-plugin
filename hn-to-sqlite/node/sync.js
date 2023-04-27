import { initializeApp, deleteApp } from "firebase/app";
import { getDatabase, ref, child, get, onValue } from "firebase/database";
import sqlite3 from "sqlite3";
import { promisify } from "util";
import ProgressBar from "progress";

const args = process.argv.slice(2);
const DB_PATH = args[0];
const BATCH_SIZE = 512;
const OFFSET = 100000;

const firebaseConfig = {
    databaseURL: "https://hacker-news.firebaseio.com",
};

const app = initializeApp(firebaseConfig);
const dbRef = ref(getDatabase(app));

const db = new sqlite3.Database(DB_PATH);
const insertItem = promisify(db.run).bind(db);
const insertKid = promisify(db.run).bind(db);
const insertUser = promisify(db.run).bind(db);

function logWithTimestamp(...args) {
    const timestamp = new Date().toLocaleString();
    console.log(`[${timestamp}]`, ...args);
}

async function getMaxItemId() {
    const snapshot = await get(child(dbRef, `v0/maxitem`));
    return snapshot.val();
}

async function getMaxItemIdFromDb() {
    const query = promisify(db.get).bind(db);
    const row = await query("SELECT MAX(id) as maxId FROM items");
    return row.maxId || 0;
}

async function fetchItem(id) {
    const snapshot = await get(child(dbRef, `v0/item/${id}`));
    return snapshot.val();
}

async function insertItems(items) {
    for (const item of items) {
        if (!item) continue;
        const parts = item.parts ? item.parts.join(",") : null;

        await insertItem(`
    INSERT OR REPLACE INTO items (id, deleted, type, by, time, text, dead, parent, poll, url, score, title, parts, descendants)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
            [item.id, item.deleted, item.type, item.by, item.time, item.text, item.dead, item.parent, item.poll, item.url, item.score, item.title, parts, item.descendants]);

        if (item.kids) {
            for (const [order, kidId] of item.kids.entries()) {
                await insertKid(`
          INSERT OR REPLACE INTO kids (item, kid, display_order)
          VALUES (?, ?, ?)
        `, [item.id, kidId, order]);
            }
        }
    }
}

async function fetchUser(id) {
    const snapshot = await get(child(dbRef, `v0/user/${id}`));
    return snapshot.val();
}

async function insertUsers(users) {
    for (const user of users) {
        if (!user) continue;
        const submitted = user.submitted ? user.submitted.join(",") : null;

        await insertUser(`
    INSERT OR REPLACE INTO users (id, created, karma, about, submitted)
    VALUES (?, ?, ?, ?, ?)`,
            [user.id, user.created, user.karma, user.about, submitted]);
    }
}

async function fetchAndInsertItems(startId, endId) {
    const progressBar = new ProgressBar('Fetching items [:bar] :current/:total :rate/s :percent :etas', {
        complete: '=',
        incomplete: ' ',
        total: endId - startId + 1
    });

    for (let i = startId; i <= endId; i += BATCH_SIZE) {
        const itemsBatch = await Promise.all(Array.from({ length: BATCH_SIZE }, (_, j) => fetchItem(i + j)));
        await insertItems(itemsBatch);
        progressBar.tick(BATCH_SIZE);
    }
}

let buffer = [];
let resolveInitialFetch;
let initialFetchCompleted = false;
const initialFetchComplete = new Promise((resolve) => {
    resolveInitialFetch = resolve;
});

async function processUpdates(updatesArray) {
    const items = [];
    const profiles = [];

    for (const updates of updatesArray) {
        if (updates.items) {
            items.push(...updates.items);
        }
        if (updates.profiles) {
            profiles.push(...updates.profiles);
        }
    }

    // Fetch and insert all items as a batch
    const fetchedItems = await Promise.all(items.map(fetchItem));
    await insertItems(fetchedItems);
    logWithTimestamp(`Updated ${fetchedItems.length} items.`);

    // Fetch and insert all user profiles as a batch
    const fetchedProfiles = await Promise.all(profiles.map(fetchUser));
    await insertUsers(fetchedProfiles);
    logWithTimestamp(`Updated ${fetchedProfiles.length} profiles.`);
}

async function watchUpdates() {
    const updatesRef = ref(getDatabase(app), "v0/updates");

    onValue(updatesRef, async (snapshot) => {
        const updates = snapshot.val();
        if (updates) {
            if (initialFetchCompleted) {
                await processUpdates([updates]);
            } else {
                buffer.push(updates);
            }
        }
    });

    // Wait for the initial fetch to complete before processing buffered updates.
    await initialFetchComplete;
    initialFetchCompleted = true;
    logWithTimestamp("Finished initial fetch, now processing buffered updates.");
    await processUpdates(buffer);
    buffer = [];
    logWithTimestamp("Finished processing buffered updates, now watching for new updates.");
}

function handleShutdown() {
    logWithTimestamp("\nGracefully shutting down...");

    // Close the SQLite database connection
    db.close((err) => {
        if (err) {
            console.error("Error closing SQLite database:", err);
            process.exit(1);
        }
        logWithTimestamp("SQLite database connection closed.");
    });

    // Close the Firebase connection
    deleteApp(app);
    logWithTimestamp("Firebase connection closed.");

    process.exit(0);
}

async function main() {
    // Start watching updates immediately and start buffering.
    process.on("SIGINT", handleShutdown);
    watchUpdates();

    // Fetch max item ID from Firebase and SQLite.
    const maxItemId = await getMaxItemId();
    const maxItemIdFromDb = await getMaxItemIdFromDb();
    const startId = Math.max(maxItemIdFromDb - OFFSET, 1);

    logWithTimestamp(`Fetching items from ID ${startId} to ${maxItemId}`);
    await fetchAndInsertItems(startId, maxItemId);
    logWithTimestamp(`Finished initial fetch, now inserting updates (buffered ${buffer.length}).`);

    // Signal that the initial fetch is complete.
    resolveInitialFetch();
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});