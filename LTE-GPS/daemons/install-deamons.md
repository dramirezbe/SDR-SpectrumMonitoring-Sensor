# Install venv and compile C code:
in root project:

```bash
sudo apt install libzmq3-dev libcjson-dev libcurl4-openssl-dev
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate

chmod +x build.sh
./build.sh
chmod +x /home/anepi/ANE2-realtime/SDR-SpectrumMonitoring-Sensor/LTE-GPS/main

```

# Install daemons:

```bash
cd daemons
```

for orchestrator:

```bash
sudo cp orchestrator-realtime.service /etc/systemd/system/
```

for client-psd-gps:

```bash
sudo cp client-psd-gps.service /etc/systemd/system/
```

Install in systemd:

```bash
# 1. Reload the systemd manager configuration
sudo systemctl daemon-reload

# 2. Enable the service to start automatically on boot
sudo systemctl enable client-psd-gps.service
sudo systemctl enable orchestrator-realtime.service

# 3. Start the service right now
sudo systemctl start client-psd-gps.service
sudo systemctl start orchestrator-realtime.service
```

