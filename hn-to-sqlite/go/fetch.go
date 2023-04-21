package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"

	"github.com/cheggaaa/pb/v3"
	_ "github.com/mattn/go-sqlite3"
)

const (
	itemURL    = "https://hacker-news.firebaseio.com/v0/item/%d.json"
	startID    = 10000
	endID      = 1
	batchSize  = 1024
	numWorkers = 96
)

type Item struct {
	ID          int    `json:"id"`
	Deleted     bool   `json:"deleted"`
	Type        string `json:"type"`
	By          string `json:"by"`
	Time        int    `json:"time"`
	Text        string `json:"text"`
	Dead        bool   `json:"dead"`
	Parent      int    `json:"parent"`
	Poll        int    `json:"poll"`
	URL         string `json:"url"`
	Score       int    `json:"score"`
	Title       string `json:"title"`
	Parts       []int  `json:"parts"`
	Descendants int    `json:"descendants"`
	Kids        []int  `json:"kids"`
}

func fetchItem(id int) (*Item, error) {
	resp, err := http.Get(fmt.Sprintf(itemURL, id))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("status code: %d", resp.StatusCode)
	}
	var item Item
	err = json.NewDecoder(resp.Body).Decode(&item)
	return &item, err
}

func insertItem(tx *sql.Tx, item *Item, itemStmt, kidStmt *sql.Stmt) error {
	_, err := itemStmt.Exec(item.ID, item.Deleted, item.Type, item.By, item.Time, item.Text,
		item.Dead, item.Parent, item.Poll, item.URL,
		item.Score, item.Title, strings.Join(intsToStrings(item.Parts), ","), item.Descendants)
	if err != nil {
		return err
	}
	if item.Kids != nil {
		for order, kidID := range item.Kids {
			_, err := kidStmt.Exec(item.ID, kidID, order)
			if err != nil {
				return err
			}
		}
	}
	return nil
}

func intsToStrings(ints []int) []string {
	strs := make([]string, len(ints))
	for i, n := range ints {
		strs[i] = strconv.Itoa(n)
	}
	return strs
}

func main() {
	db, err := sql.Open("sqlite3", "hn_data.db")
	if err != nil {
		panic(err)
	}
	defer db.Close()
	db.Exec("PRAGMA synchronous = OFF;")
	db.Exec("PRAGMA journal_mode = WAL;")
	db.Exec("PRAGMA cache_size = 1000000;")
	db.Exec("PRAGMA temp_store = MEMORY;")
	db.Exec("PRAGMA locking_mode = EXCLUSIVE;")
	db.Exec("PRAGMA mmap_size = 30000000000;")

	itemsToFetch := startID - endID
	bar := pb.Full.Start(itemsToFetch)

	var wg sync.WaitGroup
	var dbLock sync.Mutex
	sem := make(chan struct{}, numWorkers)
	for i := startID; i > endID-batchSize; i -= batchSize {
		// Begin a transaction
		tx, err := db.Begin()
		if err != nil {
			panic(err)
		}
		// Prepare the SQL statements
		itemStmt, err := tx.Prepare(`
            INSERT OR IGNORE INTO items (id, deleted, type, by, time, text, dead, parent, poll, url, score, title, parts, descendants)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `)
		if err != nil {
			panic(err)
		}
		defer itemStmt.Close()
		kidStmt, err := tx.Prepare(`
            INSERT INTO kids (item, kid, display_order)
            VALUES (?, ?, ?)
        `)
		if err != nil {
			panic(err)
		}
		defer kidStmt.Close()
		for j := i; j > i-batchSize && j > endID; j-- {
			wg.Add(1)
			sem <- struct{}{}
			go func(itemID int) {
				defer func() {
					wg.Done()
					<-sem
					bar.Increment()
				}()
				item, err := fetchItem(itemID)
				if err != nil {
					fmt.Printf("Error fetching item %d: %v\n", itemID, err)
					return
				}
				err = insertItem(tx, item, itemStmt, kidStmt)
				if err != nil {
					fmt.Printf("Error inserting item %d: %v\n", itemID, err)
				}
			}(j)
		}
		wg.Wait()
		// Commit the transaction
		dbLock.Lock()
		tx.Commit()
		dbLock.Unlock()
	}
	bar.Finish()
	fmt.Println("Completed fetching items")
}
