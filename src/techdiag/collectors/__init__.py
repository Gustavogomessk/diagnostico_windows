from .battery import BatteryCollector
from .bsod import BsodCollector
from .drivers import DriversCollector
from .eventlogs import EventLogsCollector
from .hardware import HardwareCollector
from .memory import MemoryCollector
from .network import NetworkCollector
from .reboots import RebootsCollector
from .sensors import SensorsCollector
from .storage import StorageCollector
from .temp_files import TempFilesCollector
from .windows_integrity import WindowsIntegrityCollector


def all_collectors():
    return [HardwareCollector(), SensorsCollector(), StorageCollector(), MemoryCollector(), BatteryCollector(), TempFilesCollector(),
            DriversCollector(), NetworkCollector(), BsodCollector(), RebootsCollector(), EventLogsCollector(),
            WindowsIntegrityCollector()]
