"""Default monitoring-widget registry."""

from imagined_speech.ui.monitoring_workspace import (
    MonitoringPanelRegistry,
    MonitoringViewDescriptor,
)
from imagined_speech.ui.widgets.acquisition_health import AcquisitionHealthWidget
from imagined_speech.ui.widgets.channel_reception import ChannelReceptionWidget
from imagined_speech.ui.widgets.eeg_trace import EEGTraceWidget
from imagined_speech.ui.widgets.operator_audit import OperatorAuditWidget
from imagined_speech.ui.widgets.recent_markers import RecentMarkersWidget


def create_monitoring_registry() -> MonitoringPanelRegistry:
    registry = MonitoringPanelRegistry()
    for descriptor in (
        MonitoringViewDescriptor("live_eeg", "Live EEG", EEGTraceWidget),
        MonitoringViewDescriptor(
            "channel_reception", "Channel reception", ChannelReceptionWidget
        ),
        MonitoringViewDescriptor(
            "recent_markers", "Recent protocol markers", RecentMarkersWidget
        ),
        MonitoringViewDescriptor(
            "operator_audit", "Operator command audit", OperatorAuditWidget
        ),
        MonitoringViewDescriptor(
            "acquisition_health",
            "Acquisition and storage health",
            AcquisitionHealthWidget,
        ),
    ):
        registry.register(descriptor)
    return registry
