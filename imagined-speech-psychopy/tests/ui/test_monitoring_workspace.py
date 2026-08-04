import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QApplication, QLabel, QSplitter

from imagined_speech.ui.monitoring_workspace import (
    DEFAULT_LAYOUT_ID,
    LAYOUTS,
    MonitoringPanelRegistry,
    MonitoringViewDescriptor,
    MonitoringWorkspace,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _registry() -> MonitoringPanelRegistry:
    registry = MonitoringPanelRegistry()
    for view_id in (
        "live_eeg",
        "channel_reception",
        "recent_markers",
        "operator_audit",
        "acquisition_health",
    ):
        registry.register(
            MonitoringViewDescriptor(
                view_id,
                view_id.replace("_", " ").title(),
                lambda selected=view_id: QLabel(selected),
            )
        )
    return registry


def test_all_eight_layouts_build_expected_splitter_topologies() -> None:
    app = _app()
    assert app is not None
    workspace = MonitoringWorkspace(_registry())
    expected = {
        "single": (1, None),
        "grid_4": (4, Qt.Orientation.Horizontal),
        "columns_2": (2, Qt.Orientation.Horizontal),
        "rows_2": (2, Qt.Orientation.Vertical),
        "left_right_rows": (3, Qt.Orientation.Horizontal),
        "left_rows_right": (3, Qt.Orientation.Horizontal),
        "top_bottom_columns": (3, Qt.Orientation.Vertical),
        "top_columns_bottom": (3, Qt.Orientation.Vertical),
    }
    assert len(LAYOUTS) == 8
    for layout_id, (pane_count, orientation) in expected.items():
        workspace.set_layout(layout_id)
        assert workspace.pane_count == pane_count
        if orientation is None:
            assert not isinstance(workspace.root_widget, QSplitter)
        else:
            assert isinstance(workspace.root_widget, QSplitter)
            assert workspace.root_widget.orientation() == orientation
            assert all(
                not splitter.childrenCollapsible()
                for splitter in workspace.findChildren(QSplitter)
            )


def test_assignments_survive_layout_changes_and_only_selected_pane_changes() -> None:
    app = _app()
    assert app is not None
    workspace = MonitoringWorkspace(_registry())
    other_before = workspace.active_panes["pane_2"].content_widget
    workspace.set_assignment("pane_1", "operator_audit")
    assert workspace.active_panes["pane_1"].view_id == "operator_audit"
    assert workspace.active_panes["pane_2"].content_widget is other_before
    workspace.set_assignment("pane_4", "recent_markers")
    workspace.set_layout("single")
    workspace.set_layout("grid_4")
    assert workspace.active_panes["pane_1"].view_id == "operator_audit"
    assert workspace.active_panes["pane_4"].view_id == "recent_markers"


def test_splitter_sizes_round_trip_reset_fallback_and_widget_disposal() -> None:
    app = _app()
    workspace = MonitoringWorkspace(_registry())
    workspace.resize(900, 600)
    workspace.show()
    app.processEvents()
    root = workspace.root_widget
    assert isinstance(root, QSplitter)
    root.setSizes([300, 600])
    old_widget = workspace.active_panes["pane_1"].content_widget
    destroyed = []
    assert old_widget is not None
    old_widget.destroyed.connect(lambda: destroyed.append(True))
    layout_id, assignments, sizes = workspace.preferences()

    restored = MonitoringWorkspace(_registry())
    restored.resize(900, 600)
    restored.show()
    assert restored.restore_preferences(layout_id, assignments, sizes)
    app.processEvents()
    restored.apply_saved_splitter_sizes()
    restored_root = restored.root_widget
    assert isinstance(restored_root, QSplitter)
    assert restored_root.sizes()[0] < restored_root.sizes()[1]

    workspace.set_layout("single")
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert destroyed
    restored.reset()
    assert restored.current_layout_id == DEFAULT_LAYOUT_ID
    assert restored.assignments["pane_1"] == "live_eeg"
    assert not restored.restore_preferences("unknown", {"bad": "view"}, {"bad": []})
    assert restored.current_layout_id == DEFAULT_LAYOUT_ID


def test_registry_accepts_future_qc_view_without_layout_changes() -> None:
    app = _app()
    assert app is not None
    registry = _registry()
    registry.register(
        MonitoringViewDescriptor("future_qc", "Future QC", lambda: QLabel("QC"))
    )
    workspace = MonitoringWorkspace(registry)
    workspace.set_assignment("pane_1", "future_qc")
    assert workspace.active_panes["pane_1"].view_id == "future_qc"
    assert workspace.active_panes["pane_1"].selector.findData("future_qc") >= 0
