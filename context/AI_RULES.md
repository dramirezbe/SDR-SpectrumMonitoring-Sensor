# AI Agent Coding Guidelines & Unbreakable Laws
**Project**: SDR-SpectrumMonitoring-Sensor
**Target Hardware**: Raspberry Pi 5 (RPI5)

Every AI assistant, agent, or LLM that interacts with, analyzes, or modifies this repository MUST strictly abide by the following unbreakable laws.

## 1. Strict Resource Optimization (Avoid Throttling/Undervoltage)
- **Law:** All C code and DSP logic must be aggressively optimized to use the bare minimum of CPU and Memory.
- **Why:** The target hardware is a Raspberry Pi 5. Excessive resource usage leads to CPU throttling and power/undervoltage issues. 
- **Actionable:** Avoid unnecessary memory allocations/copies (`malloc`/`memcpy`) in hot paths (like `rx_callback`). Prefer pointer manipulation, in-place processing, lock-free patterns over heavy mutexes, and lightweight math operations where possible.

## 2. Inviolability of User Configuration (ZMQ JSON Parser)
- **Law:** You MUST NOT alter, downsample, decimate, or reduce the resolution of the parameters requested by the user to save resources.
- **Why:** The user is the absolute source of truth. If the parsed JSON from ZMQ requests a specific `sample_rate`, FFT resolution, `nperseg`, or other payload constraints, it must be executed exactly as requested.
- **Actionable:** Optimize the implementation *around* the user's constraints. Never compromise the requested data quality or requested parameters.

## 3. Function-by-Function Refactoring
- **Law:** When tasked with optimization or refactoring, proceed safely by examining and modifying one function at a time.
- **Why:** To prevent sweeping changes from cascading into system failures.
- **Actionable:** Prioritize ensuring that the communication, state management, and APIs between functions do not break. Verify inputs and outputs of functions remain consistent.

## 4. Mandatory Build and Test Workflows
When compiling, testing, or verifying the project, use the exact scripts specified below based on the environment:

### Compilation (DO NOT use sudo)
- **Development/Non-RPI Environments:** ` ./build.sh -dev ` (This specifically ignores the `gpiod` library dependency for seamless testing on desktops).
- **Production (RPI5 Hardware):** ` ./build.sh `

### Full Project Flow / Installation (MUST use sudo)
- **Development Flow Testing:** ` sudo ./install-local.sh ` (Use this to verify the whole project flow without actual RPI interfaces).
- **Production Flow Testing:** ` sudo ./install.sh `
