import logging
import subprocess
import traceback
from datetime import UTC, datetime

from aoneconnector.aone_connector import AlcotestOneConnectorSerial
from install_interface.data.installation_process_data import ProcessState
from install_interface.interface.base_installation import BaseInstallation
from install_interface.tools.progress_updater import ProgressStepUpdater

import xplore_installer
from xplore_installer.xplore8300_installer_constants import (
    BOOTLOADER_SW_VERSION,
    END_PROGRESS,
    FLASHER_EXE_PATH,
    TIME_ADDRESS,
    XPLORE8300_DEVICE_TYPE_BIN,
)

logger = logging.getLogger(f'{xplore_installer.library_name}.Xplore8300Installation')


class Xplore8300Installation(BaseInstallation):
    """
    Xplore8300 update task.

    Args:
        threading (Thread): thread
    """

    def get_flasher_exe_path(self) -> str:
        return FLASHER_EXE_PATH

    def _setup(self):
        pass

    def _teardown(self):
        pass

    def _install_steps(self, installation_steps):
        step_progress = END_PROGRESS / len(installation_steps)
        step_end_progress = step_progress
        for install_step in installation_steps:
            if self._install_process.cancel and self._install_process.is_cancelable:
                logger.warning('Installation was canceled. Will stop now')
                break
            logger.info(f'Installing {install_step.software_package} to {install_step.instance_device_id}')
            progress_updater = ProgressStepUpdater(self._install_process, install_step.expected_duration, step_end_progress)
            progress_updater.start()
            try:
                self._run_install_step(install_step)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error(f"Error occured during installation: {traceback.format_exc()}")
                self._install_process.info_message = f"Update error: {ex}"
                self._install_process.state = ProcessState.error
                break
            finally:
                step_end_progress += step_progress
                progress_updater.finish()
        else:
            try:
                self._set_device_rtc_to_host_time()
            except Exception as error:
                logger.warning("Could not set device RTC: %s", error)
            self._install_process.info_message = "Update finished successfully."
            self._install_process.state = ProcessState.success

    def _find_device_ids_by_component_type(self, component_type):
        # components_types = {"A7000": ALCOTEST7000_DEVICE_TYPE_BIN, "A7000 med": ALCOTEST7000_MED_DEVICE_TYPE_BIN}
        components_types = {"Xplore8300": XPLORE8300_DEVICE_TYPE_BIN}
        device_ids_by_component_type = []
        for component in components_types:
            if component == component_type:
                device_ids_by_component_type.append(components_types[component])
        return device_ids_by_component_type

    def _set_device_rtc_to_host_time(self) -> None:
        """Set the RTC to the host UTC time, with zero fractions of a second."""
        now = datetime.now(UTC)
        utc_timestamp = f"{now:%Y-%m-%dT%H:%M:%S}.00Z"
        com_port = self._target_information.connection_information
        xplore_connector = AlcotestOneConnectorSerial({"comport": com_port})
        xplore_connector.setup()
        try:
            logger.info("Setting device RTC to %s", utc_timestamp)
            xplore_connector.put(TIME_ADDRESS, utc=utc_timestamp)
        finally:
            xplore_connector.teardown()

    def _run_install_step(self, install_step):
        """
        Runs the Xplore8300 update process given by 'cmd' in a hidden process window.

        Args:
            install_step (InstallStep): install step to be run on device
        """
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        process = None
        # DCA: original command: install_cmd = f'{self.get_flasher_exe_path()} "{install_step.software_package}" {install_step.parameters}'
        # DCA: COM port of the intended device needs to be communicated to the exe here. Is this correct?
        com_port = self._target_information.connection_information
        if self._firmware_started or not str(self._target_information.software_version).startswith(
            BOOTLOADER_SW_VERSION
        ):
            xplore_connector = AlcotestOneConnectorSerial({"comport": com_port})
            xplore_connector.setup()
            xplore_connector.put("/service/startUpdate")
            xplore_connector.teardown()

        install_cmd = f'{self.get_flasher_exe_path()} -c port="{com_port}" -rdu -e all -d "{install_step.software_package}" -v -s'
        try:
            self._install_process.is_cancelable = False
            process = subprocess.Popen(install_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, startupinfo=startupinfo)
            out, err = process.communicate(timeout=install_step.timeout_duration)
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, install_cmd)
        except subprocess.SubprocessError:
            if process:
                process.kill()
                out, err = process.communicate()
                self._log_installation_output(out, err, logger.error)
            raise
        else:
            self._log_installation_output(out, err, logger.debug)
        finally:
            self._install_process.is_cancelable = True

    @staticmethod
    def _log_installation_output(stdout: bytes, stderr: bytes, logmethod) -> None:
        """
        Method logs installation output and fails gracefully if this is not possible
        """
        try:
            logmethod(stdout.decode(errors='replace'))
        except Exception as e:  # pragma: no cover - logging failure
            logger.warning(f'Could not log stdout. Exception: {e}')
        try:
            logmethod(stderr.decode(errors='replace'))
        except Exception as e:  # pragma: no cover - logging failure
            logger.warning(f'Could not log stderr. Exception: {e}')
