import os
import pkg_resources

SW_VERSION_ADDRESS = "/service/fwVersion"
SW_VERSION_KEY = "fw_version"
SW_VERSION_KEY_MAJOR = 'major'
SW_VERSION_KEY_MINOR = 'minor'
SW_VERSION_KEY_PATCH = 'patch'
SERIAL_NUMBER_ADDRESS = "/service/serialNumber"
SW_UPDATE_START_ADDRESS = "/service/startUpdate"
TIME_ADDRESS = "/service/time"
SERIAL_NUMBER_KEY = "serial_number"
DEVICE_BIN_ADDRESS = "/serivce/deviceId"
DEVICE_BIN_KEY = "device_id"
LIBRARY_FILE_EXTENSION = ".ipex"
XPLORE8300_DEVICE_TYPE_BIN = "2776"
ALCOTEST7000_DEVICE_TYPE_BIN = "2297" # DCA: Remove
ALCOTEST7000_MED_DEVICE_TYPE_BIN = "2317" # DCA: Remove
INSTALLATION_NAME = "Xplore8300 Update"

FLASHER_EXE = "STM32_Programmer_CLI.exe" # DCA Change name?
# DCA: Unknown where this binary lives in installed updater for alcotest
# DCA: Binary for updater should live in some nested folders to handle the support files it generates
FLASHER_EXE_PATH = pkg_resources.resource_filename('xplore_installer', 'binaries/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe')

END_PROGRESS = 100
