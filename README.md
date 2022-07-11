# Firmware for AArch64 Surface Devices

Firmware and firmware-related helpers for AArch64 based Microsoft Surface devices.
Currently, supports the Surface Pro X SQ1 and SQ2.


## Obtaining Firmware

To obtain a full firmware package yourself, you need access to either a (full) Windows (recovery) image or a Windows installation.
Recovery images can be obtained from https://support.microsoft.com/en-us/surface-recovery-image.
The full firmware package can then be obtained via `./scripts/getfw.py`.
In particular, run
```sh
./scripts/getfw.py -w <path-to-windows-root>
```
where `<path-to-windows-root>` is the path to the extracted Windows recovery image or your Windows installation (i.e. C:\\ drive).
In case your Windows installation is encrypted with BitLocker, you will need to run this script from inside WSL.
By default, the final firmware tree is provided in `./out`.
See `./scripts/getfw.py --help` for more information.

Note: This repository contains submodules, so make sure you run
```
git submodule init
git submodule update
```
after cloning.


## Licensing

We do not have an explicit re-distribution license for some of the firmware files provided here, in particular the ones specific to the Microsoft Surface Pro X.
These rights lie by Microsoft and/or Qualcomm.
However, many of these files are signed in a way that make them usable only on the Surface Pro X.
The files here are provided exclusively for convenience (especially allowing easy creation of distribution packages) as owners of the Surface Pro X can obtain these themselves via the provided script.
