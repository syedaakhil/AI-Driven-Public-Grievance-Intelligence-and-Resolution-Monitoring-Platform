#!/usr/bin/env bash
# City Corporation grievance portal - first-time setup on Linux/macOS.
# Run from anywhere:  bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"

trap 'echo; echo "Setup stopped: the step above failed. Fix the problem and run setup.sh again."; \
      echo " - mysqlclient fails to build: sudo apt install pkg-config default-libmysqlclient-dev build-essential python3-dev"; \
      echo "   (macOS: brew install mysql-client pkg-config)"; \
      echo " - migrate fails with MySQL: check DATABASE_URL in .env and that citycorp_db exists."' ERR

echo "=================================================="
echo "  Welcome! Setting up the City Corporation portal"
echo "=================================================="
echo

echo "[1/5] Creating the virtual environment .venv ..."
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/5] Upgrading pip ..."
pip install --upgrade pip

echo "[3/5] Installing packages from requirements.txt ..."
pip install -r requirements.txt

echo "[4/5] Checking the .env settings file ..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example - edit it to add your settings."
else
    echo ".env already exists - left unchanged."
fi

echo "[5/5] Creating the database tables ..."
python manage.py migrate

echo
echo "Setup complete! Run: source .venv/bin/activate && python manage.py runserver"
echo "Then open http://127.0.0.1:8000/login/ in your browser."
