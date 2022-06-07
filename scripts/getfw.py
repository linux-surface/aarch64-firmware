#!/usr/bin/env python

import argparse
import json
import os
import shutil
import subprocess
import types
import urllib.request
import tempfile

from pathlib import Path


ATH10K_BOARD_FILE = "bdwlan.b58"
ATH10K_BOARD_NAME = "bus=snoc,qmi-board-id=ff,qmi-chip-id=30224"

PATH_PLATFORM = "msft/surface/pro-x-sq2"
PATH_VENUS = "venus-5.2"

PATH_WDSFR = "Windows/System32/DriverStore/FileRepository"
PATH_THIRDPARTY = Path(__file__).parent / 'third-party'

URL_AARCH64_FIRMWARE_REPO = "https://raw.githubusercontent.com/linux-surface/aarch64-firmware/main/firmware"
URL_LINUX_FIRMWARE_REPO = "https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain"


class Logger:
    log_pfx = [
        "==> ",
        " -> ",
        "    ",
    ]

    def __init__(self, level=0):
        self.level = level
        self.pfx = Logger.log_pfx[level]

    def sub(self):
        return Logger(level=self.level + 1)

    def info(self, msg):
        print(f"{self.pfx}{msg}")

    def warn(self, msg):
        print(f"{self.pfx}WARNING: {msg}")

    def error(self, msg):
        print(f"{self.pfx}ERROR: {msg}")


class Firmware:
    """Basic firmware source description"""

    @staticmethod
    def _filemap(files):
        if isinstance(files, list):
            return {x: x for x in files}
        elif isinstance(files, dict):
            return files
        else:
            raise Exception("invalid file map")

    def __init__(self, name, target_directory):
        self.name = name
        self.target_directory = Path(target_directory)

    def get(self, log, args):
        raise NotImplementedError()


class WindowsDriverFirmware(Firmware):
    """Firmware extracted from Windows Driver Store File Repository"""

    def __init__(self, name, target_directory, source_directory, files):
        super().__init__(name, target_directory)

        self.source_directory = source_directory
        self.files = Firmware._filemap(files)

    def _find_source_directory(self, args):
        for p in args.path_wdsfr.iterdir():
            if p.name.startswith(self.source_directory):
                return p

    def get(self, log, args):
        base = self._find_source_directory(args)

        for s, t in self.files.items():
            src = base / s
            tgt = args.path_out / self.target_directory / t

            tgt.parent.mkdir(parents=True, exist_ok=True)

            log.info(f"copying '{src}' to '{self.target_directory / t}'")
            shutil.copy(src, tgt)


class DownloadFirmware(Firmware):
    """Firmware downloaded from the internet"""

    def __init__(self, name, target_directory, source_url, files):
        super().__init__(name, target_directory)

        self.source_url = source_url
        self.files = Firmware._filemap(files)

    def get(self, log, args):
        for s, t in self.files.items():
            src = f"{self.source_url}/{s}"
            tgt = args.path_out / self.target_directory / t

            tgt.parent.mkdir(parents=True, exist_ok=True)

            log.info(f"downloading '{src}' to '{self.target_directory / t}'")
            urllib.request.urlretrieve(src, tgt)


class Patch:
    def __init__(self, name, fn) -> None:
        self.name = name
        self.fn = fn

    def apply(self, log, args):
        self.fn(log, args)


def patch_venus_extract(log, args):
    pil_splitter = PATH_THIRDPARTY / "qcom-mbn-tools" / "pil-splitter.py"
    mbn_venus = args.path_out / 'qcom' / PATH_PLATFORM / 'qcvss8180.mbn'
    dir_venus = args.path_out / 'qcom' / PATH_VENUS

    dir_venus.mkdir(parents=True, exist_ok=True)

    subprocess.call([pil_splitter, mbn_venus, dir_venus / 'venus'])
    shutil.copy(mbn_venus, dir_venus / 'venus.mbn')


def patch_ath10k_board(log, args):
    """
    Create ath10k board-2.bin from single bdf file. Note that we need to create
    an entry matching the chip ID instead of the board ID. The board ID is
    0xff, which seems to be used on multiple chips and could be an indicator
    that it should not be used for matching (i.e. redirecting to the chip ID).
    It is currently unclear which bdf file to use for this. Any except the
    '.b5f' files seems to work. Those exceptions cause instant crashes of the
    remote processor.
    """

    ath10k_bdencoder = PATH_THIRDPARTY / "qca-swiss-army-knife" / "tools" / "scripts" / "ath10k" / "ath10k-bdencoder"
    path_boards = args.path_out.resolve() / 'ath10k' / 'WCN3990' / 'hw1.0' / 'boards'
    path_board_out = args.path_out.resolve() / 'ath10k' / 'WCN3990' / 'hw1.0' / 'board-2.bin'

    spec = [
        {
            "data": str(path_boards / ATH10K_BOARD_FILE),
            "names": [ATH10K_BOARD_NAME],
        }
    ]

    file = tempfile.NamedTemporaryFile()

    with open(file.name, "w") as fd:
        json.dump(spec, fd)

    subprocess.call([ath10k_bdencoder, "-c", file.name, "-o", path_board_out])
    shutil.rmtree(path_boards)


def patch_ath10k_firmware(log, args):
    """
    Patch the upstream firmware-5.bin file. The firmware running on the WiFi
    processor seems to send single events per channel instead of event pairs.
    without the 'single-chan-info-per-channel' option set in firmware-5.bin,
    the ath10k driver will complain (somewhat cryptically) that it only
    received a single event. Setting this option shuts up the warning and
    generally seems like the right thing to do.

    See also: https://www.spinics.net/lists/linux-wireless/msg178387.html.
    """

    ath10k_fwencoder = PATH_THIRDPARTY / "qca-swiss-army-knife" / "tools" / "scripts" / "ath10k" / "ath10k-fwencoder"
    fw5_bin = args.path_out / 'ath10k' / 'WCN3990' / 'hw1.0' / 'firmware-5.bin'

    args = ['--modify', '--features=wowlan,mgmt-tx-by-ref,non-bmi,single-chan-info-per-channel', fw5_bin]

    subprocess.call([ath10k_fwencoder] + args)


def patch_qca_bt_symlinks(log, args):
    """
    For some reason the revision/chip ID seems to be read as 0x01 instead of
    0x21. Windows drivers do only provide the files for revision 0x21 and there
    also doesn't seem to be a revision 0x01. Symlinking new 0x01 files to their
    existing 0x21 counterparts works.
    """

    base_path = args.path_out / 'qca'

    files = [
        "crbtfw21.tlv",
        "crnv21.b3c",
        "crnv21.b44",
        "crnv21.b45",
        "crnv21.b46",
        "crnv21.b47",
        "crnv21.b71",
        "crnv21.bin",
    ]

    for file in files:
        link = base_path / file.replace('21', '01')
        link.unlink(missing_ok=True)
        link.symlink_to(file)


sources = [
    # PD Maps for pd-mapper.service
    DownloadFirmware("pd-maps", f"qcom/{PATH_PLATFORM}", f"{URL_AARCH64_FIRMWARE_REPO}/qcom/{PATH_PLATFORM}", [
        "adspr.jsn",
        "adspua.jsn",
        "cdspr.jsn",
        "charger.jsn",
        "modemr.jsn",
        "modemuw.jsn",
    ]),

    # Bluetooth
    WindowsDriverFirmware("bluetooth", f"qca", "qcbtfmuart8180", [
        "crbtfw21.tlv",
        "crnv21.b3c",
        "crnv21.b44",
        "crnv21.b45",
        "crnv21.b46",
        "crnv21.b47",
        "crnv21.b71",
        "crnv21.bin",
    ]),

    # GPU (Adreno 680)
    DownloadFirmware("gpu/base", "qcom", f"{URL_AARCH64_FIRMWARE_REPO}/qcom", [
        "a680_gmu.bin",
        "a680_sqe.fw",
    ]),
    WindowsDriverFirmware("gpu/vendor", f"qcom/{PATH_PLATFORM}", "qcdx8180", [
        "qcdxkmsuc8180.mbn",
        "qcvss8180.mbn",
    ]),

    # WLAN
    WindowsDriverFirmware("wlan/vendor", f"qcom/{PATH_PLATFORM}", "qcwlan8180", [
        "wlanmdsp.mbn",
    ]),
    WindowsDriverFirmware("wlan/ath10k/board", "ath10k/WCN3990/hw1.0/boards", "qcwlan8180", [
        "bdwlan.b5f",
        "bdwlan.b36",
        "bdwlan.b37",
        "bdwlan.b46",
        "bdwlan.b47",
        "bdwlan.b48",
        "bdwlan.b58",
        "bdwlan.b71",
        "bdwlan.bin",
        "bdwlanu.b5f",
        "bdwlanu.b58",
    ]),
    DownloadFirmware("wlan/ath10k/firmware-5", f"ath10k/WCN3990/hw1.0", f"{URL_LINUX_FIRMWARE_REPO}/ath10k/WCN3990/hw1.0", [
        "firmware-5.bin",
    ]),

    # MCFG (file map based on inf contents)
    WindowsDriverFirmware("mcfg", f"qcom/{PATH_PLATFORM}", "surfaceprox_mcfg", {
        "MCFG/MCFG.1": "modem_pr/mcfg/configs/mcfg_sw/oem_sw.txt",
        "MCFG/MCFG.2": "modem_pr/mcfg/configs/mcfg_sw/mbn_sw.txt",
        "MCFG/MCFG.3": "modem_pr/mcfg/configs/mcfg_sw/mbn_sw.dig",
        "MCFG/MCFG.4": "modem_pr/mcfg/configs/mcfg_sw/generic/APAC/DCM/Commercial/mcfg_sw.mbn",
        "MCFG/MCFG.5": "modem_pr/mcfg/configs/mcfg_sw/generic/APAC/SBM/Commercial/mcfg_sw.mbn",
        "MCFG/MCFG.6": "modem_pr/mcfg/configs/mcfg_sw/generic/common/ROW/Commercial/mcfg_sw.mbn",
        "MCFG/MCFG.7": "modem_pr/mcfg/configs/mcfg_sw/generic/Microsoft/Cambria/SW/CMCC/Commercial/MSFT_OpenMkt/mcfg_sw.mbn",
        "MCFG/MCFG.8": "modem_pr/mcfg/configs/mcfg_sw/generic/Microsoft/Cambria/SW/CT/Commercial/MSFT_OpenMkt/mcfg_sw.mbn",
        "MCFG/MCFG.9": "modem_pr/mcfg/configs/mcfg_sw/generic/Microsoft/Cambria/SW/CU/Commercial/MSFT_OpenMkt/mcfg_sw.mbn",
        "MCFG/MCFG.10": "modem_pr/mcfg/configs/mcfg_sw/generic/Microsoft/Cambria/SW/GIGSKY/Commercial/mcfg_sw.mbn",
        "MCFG/MCFG.11": "modem_pr/mcfg/configs/mcfg_sw/generic/Microsoft/Cambria/SW/mte/factory/mcfg_sw.mbn",
        "MCFG/MCFG.12": "modem_pr/mcfg/configs/mcfg_sw/generic/Microsoft/Cambria/SW/rel/sc8180x.gen.prod/common/mcfg_sw.mbn",
        "MCFG/MCFG.13": "modem_pr/mcfg/configs/mcfg_sw/generic/NA/ATT/FirstNet/mcfg_sw.mbn",
        "MCFG/MCFG.14": "modem_pr/mcfg/configs/mcfg_sw/generic/NA/ATT/Non_VoLTE/mcfg_sw.mbn",
        "MCFG/MCFG.15": "modem_pr/mcfg/configs/mcfg_sw/generic/NA/Verizon/CDMAless/mcfg_sw.mbn",
        "MCFG/MCFG.16": "modem_pr/mcfg/configs/mcfg_hw/mbn_hw.txt",
        "MCFG/MCFG.17": "modem_pr/mcfg/configs/mcfg_hw/mbn_hw.dig",
        "MCFG/MCFG.18": "modem_pr/mcfg/configs/mcfg_hw/generic/common/SC8180X/cmcc_subsidized/SR_DSDS/mcfg_hw.mbn",
        "MCFG/MCFG.19": "modem_pr/mcfg/configs/mcfg_hw/generic/common/SC8180X/LA/7+7_mode/SR_DSDS/mcfg_hw.mbn",
        "MCFG/MCFG.20": "modem_pr/mcfg/configs/mcfg_hw/generic/common/SC8180X/LA/DSDS/mcfg_hw.mbn",
        "MCFG/MCFG.21": "modem_pr/mcfg/configs/mcfg_hw/generic/common/SC8180X/LA/SS/mcfg_hw.mbn",
        "MCFG/MCFG.22": "modem_pr/mcfg/configs/mcfg_hw/generic/common/SC8180X/WD/7+7_mode/SR_DSDS/mcfg_hw.mbn",
        "MCFG/MCFG.23": "modem_pr/mcfg/configs/mcfg_hw/generic/common/SC8180X/WD/DSSA/mcfg_hw.mbn",
        "MCFG/MCFG.24": "modem_pr/mcfg/configs/mcfg_hw/generic/common/SC8180X/WD/SS/mcfg_hw.mbn",
        "MCFG/MCFG.25": "modem_pr/mcfg/configs/mcfg_hw/generic/common/SC8180X/WP8/7+7_mode/SR_DSDS/mcfg_hw.mbn",
        "MCFG/MCFG.26": "modem_pr/mcfg/configs/mcfg_hw/generic/common/SC8180X/WP8/SS/mcfg_hw.mbn",
        "MCFG/MCFG.27": "modem_pr/mcfg/configs/mcfg_hw/generic/Microsoft/Cambria/hw/mte/factory/mcfg_hw.mbn",
        "MCFG/MCFG.28": "modem_pr/mcfg/configs/mcfg_hw/generic/Microsoft/Cambria/hw/rel/sc8180x.gen.prod/common/mcfg_hw.mbn"
    }),

    # ADSP
    WindowsDriverFirmware("adsp/vendor", f"qcom/{PATH_PLATFORM}", "surfaceprox_subextadsp", [
        "qcadsp8180.mbn",
    ]),
    # TODO: ADSP directory?

    # CDSP
    WindowsDriverFirmware("cdsp/vendor", f"qcom/{PATH_PLATFORM}", "surfaceprox_subextcdsp", [
        "qccdsp8180.mbn",
    ]),
    # TODO: CDSP directory?

    # MPSS
    WindowsDriverFirmware("mpss/vendor", f"qcom/{PATH_PLATFORM}", "surfaceprox_subextmpss", [
        "qcmpss8180.mbn",
        "qcmpss8180_nm.mbn",
    ]),
    WindowsDriverFirmware("mpss/library", f"qcom/{PATH_PLATFORM}", "surfaceprox_subextmpss", [
        "qdsp6m.qdb",
    ]),
]

patches = [
    Patch('venus', patch_venus_extract),
    Patch('ath10k/board-2.bin', patch_ath10k_board),
    Patch('ath10k/firmware-5.bin', patch_ath10k_firmware),
    Patch('qca/bt', patch_qca_bt_symlinks),
]


def gather(log, args, sources):
    for src in sources:
        log.info(f"{src.name}")
        src.get(log.sub(), args)


def patch(log, args, patches):
    for patch in patches:
        log.info(f"{patch.name}")
        patch.apply(log.sub(), args)


def main():
    if os.geteuid() == 0:
        print("Please do not run this script as root!")
        exit(1)

    parser = argparse.ArgumentParser(description="Gather firmware files for Surface Pro X (SQ2)")
    parser.add_argument("-w", "--windows", help="Windows root directory", required=True)
    parser.add_argument("-o", "--output", help="output directory", default="out")
    cli_args = parser.parse_args()

    args = types.SimpleNamespace()
    args.path_wdsfr = Path(cli_args.windows) / PATH_WDSFR
    args.path_out = Path(cli_args.output)

    log = Logger()

    log.info("retrieving base firmware files")
    gather(log.sub(), args, sources)

    log.info("patching firmware files")
    patch(log.sub(), args, patches)

    log.info("done!")


if __name__ == '__main__':
    main()
