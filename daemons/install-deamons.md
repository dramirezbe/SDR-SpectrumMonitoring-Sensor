# Install venv , install gpiod v2 binarie from source and compile C code:

```bash
# 1. Install build dependencies
sudo apt install libzmq3-dev libcjson-dev libcurl4-openssl-dev python3-venv autoconf automake libtool pkg-config git autoconf-archive
# 2. Install gpiod v2 from source
cd ~
git clone https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git
cd libgpiod
./autogen.sh
./configure
make
sudo make install

# 3. Install venv and project
cd /home/anepi/SDR-SpectrumMonitoring-Sensor/
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate
chmod +x build.sh
./build.sh
chmod +x /home/anepi/SDR-SpectrumMonitoring-Sensor/rf_app
chmod +x /home/anepi/SDR-SpectrumMonitoring-Sensor/ltegps_app
```

# Install daemons:

```bash
cd daemons
```

```bash
sudo cp lte-gps-client.service /etc/systemd/system/
sudo cp rf-client.service /etc/systemd/system/
sudo cp orchestrator.service /etc/systemd/system/
```

# Install in systemd:

```bash
# 1. Reload the systemd manager configuration
sudo systemctl daemon-reload

# 2. Enable the service to start automatically on boot
sudo systemctl enable lte-gps-client.service
sudo systemctl enable rf-client.service
sudo systemctl enable orchestrator.service

# 3. Start the service right now
sudo systemctl start lte-gps-client.service
sudo systemctl start rf-client.service
sudo systemctl start orchestrator.service
```

