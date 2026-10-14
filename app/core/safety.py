"""Conservative execution policy and sustained-load guard (no Qt or OS mutations)."""
import os
from dataclasses import dataclass

LOW_RAM_BYTES = 3 * 1024 ** 3


def encoding_threads(safe_mode=True, logical_cores=None):
    cores = logical_cores or os.cpu_count() or 2
    return max(2, cores - 2) if safe_mode else 0


def reserved_affinity(allowed):
    """Leave two allowed logical CPUs free, or one on a three-core system."""
    if len(allowed) > 2:
        return allowed[:-min(2, len(allowed) - 2)]
    return None  # Cannot reserve cores while honoring the two-thread minimum.


@dataclass
class SustainedLoadGuard:
    since: float = None
    notified: bool = False

    def observe(self, ram_percent, cpu_percent, now, enabled=True, active=True):
        overloaded = ram_percent > 90 or cpu_percent > 95
        if not enabled or not active or not overloaded:
            self.since = None
            self.notified = False
            return None
        if self.since is None:
            self.since = now
        if now - self.since >= 10 and not self.notified:
            self.notified = True
            return (f'Safety guard paused the queue: RAM {ram_percent:.1f}%, '
                    f'CPU {cpu_percent:.1f}% stayed over the limit for 10 seconds. '
                    'Free resources, then explicitly Resume.')
        return None
