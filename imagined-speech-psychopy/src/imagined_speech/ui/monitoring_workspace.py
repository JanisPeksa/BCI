"""Configurable splitter-based monitoring workspace for the experimenter UI."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)


PaneFactory = Callable[[], QWidget]


@dataclass(frozen=True)
class MonitoringViewDescriptor:
    view_id: str
    title: str
    factory: PaneFactory
    refresh_policy: str = "visible"


class MonitoringPanelRegistry:
    """Registry of monitoring projections available to every pane selector."""

    def __init__(self) -> None:
        self._views: dict[str, MonitoringViewDescriptor] = {}

    def register(self, descriptor: MonitoringViewDescriptor) -> None:
        if descriptor.view_id in self._views:
            raise ValueError(f"duplicate monitoring view ID: {descriptor.view_id}")
        self._views[descriptor.view_id] = descriptor

    def descriptor(self, view_id: str) -> MonitoringViewDescriptor:
        try:
            return self._views[view_id]
        except KeyError as exc:
            raise ValueError(f"unknown monitoring view ID: {view_id}") from exc

    def contains(self, view_id: str) -> bool:
        return view_id in self._views

    @property
    def descriptors(self) -> tuple[MonitoringViewDescriptor, ...]:
        return tuple(self._views.values())


@dataclass(frozen=True)
class WorkspaceLayoutDescriptor:
    layout_id: str
    name: str
    rectangles: tuple[tuple[float, float, float, float], ...]
    topology: str

    @property
    def pane_count(self) -> int:
        return len(self.rectangles)


LAYOUTS = (
    WorkspaceLayoutDescriptor("single", "Single pane", ((0, 0, 1, 1),), "single"),
    WorkspaceLayoutDescriptor(
        "grid_4",
        "Four-pane grid",
        ((0, 0, .5, .5), (0, .5, .5, .5), (.5, 0, .5, .5), (.5, .5, .5, .5)),
        "grid_4",
    ),
    WorkspaceLayoutDescriptor(
        "columns_2", "Two columns", ((0, 0, .5, 1), (.5, 0, .5, 1)), "columns_2"
    ),
    WorkspaceLayoutDescriptor(
        "rows_2", "Two rows", ((0, 0, 1, .5), (0, .5, 1, .5)), "rows_2"
    ),
    WorkspaceLayoutDescriptor(
        "left_right_rows",
        "Left pane with two rows",
        ((0, 0, .5, 1), (.5, 0, .5, .5), (.5, .5, .5, .5)),
        "left_right_rows",
    ),
    WorkspaceLayoutDescriptor(
        "left_rows_right",
        "Two rows with right pane",
        ((0, 0, .5, .5), (0, .5, .5, .5), (.5, 0, .5, 1)),
        "left_rows_right",
    ),
    WorkspaceLayoutDescriptor(
        "top_bottom_columns",
        "Top pane with two columns",
        ((0, 0, 1, .5), (0, .5, .5, .5), (.5, .5, .5, .5)),
        "top_bottom_columns",
    ),
    WorkspaceLayoutDescriptor(
        "top_columns_bottom",
        "Two columns with bottom pane",
        ((0, 0, .5, .5), (.5, 0, .5, .5), (0, .5, 1, .5)),
        "top_columns_bottom",
    ),
)
LAYOUT_BY_ID = {layout.layout_id: layout for layout in LAYOUTS}
DEFAULT_LAYOUT_ID = "grid_4"
PANE_SLOT_IDS = ("pane_1", "pane_2", "pane_3", "pane_4")
DEFAULT_ASSIGNMENTS = {
    "pane_1": "live_eeg",
    "pane_2": "channel_reception",
    "pane_3": "recent_markers",
    "pane_4": "acquisition_health",
}


def create_layout_icon(
    rectangles: Sequence[tuple[float, float, float, float]],
) -> QIcon:
    pixmap = QPixmap(48, 38)
    pixmap.fill(QColor("#ffffff"))
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor("#000000"), 1))
    margin, gap = 4.0, 2.0
    width = pixmap.width() - 2 * margin
    height = pixmap.height() - 2 * margin
    for x, y, rect_width, rect_height in rectangles:
        painter.drawRect(
            QRectF(
                margin + x * width + gap,
                margin + y * height + gap,
                rect_width * width - 2 * gap,
                rect_height * height - 2 * gap,
            )
        )
    painter.end()
    return QIcon(pixmap)


class LayoutPicker(QWidget):
    layoutSelected = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QToolButton] = {}
        layout = QGridLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)
        for index, descriptor in enumerate(LAYOUTS):
            button = QToolButton(self)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setIcon(create_layout_icon(descriptor.rectangles))
            button.setIconSize(QSize(48, 38))
            button.setFixedSize(54, 44)
            button.setToolTip(descriptor.name)
            button.clicked.connect(
                lambda _checked=False, selected=descriptor.layout_id: self.layoutSelected.emit(selected)
            )
            layout.addWidget(button, index // 4, index % 4)
            self._buttons[descriptor.layout_id] = button

    def set_current(self, layout_id: str) -> None:
        button = self._buttons.get(layout_id)
        if button is not None:
            button.setChecked(True)


class MonitoringPane(QFrame):
    assignmentChanged = pyqtSignal(str, str)

    def __init__(
        self,
        slot_id: str,
        view_id: str,
        registry: MonitoringPanelRegistry,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.slot_id = slot_id
        self.registry = registry
        self.view_id = ""
        self.content_widget: QWidget | None = None
        self.setObjectName("monitoringPane")
        self.setMinimumSize(150, 110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame(self)
        header.setObjectName("monitoringPaneHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(5, 2, 5, 2)
        header_layout.setSpacing(6)
        self.selector = QComboBox(header)
        self.selector.setObjectName("monitoringPaneSelector")
        for descriptor in registry.descriptors:
            self.selector.addItem(descriptor.title, descriptor.view_id)
        header_layout.addWidget(self.selector, 1)
        root.addWidget(header)
        self.content_host = QWidget(self)
        self.content_layout = QVBoxLayout(self.content_host)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.content_host, 1)
        self.selector.currentIndexChanged.connect(self._selection_changed)
        self.set_assignment(view_id, emit_signal=False)

    def set_assignment(self, view_id: str, *, emit_signal: bool = True) -> None:
        descriptor = self.registry.descriptor(view_id)
        if self.view_id == view_id and self.content_widget is not None:
            return
        if self.content_widget is not None:
            self.content_layout.removeWidget(self.content_widget)
            self.content_widget.hide()
            self.content_widget.setParent(None)
            self.content_widget.deleteLater()
        self.view_id = view_id
        self.content_widget = descriptor.factory()
        self.content_widget.setObjectName(f"monitoringView.{view_id}.{self.slot_id}")
        self.content_layout.addWidget(self.content_widget)
        index = self.selector.findData(view_id)
        if index >= 0 and index != self.selector.currentIndex():
            self.selector.blockSignals(True)
            self.selector.setCurrentIndex(index)
            self.selector.blockSignals(False)
        if emit_signal:
            self.assignmentChanged.emit(self.slot_id, view_id)

    def _selection_changed(self, index: int) -> None:
        view_id = self.selector.itemData(index)
        if view_id:
            self.set_assignment(str(view_id))


class MonitoringWorkspace(QWidget):
    """Owns the active splitter tree and persistent pane preferences."""

    stateChanged = pyqtSignal()

    def __init__(
        self,
        registry: MonitoringPanelRegistry,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.current_layout_id = DEFAULT_LAYOUT_ID
        self.assignments = dict(DEFAULT_ASSIGNMENTS)
        self._splitter_sizes: dict[str, dict[str, list[int]]] = {}
        self.root_widget: QWidget | None = None
        self.active_panes: dict[str, MonitoringPane] = {}
        self._restoring = False
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)
        self.setStyleSheet(
            "QFrame#monitoringPane { background: white; border: 1px solid #526576; }"
            "QFrame#monitoringPaneHeader { background: #d7dce1; border-bottom: 1px solid #8b949d; }"
            "QComboBox#monitoringPaneSelector { background: white; padding: 2px 5px; }"
            "QSplitter::handle { background: #526576; }"
            "QSplitter::handle:hover { background: #357fc2; }"
        )
        self.set_layout(DEFAULT_LAYOUT_ID)

    @property
    def pane_count(self) -> int:
        return len(self.active_panes)

    def set_layout(self, layout_id: str) -> None:
        if layout_id not in LAYOUT_BY_ID:
            layout_id = DEFAULT_LAYOUT_ID
        if self.root_widget is not None:
            if not self._restoring:
                self._capture_active_splitter_sizes()
            self._root_layout.removeWidget(self.root_widget)
            self.root_widget.hide()
            self.root_widget.setParent(None)
            self.root_widget.deleteLater()
        self.current_layout_id = layout_id
        self.active_panes = {}
        pane_index = 0

        def pane() -> MonitoringPane:
            nonlocal pane_index
            slot_id = PANE_SLOT_IDS[pane_index]
            pane_index += 1
            widget = MonitoringPane(
                slot_id,
                self.assignments.get(slot_id, DEFAULT_ASSIGNMENTS[slot_id]),
                self.registry,
            )
            widget.assignmentChanged.connect(self._assignment_changed)
            self.active_panes[slot_id] = widget
            return widget

        def splitter(orientation: Qt.Orientation, *children: QWidget) -> QSplitter:
            widget = QSplitter(orientation)
            widget.setChildrenCollapsible(False)
            widget.setHandleWidth(3)
            widget.setOpaqueResize(True)
            for child in children:
                widget.addWidget(child)
            for index in range(len(children)):
                widget.setStretchFactor(index, 1)
            widget.splitterMoved.connect(lambda _pos, _index: self.stateChanged.emit())
            return widget

        builders: dict[str, Callable[[], QWidget]] = {
            "single": lambda: pane(),
            "grid_4": lambda: splitter(
                Qt.Orientation.Horizontal,
                splitter(Qt.Orientation.Vertical, pane(), pane()),
                splitter(Qt.Orientation.Vertical, pane(), pane()),
            ),
            "columns_2": lambda: splitter(Qt.Orientation.Horizontal, pane(), pane()),
            "rows_2": lambda: splitter(Qt.Orientation.Vertical, pane(), pane()),
            "left_right_rows": lambda: splitter(
                Qt.Orientation.Horizontal,
                pane(),
                splitter(Qt.Orientation.Vertical, pane(), pane()),
            ),
            "left_rows_right": lambda: splitter(
                Qt.Orientation.Horizontal,
                splitter(Qt.Orientation.Vertical, pane(), pane()),
                pane(),
            ),
            "top_bottom_columns": lambda: splitter(
                Qt.Orientation.Vertical,
                pane(),
                splitter(Qt.Orientation.Horizontal, pane(), pane()),
            ),
            "top_columns_bottom": lambda: splitter(
                Qt.Orientation.Vertical,
                splitter(Qt.Orientation.Horizontal, pane(), pane()),
                pane(),
            ),
        }
        self.root_widget = builders[LAYOUT_BY_ID[layout_id].topology]()
        self._name_splitters(self.root_widget, "root")
        self._root_layout.addWidget(self.root_widget)
        QTimer.singleShot(0, self.apply_saved_splitter_sizes)
        if not self._restoring:
            self.stateChanged.emit()

    def set_assignment(self, slot_id: str, view_id: str) -> None:
        if slot_id not in PANE_SLOT_IDS:
            raise ValueError(f"unknown pane slot ID: {slot_id}")
        self.registry.descriptor(view_id)
        self.assignments[slot_id] = view_id
        pane = self.active_panes.get(slot_id)
        if pane is not None:
            pane.set_assignment(view_id, emit_signal=False)
        if not self._restoring:
            self.stateChanged.emit()

    def _assignment_changed(self, slot_id: str, view_id: str) -> None:
        self.assignments[slot_id] = view_id
        if not self._restoring:
            self.stateChanged.emit()

    def widgets_for(self, view_id: str) -> tuple[QWidget, ...]:
        return tuple(
            pane.content_widget
            for pane in self.active_panes.values()
            if pane.view_id == view_id
            and pane.content_widget is not None
            and not pane.isHidden()
        )

    def clear_views(self) -> None:
        for pane in self.active_panes.values():
            widget = pane.content_widget
            clear = getattr(widget, "clear", None)
            if callable(clear):
                clear()

    def reset(self) -> None:
        self.assignments = dict(DEFAULT_ASSIGNMENTS)
        self._splitter_sizes.clear()
        self.set_layout(DEFAULT_LAYOUT_ID)

    def preferences(self) -> tuple[str, dict[str, str], dict[str, dict[str, list[int]]]]:
        self._capture_active_splitter_sizes()
        return (
            self.current_layout_id,
            dict(self.assignments),
            {
                layout_id: {path: list(sizes) for path, sizes in paths.items()}
                for layout_id, paths in self._splitter_sizes.items()
            },
        )

    def restore_preferences(
        self,
        layout_id: object,
        assignments: object,
        splitter_sizes: object,
    ) -> bool:
        valid = True
        selected_layout = str(layout_id)
        if selected_layout not in LAYOUT_BY_ID:
            selected_layout = DEFAULT_LAYOUT_ID
            valid = False

        restored_assignments = dict(DEFAULT_ASSIGNMENTS)
        if isinstance(assignments, Mapping):
            for slot_id, view_id in assignments.items():
                if slot_id in PANE_SLOT_IDS and self.registry.contains(str(view_id)):
                    restored_assignments[str(slot_id)] = str(view_id)
                else:
                    valid = False
        else:
            valid = False

        restored_sizes: dict[str, dict[str, list[int]]] = {}
        if isinstance(splitter_sizes, Mapping):
            for candidate_layout, paths in splitter_sizes.items():
                if candidate_layout not in LAYOUT_BY_ID or not isinstance(paths, Mapping):
                    valid = False
                    continue
                clean_paths: dict[str, list[int]] = {}
                for path, sizes in paths.items():
                    if (
                        isinstance(path, str)
                        and isinstance(sizes, list)
                        and sizes
                        and all(isinstance(size, int) and size > 0 for size in sizes)
                    ):
                        clean_paths[path] = list(sizes)
                    else:
                        valid = False
                restored_sizes[str(candidate_layout)] = clean_paths
        else:
            valid = False

        self._restoring = True
        self.assignments = restored_assignments
        self._splitter_sizes = restored_sizes
        self.set_layout(selected_layout)
        self._restoring = False
        return valid

    def apply_saved_splitter_sizes(self) -> None:
        saved = self._splitter_sizes.get(self.current_layout_id, {})
        for path, splitter in self._splitters().items():
            sizes = saved.get(path)
            if sizes is not None and len(sizes) == splitter.count():
                splitter.setSizes(sizes)

    def _capture_active_splitter_sizes(self) -> None:
        if self.root_widget is None:
            return
        self._splitter_sizes[self.current_layout_id] = {
            path: splitter.sizes() for path, splitter in self._splitters().items()
        }

    def _splitters(self) -> dict[str, QSplitter]:
        result: dict[str, QSplitter] = {}
        if self.root_widget is None:
            return result

        def visit(widget: QWidget, path: str) -> None:
            if not isinstance(widget, QSplitter):
                return
            result[path] = widget
            for index in range(widget.count()):
                child = widget.widget(index)
                if child is not None:
                    visit(child, f"{path}/{index}")

        visit(self.root_widget, "root")
        return result

    def _name_splitters(self, widget: QWidget, path: str) -> None:
        if not isinstance(widget, QSplitter):
            return
        widget.setObjectName(f"monitoringSplitter.{self.current_layout_id}.{path}")
        for index in range(widget.count()):
            child = widget.widget(index)
            if child is not None:
                self._name_splitters(child, f"{path}/{index}")


def create_layout_menu(
    button: QPushButton,
    workspace: MonitoringWorkspace,
    reset_callback: Callable[[], None],
) -> tuple[QMenu, LayoutPicker]:
    menu = QMenu(button)
    picker = LayoutPicker(menu)
    action = QWidgetAction(menu)
    action.setDefaultWidget(picker)
    menu.addAction(action)
    menu.addSeparator()
    reset = menu.addAction("Reset monitoring layout")
    reset.triggered.connect(reset_callback)
    picker.layoutSelected.connect(
        lambda layout_id: (menu.close(), workspace.set_layout(layout_id), picker.set_current(layout_id))
    )
    picker.set_current(workspace.current_layout_id)
    button.setMenu(menu)
    return menu, picker
