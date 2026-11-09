from dataclasses import dataclass
from enum import IntEnum
from functools import wraps
from threading import Thread
from unittest.mock import Mock
from xml.etree import ElementTree


class NotApplicable(Exception):
    pass


class LoadFileError(Exception):
    pass


class LoadDeviceInformationError(Exception):
    pass


class ActionProhibited(Exception):
    pass


@dataclass
class InstallationFileData:
    installation_name: str
    library_name: str
    software_version: str
    file_path: str
    install_configurations: list[str]
    device_type: str
    internal_installation_details: object = None
    rnd_right_required: bool = False

    @property
    def assigned_library_name(self) -> str:
        return self.library_name


@dataclass
class InstallationInstructionData:
    installation_file_data: InstallationFileData
    chosen_configuration: str | None = None

    @property
    def assigned_library_name(self) -> str:
        return self.installation_file_data.assigned_library_name


@dataclass
class TargetInformationData:
    connection_information: str
    library_name: str
    software_version: str
    device_type: str
    serial_number: str
    equipment_number: str

    @property
    def assigned_library_name(self) -> str:
        return self.library_name


class ProcessState(IntEnum):
    error = -1
    init = 0
    success = 1
    running = 2
    canceling = 3
    canceled = 4


@dataclass
class InstallationProcessData:
    progress: int = 0
    info_message: str = ""
    state: ProcessState = ProcessState.init
    cancel: bool = False
    is_cancelable: bool = True
    estimated_duration_sec: int | None = None


@dataclass
class InstallStep:
    name: str
    instance_device_id: str
    software_package: str
    parameters: str
    expected_duration: int
    timeout_duration: int


class BaseInstallerInterface:
    pass


def ensure_correct_library_decorator(function):
    @wraps(function)
    def check_library(installer, instructions, target):
        library_name = type(installer).__name__
        if (
            instructions.assigned_library_name != library_name
            or target.assigned_library_name != library_name
        ):
            raise ActionProhibited("Library names do not match.")
        return function(installer, instructions, target)

    return check_library


class BaseInstallation(Thread):
    """Model the installation lifecycle used by these tests."""

    def __init__(self, install_process, install_instructions, target_information, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.daemon = True
        self._install_process = install_process
        self._install_instructions = install_instructions
        self._target_information = target_information

    def _determine_installation_steps(self):
        raise NotImplementedError("Set installation steps in the test.")

    def _find_hardware_version(self):
        raise NotImplementedError("Hardware version detection is not available.")

    def run(self) -> None:
        process = self._install_process
        try:
            self._setup()
            steps = self._determine_installation_steps()
            process.estimated_duration_sec = sum(step.expected_duration for step in steps)
            process.state = ProcessState.running
            self._install_steps(steps)
        except Exception as error:
            process.state = ProcessState.error
            process.info_message = f"Could not install. Unhandled Exception: {error}"
        finally:
            self._teardown()
            if process.cancel or process.state == ProcessState.canceling:
                process.state = ProcessState.canceled
                process.info_message = "Installation canceled."
            elif process.state == ProcessState.running:
                process.state = ProcessState.success
                process.info_message = "Installation completed successfully."
            process.progress = 100


def load_install_package(path: str):
    root = ElementTree.parse(path).getroot()
    if not all(root.get(name) for name in ("name", "deviceTypeBin", "productVersion")):
        raise ElementTree.ParseError("Package attributes are missing.")
    package = Mock(spec=["get", "find", "xpath", "tag"], tag=root.tag)
    package.get.side_effect = root.get
    package.find.side_effect = root.find
    package.xpath.side_effect = lambda query: root.findall(query.removeprefix(f"/{root.tag}/"))
    return package
