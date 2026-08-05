"""Modular read-only experimenter monitoring widgets."""

from imagined_speech.ui.widgets.acquisition_health import AcquisitionHealthWidget
from imagined_speech.ui.widgets.channel_reception import ChannelReceptionWidget
from imagined_speech.ui.widgets.eeg_trace import EEGTraceWidget
from imagined_speech.ui.widgets.operator_audit import OperatorAuditWidget
from imagined_speech.ui.widgets.recent_markers import RecentMarkersWidget
from imagined_speech.ui.widgets.registry import create_monitoring_registry

__all__ = [
    "AcquisitionHealthWidget",
    "ChannelReceptionWidget",
    "EEGTraceWidget",
    "OperatorAuditWidget",
    "RecentMarkersWidget",
    "create_monitoring_registry",
]
