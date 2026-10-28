import sys
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.install_interface_mocks import (
    ActionProhibited,
    BaseInstallation,
    BaseInstallerInterface,
    InstallationFileData,
    InstallationInstructionData,
    InstallationProcessData,
    LoadDeviceInformationError,
    LoadFileError,
    NotApplicable,
    ProcessState,
    TargetInformationData,
    ensure_correct_library_decorator,
    load_install_package,
)


@pytest.fixture
def installer_library(monkeypatch: pytest.MonkeyPatch):
    dependencies = {
        "install_interface.data.installation_file_data": Mock(
            InstallationFileData=InstallationFileData
        ),
        "install_interface.data.installation_instruction_data": Mock(
            InstallationInstructionData=InstallationInstructionData
        ),
        "install_interface.data.installation_process_data": Mock(
            InstallationProcessData=InstallationProcessData, ProcessState=ProcessState
        ),
        "install_interface.data.target_information_data": Mock(
            TargetInformationData=TargetInformationData
        ),
        "install_interface.installer_exceptions": Mock(
            NotApplicable=NotApplicable,
            LoadFileError=LoadFileError,
            LoadDeviceInformationError=LoadDeviceInformationError,
            ActionProhibited=ActionProhibited,
        ),
        "install_interface.interface.base_installer_interface": Mock(
            BaseInstallerInterface=BaseInstallerInterface,
            ensure_correct_library_decorator=ensure_correct_library_decorator,
        ),
        "install_interface.interface.base_installation": Mock(BaseInstallation=BaseInstallation),
        "install_interface.tools.progress_updater": Mock(ProgressStepUpdater=Mock()),
        "install_interface.normalized_installation_package": Mock(
            package_constants=Mock(
                ROOT_ATTRIBUTE_PRODUCT_VERSION="productVersion",
                ROOT_ATTRIBUTE_PRODUCT_ID="deviceTypeBin",
                ROOT_ATTRIBUTE_INSTALLATION_NAME="name",
                CONFIGURATION_ATTRIBUTE_NAME="name",
                XML_CONFIGURATION_PATH="/ProductSoftwarePackage/Configurations/Configuration",
            ),
            package_parser=Mock(load_install_package=Mock(side_effect=load_install_package)),
        ),
        "aoneconnector": Mock(),
        "aoneconnector.aone_connector": Mock(),
        "customcertstore": Mock(),
        "customcertstore.definitions": Mock(),
        "customcertstore.certificate_store": Mock(),
    }
    for name, dependency in dependencies.items():
        monkeypatch.setitem(sys.modules, name, dependency)

    from xplore_installer.xplore8300_install_lib import Xplore8300InstallLib

    return Xplore8300InstallLib()


def test_firmware_file_extensions(installer_library) -> None:
    assert installer_library.firmware_file_extensions == [".ipex"]


@pytest.fixture
def installation_file(tmp_path: Path) -> Path:
    path = tmp_path / "firmware.ipex"
    path.write_text(
        """<ProductSoftwarePackage name="X-plore update"
                        deviceTypeBin="2776" productVersion="1.2.3">
  <Components>
    <Component id="Xplore8300">
      <Target name="Firmware" moduleSoftwarePackage="firmware.bin"
              expected_duration="10" timeout_duration="30"/>
    </Component>
  </Components>
  <Configurations>
    <Configuration name="firmware" description="Install firmware" authorizationLevel="any">
      <InstallStep type="install"><InstallationTask Target="Firmware"/></InstallStep>
    </Configuration>
    <Configuration name="full" description="Install all software" authorizationLevel="expert">
      <InstallStep type="install"><InstallationTask Target="Firmware"/></InstallStep>
    </Configuration>
  </Configurations>
</ProductSoftwarePackage>
""",
        encoding="utf-8",
    )
    return path


def test_load_file_returns_installation_data(installer_library, installation_file: Path) -> None:
    from install_interface.data.installation_file_data import InstallationFileData

    result = installer_library.load_file(str(installation_file))

    assert isinstance(result, InstallationFileData)
    assert result.installation_name == "X-plore update"
    assert result.assigned_library_name == "Xplore8300InstallLib"
    assert result.software_version == "1.2.3"
    assert result.file_path == str(installation_file)
    assert result.install_configurations == ["firmware", "full"]
    assert result.device_type == "2776"
    assert result.rnd_right_required is False
    assert result.internal_installation_details.tag == "ProductSoftwarePackage"
    assert (
        result.internal_installation_details.find("Components/Component/Target").get(
            "moduleSoftwarePackage"
        )
        == "firmware.bin"
    )


def test_load_file_rejects_unsupported_extension(
    installer_library, installation_file: Path
) -> None:
    from install_interface.installer_exceptions import NotApplicable

    unsupported_file = installation_file.with_suffix(".zip")
    installation_file.rename(unsupported_file)

    with pytest.raises(NotApplicable):
        installer_library.load_file(str(unsupported_file))


@pytest.mark.xfail(strict=True, reason="Product ID is not checked.")
def test_load_file_rejects_other_product(installer_library, installation_file: Path) -> None:
    from install_interface.installer_exceptions import NotApplicable

    content = installation_file.read_text(encoding="utf-8")
    installation_file.write_text(
        content.replace('deviceTypeBin="2776"', 'deviceTypeBin="2402"'), encoding="utf-8"
    )

    with pytest.raises(NotApplicable):
        installer_library.load_file(str(installation_file))


@pytest.mark.xfail(strict=True, reason="File errors are not wrapped.")
def test_load_file_reports_missing_file(installer_library, tmp_path: Path) -> None:
    from install_interface.installer_exceptions import LoadFileError

    with pytest.raises(LoadFileError):
        installer_library.load_file(str(tmp_path / "missing.ipex"))


@pytest.mark.parametrize(
    "content",
    ["<ProductSoftwarePackage", "<ProductSoftwarePackage/>"],
    ids=["invalid-xml", "invalid-schema"],
)
@pytest.mark.xfail(strict=True, reason="File errors are not wrapped.")
def test_load_file_reports_invalid_file(
    installer_library, installation_file: Path, content: str
) -> None:
    from install_interface.installer_exceptions import LoadFileError

    installation_file.write_text(content, encoding="utf-8")

    with pytest.raises(LoadFileError):
        installer_library.load_file(str(installation_file))


@pytest.fixture
def target_connector(installer_library, monkeypatch: pytest.MonkeyPatch):
    connector = Mock(spec=["setup", "get", "teardown"])
    connector.get.side_effect = [
        {"serial_number": "ABCD-1234"},
        {"fw_version": "1.2.3"},
        {"device_id": 2776},
    ]
    factory = Mock(return_value=connector)
    monkeypatch.setattr(
        sys.modules[type(installer_library).__module__], "AlcotestOneConnectorSerial", factory
    )
    return SimpleNamespace(connection=connector, factory=factory)


def test_get_target_information_returns_device_data(installer_library, target_connector) -> None:
    from install_interface.data.target_information_data import TargetInformationData

    result = installer_library.get_target_information("COM3")

    assert isinstance(result, TargetInformationData)
    assert result.connection_information == "COM3"
    assert result.assigned_library_name == "Xplore8300InstallLib"
    assert result.software_version == "1.2.3"
    assert result.device_type == "2776"
    assert result.serial_number == "ABCD-1234"
    assert result.equipment_number == "NO_EQUIPMENT_NUMBER"
    target_connector.factory.assert_called_with({"comport": "COM3"})
    target_connector.connection.setup.assert_called()
    target_connector.connection.teardown.assert_called()


def test_get_target_information_rejects_unsupported_connection(
    installer_library, target_connector
) -> None:
    from install_interface.installer_exceptions import NotApplicable

    with pytest.raises(NotApplicable):
        installer_library.get_target_information("USB3")

    target_connector.factory.assert_not_called()


@pytest.mark.xfail(strict=True, reason="Device ID is not checked.")
def test_get_target_information_rejects_other_device(installer_library, target_connector) -> None:
    from install_interface.installer_exceptions import NotApplicable

    target_connector.connection.get.side_effect = [
        {"serial_number": "ABC123"},
        {"fw_version": "1.2.3"},
        {"device_id": 2402},
    ]

    with pytest.raises(NotApplicable):
        installer_library.get_target_information("COM3")

    target_connector.connection.teardown.assert_called()


@pytest.mark.xfail(strict=True, reason="Connection errors use NotApplicable.")
def test_get_target_information_reports_connection_error(
    installer_library, target_connector
) -> None:
    from install_interface.installer_exceptions import LoadDeviceInformationError

    target_connector.connection.setup.side_effect = OSError("Port unavailable")

    with pytest.raises(LoadDeviceInformationError):
        installer_library.get_target_information("COM3")

    target_connector.connection.get.assert_not_called()


@pytest.mark.parametrize("read_index", range(3), ids=["serial-number", "version", "device-type"])
@pytest.mark.xfail(strict=True, reason="Timeouts use NotApplicable.")
def test_get_target_information_reports_timeout(
    installer_library, target_connector, read_index: int
) -> None:
    from install_interface.installer_exceptions import LoadDeviceInformationError

    responses = [
        {"serial_number": "ABC123"},
        {"fw_version": "1.2.3"},
        {"device_id": 2776},
    ]
    target_connector.connection.get.side_effect = responses[:read_index] + [
        TimeoutError("No reply")
    ]

    try:
        with pytest.raises(LoadDeviceInformationError):
            installer_library.get_target_information("COM3")
    finally:
        target_connector.connection.teardown.assert_called()


@pytest.mark.xfail(strict=True, reason="Read errors are not wrapped.")
def test_get_target_information_reports_read_error(installer_library, target_connector) -> None:
    from install_interface.installer_exceptions import LoadDeviceInformationError

    target_connector.connection.get.side_effect = OSError("Read failed")

    try:
        with pytest.raises(LoadDeviceInformationError):
            installer_library.get_target_information("COM3")
    finally:
        target_connector.connection.teardown.assert_called()


@pytest.mark.xfail(strict=True, reason="Invalid replies are not wrapped.")
def test_get_target_information_reports_invalid_response(
    installer_library, target_connector
) -> None:
    from install_interface.installer_exceptions import LoadDeviceInformationError

    target_connector.connection.get.side_effect = [None]

    try:
        with pytest.raises(LoadDeviceInformationError):
            installer_library.get_target_information("COM3")
    finally:
        target_connector.connection.teardown.assert_called()


@pytest.fixture
def installation_inputs(installer_library):
    from install_interface.data.installation_file_data import InstallationFileData
    from install_interface.data.installation_instruction_data import InstallationInstructionData
    from install_interface.data.target_information_data import TargetInformationData

    def create(
        file_library: str = "Xplore8300InstallLib", target_library: str = "Xplore8300InstallLib"
    ):
        file_data = InstallationFileData(
            "X-plore update", file_library, "1.2.3", "firmware.ipex", ["firmware"], "2776"
        )
        instructions = InstallationInstructionData(file_data)
        instructions.chosen_configuration = "firmware"
        target = TargetInformationData(
            "COM3", target_library, "1.0.0", "2776", "ABCD-1234", "NO_EQUIPMENT_NUMBER"
        )
        return SimpleNamespace(instructions=instructions, target=target)

    return create


@pytest.fixture
def installation_worker(installer_library, monkeypatch: pytest.MonkeyPatch):
    factory = Mock(return_value=Mock(spec=["start"]))
    monkeypatch.setattr(
        sys.modules[type(installer_library).__module__], "Xplore8300Installation", factory
    )
    return factory


def test_start_installation_starts_worker_and_returns_process_data(
    installer_library, installation_inputs, installation_worker
) -> None:
    from install_interface.data.installation_process_data import InstallationProcessData

    inputs = installation_inputs()

    result = installer_library.start_installation(inputs.instructions, inputs.target)

    assert isinstance(result, InstallationProcessData)
    installation_worker.assert_called_once()
    assert installation_worker.call_args.args == (result, inputs.instructions, inputs.target)
    installation_worker.return_value.start.assert_called_once()


def test_start_installation_creates_separate_process_data(
    installer_library, installation_inputs, installation_worker
) -> None:
    inputs = installation_inputs()

    first = installer_library.start_installation(inputs.instructions, inputs.target)
    second = installer_library.start_installation(inputs.instructions, inputs.target)

    assert first is not second


@pytest.mark.parametrize(
    ("file_library", "target_library"),
    [
        ("OtherInstallLib", "Xplore8300InstallLib"),
        ("Xplore8300InstallLib", "OtherInstallLib"),
        ("OtherInstallLib", "OtherInstallLib"),
    ],
    ids=["file-library", "target-library", "both-libraries"],
)
def test_start_installation_rejects_library_mismatch(
    installer_library,
    installation_inputs,
    installation_worker,
    file_library: str,
    target_library: str,
) -> None:
    from install_interface.installer_exceptions import ActionProhibited

    inputs = installation_inputs(file_library, target_library)

    with pytest.raises(ActionProhibited):
        installer_library.start_installation(inputs.instructions, inputs.target)

    installation_worker.assert_not_called()


def test_get_library_identification_returns_package_name_and_version(installer_library) -> None:
    result = installer_library._get_library_identification()

    assert result == f"xplore_installer {version('xplore-installer')}"
