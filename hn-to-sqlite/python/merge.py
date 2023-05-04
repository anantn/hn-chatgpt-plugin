import sqlite3


def merge_databases(file_list, output_file):
    # Connect to the new database
    output_db = sqlite3.connect(output_file)

    # Optimize SQLite settings for faster performance
    output_db.execute("PRAGMA synchronous = OFF")
    output_db.execute("PRAGMA journal_mode = MEMORY")

    # Create the schema in the new database
    output_db.execute(
        """
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
    """
    )

    output_db.execute(
        """
        CREATE TABLE IF NOT EXISTS kids (
            item INTEGER,
            kid INTEGER,
            display_order INTEGER,
            FOREIGN KEY (item) REFERENCES items (id),
            FOREIGN KEY (kid) REFERENCES items (id),
            UNIQUE(item, kid)
        );
    """
    )

    output_db.commit()

    # Iterate through the list of database files to prefer newer data
    for db_file in file_list:
        print(f"Merging {db_file}")

        # Attach the current database
        output_db.execute(f"ATTACH DATABASE ? AS to_merge", (db_file,))

        # Begin transaction
        output_db.execute("BEGIN")

        # Insert or ignore data in items table
        output_db.execute(
            """
            INSERT OR IGNORE INTO main.items
            SELECT * FROM to_merge.items;
        """
        )

        # Insert or ignore data in kids table
        output_db.execute(
            """
            INSERT OR IGNORE INTO main.kids
            SELECT * FROM to_merge.kids;
        """
        )

        # Commit transaction
        output_db.commit()

        # Detach the current database
        output_db.execute("DETACH DATABASE to_merge")

    # Close the output database
    output_db.close()


if __name__ == "__main__":
    db_files = [f"hn_data_{i}.db" for i in range(32)]
    output_db_file = "merged_hn_data.db"

    merge_databases(db_files, output_db_file)
    print("Merging complete")
