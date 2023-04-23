#!/bin/bash

curl -fsSL https://deb.nodesource.com/setup_19.x | sudo -E bash - &&\
sudo apt-get install -y nodejs wget screen awscli sqlite3

cd /home/ubuntu
wget https://raw.githubusercontent.com/anantn/chatgpt-hn-plugin/main/hn-to-sqlite/node/fetch.js
wget https://raw.githubusercontent.com/anantn/chatgpt-hn-plugin/main/hn-to-sqlite/node/package.json
npm install

# Calculate start and end item IDs for each instance
index=$(curl -s http://169.254.169.254/latest/meta-data/ami-launch-index)
total_items=35663259
items_per_instance=$((total_items / 32))
start_id=$((total_items - items_per_instance * index))

if [ $index -eq 31 ]; then
  end_id=1
else
  end_id=$((start_id - items_per_instance + 1))
fi

# Create a shell script to run the fetch job
cat > run_fetch_job.sh <<EOL
#!/bin/bash
node fetch.js $start_id $end_id
EOL

# Make the shell script executable
chmod +x run_fetch_job.sh

# Run the script inside a detached screen session
screen -dmS fetch-job ./run_fetch_job.sh
