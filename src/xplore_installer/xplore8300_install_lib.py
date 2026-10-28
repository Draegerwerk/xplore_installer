import logging
import os
from typing import List

from aoneconnector.aone_connector import AlcotestOneConnectorSerial, force_single_event_loop
from install_interface.data.installation_file_data import InstallationFileData
from install_interface.data.installation_instruction_data import InstallationInstructionData
from install_interface.data.installation_process_data import InstallationProcessData
from install_interface.data.target_information_data import TargetInformationData
from install_interface.installer_exceptions import (
    LoadDeviceInformationError,
    LoadFileError,
    NotApplicable,
)
from install_interface.interface.base_installer_interface import (
    BaseInstallerInterface,
    ensure_correct_library_decorator,
)
from install_interface.normalized_installation_package import package_constants, package_parser

import xplore_installer
from xplore_installer.stm32_bootloader import STM32Bootloader
from xplore_installer.xplore8300_installation import Xplore8300Installation
from xplore_installer.xplore8300_installer_constants import (
    BOOTLOADER_SERIAL_UNKNOWN,
    BOOTLOADER_SW_VERSION,
    DEVICE_BIN_ADDRESS,
    DEVICE_BIN_KEY,
    LIBRARY_FILE_EXTENSION,
    SERIAL_NUMBER_ADDRESS,
    SERIAL_NUMBER_KEY,
    SW_VERSION_ADDRESS,
    SW_VERSION_KEY,
    XPLORE8300_BOOTLOADER_PRODUCT_ID,
    XPLORE8300_BOOTLOADER_VERSION,
    XPLORE8300_DEVICE_TYPE_BIN,
)

logger = logging.getLogger(f'{xplore_installer.library_name}.Xplore8300InstallLib')


# def _read_cert_data()->CertificateData:
#     cert_store = CertificateStore(cert_store_definitions.PURPOSES.STANDARD_TECHNICIAN_DEVICE_CERTIFICATE)
#     pub, priv, pw = cert_store.get()
#     return CertificateData(pathlib.Path(pub).read_text(encoding="utf8"), pathlib.Path(priv).read_text(encoding="utf8"), pw)


class Xplore8300InstallLib(BaseInstallerInterface):
    """
    Xplore8300 installer interface.

    Available configurations:

    - full: full update -> firmware and system files with files check
    - full-no-check: firmware and file without files check
    - firmware: only firmware
    - files: only system files with files check
    - files-no-check: only system files without files check
    """

    def __init__(self):
        logger.info("start Xplore8300 install interface")
        force_single_event_loop()

    @property
    def firmware_file_extensions(self) -> List[str]:
        return [LIBRARY_FILE_EXTENSION]

    def load_file(self, path: str) -> InstallationFileData:
        """Load an installation file.

        Raise NotApplicable for unsupported file extensions.
        Raise LoadFileError for file read and format errors.
        """
        if os.path.splitext(path)[1] != LIBRARY_FILE_EXTENSION:
            raise NotApplicable(f'File "{path}" is not an Xplore8300 installation file.')

        try:
            return self._read_installation_file(path)
        except Exception as error:
            raise LoadFileError(f'Cannot load installation file "{path}".') from error

    def _read_installation_file(self, path: str) -> InstallationFileData:
        installation_package = package_parser.load_install_package(path)
        version = installation_package.get(package_constants.ROOT_ATTRIBUTE_PRODUCT_VERSION)
        device_type = installation_package.get(package_constants.ROOT_ATTRIBUTE_PRODUCT_ID)
        configuration_elements = installation_package.xpath(
            package_constants.XML_CONFIGURATION_PATH
        )
        configurations = [
            element.get(package_constants.CONFIGURATION_ATTRIBUTE_NAME)
            for element in configuration_elements
        ]
        package_name = installation_package.get(package_constants.ROOT_ATTRIBUTE_INSTALLATION_NAME)

        return InstallationFileData(
            package_name,
            Xplore8300InstallLib.__name__,
            version,
            path,
            configurations,
            device_type,
            installation_package,
        )

    def get_target_information(self, connection_information: str) -> TargetInformationData:
        """Load device information.

        Raise NotApplicable for unsupported ports.
        Raise LoadDeviceInformationError for connection and read errors.
        """
        if not connection_information.startswith("COM"):
            raise NotApplicable("Not a COM port.")

        try:
            return self._read_target_information(connection_information)
        except Exception as error:
            raise LoadDeviceInformationError(
                f"Cannot load device information on {connection_information}."
            ) from error

    def _read_target_information(self, connection_information: str) -> TargetInformationData:
        bootloader = STM32Bootloader(connection_information).probe()
        if bootloader is not None:
            bootloader_version, product_id = bootloader
            if (
                product_id == XPLORE8300_BOOTLOADER_PRODUCT_ID
                and bootloader_version == XPLORE8300_BOOTLOADER_VERSION
            ):
                logger.info("Device on %s is in bootloader mode.", connection_information)
                return TargetInformationData(
                    connection_information,
                    type(self).__name__,
                    BOOTLOADER_SW_VERSION,
                    XPLORE8300_DEVICE_TYPE_BIN,
                    BOOTLOADER_SERIAL_UNKNOWN,
                    "NO_EQUIPMENT_NUMBER",
                )
            logger.debug(
                "Bootloader on %s does not match X-plore 8300: chip %s, version %s.",
                connection_information,
                product_id,
                bootloader_version,
            )

        xplore_connector = AlcotestOneConnectorSerial({"comport": connection_information})
        xplore_connector.setup()
        try:
            serial_number = xplore_connector.get(SERIAL_NUMBER_ADDRESS, coap_timeout=1.0).get(
                SERIAL_NUMBER_KEY, "---"
            )
            software_version = xplore_connector.get(SW_VERSION_ADDRESS, coap_timeout=1.0).get(
                SW_VERSION_KEY, "---"
            )
            device_type_bin = xplore_connector.get(DEVICE_BIN_ADDRESS, coap_timeout=1.0).get(
                DEVICE_BIN_KEY, "---"
            )
        finally:
            xplore_connector.teardown()

        logger.info(
            "Found device with serial number: %s and software version: %s",
            serial_number,
            software_version,
        )
        return TargetInformationData(
            connection_information,
            Xplore8300InstallLib.__name__,
            software_version,
            str(device_type_bin),
            serial_number,
            "NO_EQUIPMENT_NUMBER",
        )

    @ensure_correct_library_decorator
    def start_installation(self, install_data: InstallationInstructionData,
                           target_data: TargetInformationData) -> InstallationProcessData:
        """
        Start Xplore8300 update process.

        Args:
            install_data: required installation instruction data
            target_data: required target information data

        Returns:
            InstallationProcessData: process data

        """
        install_process = InstallationProcessData()
        Xplore8300Installation(install_process, install_data, target_data, daemon=True).start()
        return install_process

    @staticmethod
    def _get_library_identification():
        return f"{xplore_installer.library_name} {xplore_installer.__version__}"
