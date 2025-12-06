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
chmod +x /home/anepi/ANE2-realtime/SDR-SpectrumMonitoring-Sensor/LTE-GPS/rf_app
chmod +x /home/anepi/ANE2-realtime/SDR-SpectrumMonitoring-Sensor/LTE-GPS/gps_lte_app

```

# Install daemons:

```bash
cd daemons
```

```bash
sudo cp lte-gps-client.service /etc/systemd/system/
sudo cp rf-client.service /etc/systemd/system/
sudo cp orchestrator-realtime.service /etc/systemd/system/
```

Install in systemd:

```bash
# 1. Reload the systemd manager configuration
sudo systemctl daemon-reload

# 2. Enable the service to start automatically on boot
sudo systemctl enable lte-gps-client.service
sudo systemctl enable rf-client.service
sudo systemctl enable orchestrator-realtime.service

# 3. Start the service right now
sudo systemctl start lte-gps-client.service
sudo systemctl start rf-client.service
sudo systemctl start orchestrator-realtime.service
```

