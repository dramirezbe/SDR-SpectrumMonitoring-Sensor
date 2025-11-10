# Installation kalibrate-hackrf

```bash
sudo apt install automake libtool libhackrf-dev hackrf libusb-1.0-0-dev libfftw3-dev

git clone https://github.com/scateu/kalibrate-hackrf.git

cd kalibrate-hackrf
./bootstrap
./configure
make
sudo make install
make check
make installcheck
```


# How to use it

```bash
kal -a -s <band>
```