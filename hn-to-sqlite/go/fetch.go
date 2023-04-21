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
	startID    = 25000
	endID      = 1
	batchSize  = 1000
	numWorkers = 100
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

func insertItem(db *sql.DB, item *Item) error {
	_, err := db.Exec(`
INSERT OR IGNORE INTO items (id, deleted, type, by, time, text, dead, parent, poll, url, score, title, parts, descendants)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
`, item.ID, item.Deleted, item.Type, item.By, item.Time, item.Text,
		item.Dead, item.Parent, item.Poll, item.URL,
		item.Score, item.Title, strings.Join(intsToStrings(item.Parts), ","), item.Descendants)
	if err != nil {
		return err
	}
	if item.Kids != nil {
		for order, kidID := range item.Kids {
			_, err := db.Exec(`
INSERT INTO kids (item, kid, display_order)
VALUES (?, ?, ?)
`, item.ID, kidID, order)
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

	itemsToFetch := startID - endID
	bar := pb.Full.Start(itemsToFetch)

	var wg sync.WaitGroup
	var dbLock sync.Mutex
	sem := make(chan struct{}, numWorkers)
	for i := startID; i > endID-batchSize; i -= batchSize {
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
				dbLock.Lock()
				err = insertItem(db, item)
				dbLock.Unlock()
				if err != nil {
					fmt.Printf("Error inserting item %d: %v\n", itemID, err)
				}
			}(j)
		}
		wg.Wait()
	}
	bar.Finish()
	fmt.Println("Completed fetching items")
}
