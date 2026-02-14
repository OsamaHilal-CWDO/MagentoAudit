"""Audit modules for Magento health checks."""

from .cache import CacheAuditModule
from .capacity import CapacityLoadTestModule
from .backend import BackendProfilingModule
from .database import DatabaseAuditModule
from .extension_profiler import ExtensionProfilerModule
from .extensions import ExtensionsAuditModule
from .frontend import FrontendAuditModule
from .hotspots import MagentoHotspotsModule
from .indexers import IndexersAuditModule
from .logs import LogAnalysisModule
from .production import ProductionReadinessModule
from .security import SecurityAuditModule
from .sessions import SessionStorageAuditModule

__all__ = [
    "ExtensionsAuditModule",
    "ExtensionProfilerModule",
    "DatabaseAuditModule",
    "BackendProfilingModule",
    "IndexersAuditModule",
    "CacheAuditModule",
    "SessionStorageAuditModule",
    "FrontendAuditModule",
    "LogAnalysisModule",
    "MagentoHotspotsModule",
    "ProductionReadinessModule",
    "SecurityAuditModule",
    "CapacityLoadTestModule",
]
