#!/bin/bash

curl -fsSL https://deb.nodesource.com/setup_19.x | sudo -E bash - &&\
sudo apt-get install -y nodejs wget screen

cd /home/ec2-user
wget https://proness.kix.in/misc/hn/fetch.js
wget https://proness.kix.in/misc/hn/package.json
npm install

# Calculate start and end item IDs for each instance
index=$(curl -s http://169.254.169.254/latest/meta-data/ami-launch-index)
total_items=35662762
items_per_instance=$((total_items / 16))
start_id=$((total_items - items_per_instance * index))

if [ $index -eq 15 ]; then
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
