from PyInstaller.utils.hooks import collect_data_files, logger
from xplore_installer import binaries as installer_binaries_package

datas = collect_data_files(installer_binaries_package.__name__)
logger.info(f"Adding {datas}")