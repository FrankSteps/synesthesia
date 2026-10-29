sudo fuser -k 8080/tcp

pkill -f server.py
python3 synesthesia.py