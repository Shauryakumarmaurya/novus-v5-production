#!/bin/bash
# run this script as sudo: sudo bash deploy_azure.sh

echo "Updating system packages..."
apt-get update -y
apt-get upgrade -y

echo "Installing Docker..."
apt-get install -y ca-certificates curl gnupg lsb-release
mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "Starting Docker containers..."
# This assumes you are in the directory with the docker-compose.yml
docker compose up -d --build

echo "Deployment finished! You can view logs with: docker compose logs -f"
