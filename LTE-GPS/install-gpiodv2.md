# libgpiod v2 Installation and Compilation Guide

This guide details how to install the latest **libgpiod (v2 API)** from source on Raspberry Pi OS and provides the necessary compilation flags to successfully build your C program.

## 1. 📦 Install Build Dependencies

Before compiling, ensure you have all necessary tools and the missing `autoconf-archive` package installed.

```bash
sudo apt update
sudo apt install autoconf automake libtool pkg-config git autoconf-archive

# Go to your home directory or a preferred development location
cd ~
git clone https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git
cd libgpiod

# 3a. Generate the build scripts (fixes 'AX_ macro' error)
./autogen.sh

# 3b. Configure the build environment
./configure

# 3c. Compile the source code
make

# 3d. Install the library and headers
# This installs the v2 headers and libraries primarily to /usr/local/
sudo make install
```
