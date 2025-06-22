import sys
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QLabel,
    QLineEdit, QPushButton, QComboBox, QMessageBox, QProgressBar, QDialog,
    QMenu, QTextEdit, QTableWidget, QTableWidgetItem
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont


def parse_version(version_str):
    """
    Parse version string like '2.03.30(2)' to a tuple of integers (2, 3, 30)
    """
    ver = version_str.split('(')[0]  # remove parentheses and after
    parts = ver.strip().split('.')
    parsed = []
    for p in parts:
        try:
            parsed.append(int(p))
        except:
            # In case of weird format, extract digits only
            digits = ''.join(filter(str.isdigit, p))
            parsed.append(int(digits) if digits else 0)
    return tuple(parsed)


def check_lvm_version():
    """
    Run 'lvm version' command and parse the LVM version string.
    Returns a tuple like (2, 3, 30) or None on failure.
    """
    result = subprocess.run(["lvm", "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("LVM version:"):
            version_part = line.split(':', 1)[1].strip().split()[0]
            return parse_version(version_part)
    return None

class DetailsTableDialog(QDialog):
    def __init__(self, title, raw_csv, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 400)

        layout = QVBoxLayout(self)

        lines = raw_csv.strip().splitlines()
        headers = ["LV", "Path", "LSize (GB)", "Attr", "Origin", "Data %", "Meta %", "CTime"]

        self.table = QTableWidget(len(lines), len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        def fix_parts(parts):
            # LSize merging (columns 2 and 3)
            if len(parts) >= 4:
                parts[2] = parts[2] + ',' + parts[3]
                del parts[3]

            # Missing origin (4th column)
            if len(parts) == 7:
                parts.insert(4, '')

            # Merging the rest after Meta % (i.e. from column 7 to the end) into one CTime column
            if len(parts) > 8:
                parts[7] = ' '.join(parts[7:])
                del parts[8:]

            return parts

        for row_i, line in enumerate(lines):
            parts = line.split(",")
            parts = fix_parts(parts)  # naprawiamy format
            for col_i, val in enumerate(parts):
                item = QTableWidgetItem(val.strip())
                item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row_i, col_i, item)

        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.copy_btn = QPushButton("Copy to clipboard")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        self.close_btn.clicked.connect(self.close)

    def copy_to_clipboard(self):
        rows = []
        headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
        rows.append("\t".join(headers))
        for r in range(self.table.rowCount()):
            row_data = []
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                row_data.append(item.text() if item else "")
            rows.append("\t".join(row_data))
        text = "\n".join(rows)
        QApplication.clipboard().setText(text)

class LvmManager:
    """
    Encapsulates LVM command interactions.
    """

    def list_logical_volumes(self):
        """
        List all logical volumes with their volume group and whether they're snapshots.
        Returns list of tuples: (vg_name, lv_name, is_snapshot)
        """
        result = subprocess.run(
            ["lvs", "--noheadings", "-o", "lv_name,vg_name,origin"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Error running lvs: {result.stderr.strip()}")

        lvs = []
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split()
            lv_name = parts[0]
            vg_name = parts[1]
            origin = parts[2] if len(parts) > 2 else ""
            is_snap = bool(origin)  # presence of origin means snapshot
            lvs.append((vg_name, lv_name, is_snap))
        return lvs

    def get_snapshot_info(self, vg_name, lv_name, is_snap):
        """
        Get snapshot size and used data percentage in MB.
        Returns (used_mb, size_mb) or (None, None) if not a snapshot or error.
        """
        if not is_snap:
            return None, None
        result = subprocess.run(
            ["lvs", "--noheadings", "-o", "lv_name,lv_size,data_percent",
             "--units", "m", "--nosuffix", f"/dev/{vg_name}/{lv_name}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            return None, None
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) == 3 and parts[0] == lv_name:
                try:
                    size_mb = float(parts[1].replace(',', '.'))
                    percent = float(parts[2].replace(',', '.').replace('%', ''))
                    used_mb = size_mb * percent / 100.0
                    return used_mb, size_mb
                except Exception:
                    return None, None
        return None, None

    def get_vg_free_space(self, vg_name):
        """
        Get free and total size in MB of the volume group.
        Returns (free_mb, size_mb) or (None, None) on error.
        """
        result = subprocess.run(
            ["vgs", "--noheadings", "-o", "vg_free,vg_size", "--units", "m", "--nosuffix", vg_name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            return None, None
        line = result.stdout.strip()
        if not line:
            return None, None
        parts = line.split()
        if len(parts) != 2:
            return None, None
        try:
            free_mb = float(parts[0].replace(',', '.'))
            size_mb = float(parts[1].replace(',', '.'))
            return free_mb, size_mb
        except Exception:
            return None, None

    def create_snapshot(self, vg_name, lv_name, snap_name, size):
        """
        Create a snapshot named snap_name of given logical volume lv_name in vg_name,
        with specified size (e.g., '1G').
        Returns (success: bool, message: str).
        """
        path = f"/dev/{vg_name}/{lv_name}"
        result = subprocess.run(
            ["lvcreate", "-L", size, "-s", "-n", snap_name, path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, result.stdout.strip()

    def remove_snapshot(self, vg_name, snap_name):
        """
        Remove snapshot named snap_name from volume group vg_name.
        Returns (success: bool, message: str).
        """
        path = f"/dev/{vg_name}/{snap_name}"
        result = subprocess.run(
            ["lvremove", "-f", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, result.stdout.strip()

    def mount_snapshot(self, vg_name, snap_name, mount_point):
        """
        Mount the snapshot volume at mount_point.
        Returns (success: bool, message: str).
        """
        device_path = f"/dev/{vg_name}/{snap_name}"
        # Create mount point if doesn't exist
        subprocess.run(["mkdir", "-p", mount_point])
        # Mount command
        result = subprocess.run(
            ["mount", device_path, mount_point],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            print(f"[mount_snapshot ERROR] stderr: {result.stderr.strip()}")
            print(f"[mount_snapshot ERROR] stdout: {result.stdout.strip()}")
            return False, result.stderr.strip()
        print(f"[mount_snapshot SUCCESS] stdout: {result.stdout.strip()}")
        return True, result.stdout.strip()

    def get_detailed_lv_info(self, vg_name, lv_name):
        """
        Retrieves volume information in CSV format for easier parsing.
        """
        result = subprocess.run(
            ["lvs", "-a", f"/dev/{vg_name}/{lv_name}",
            "-o", "lv_name,lv_path,lv_size,attr,origin,data_percent,metadata_percent,lv_time",
            "--units", "g", "--separator", ",", "--nosuffix", "--noheadings"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()


class CommandThread(QThread):
    """
    QThread to run blocking subprocess commands in background.
    """
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            success, msg = self.func(*self.args, **self.kwargs)
            if not success:
                print(f"[CommandThread ERROR] {msg}")
        except Exception as e:
            print(f"[CommandThread EXCEPTION] {e}")
            success, msg = False, str(e)
        self.finished_signal.emit(success, msg)


class LoadingDialog(QDialog):
    """
    Simple modal dialog showing "Loading..." to block UI during blocking operations.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint)
        self.setWindowTitle("Please wait")
        self.label = QLabel("Loading...", self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout()
        layout.addWidget(self.label)
        self.setLayout(layout)
        self.resize(150, 80)


class DetailsDialog(QDialog):
    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 400)

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Monospace"))
        self.text_edit.setText(text)
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        self.copy_btn = QPushButton("Kopiuj do schowka")
        self.close_btn = QPushButton("Zamknij")
        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        self.close_btn.clicked.connect(self.close)

    def copy_to_clipboard(self):
        # Copy the table to the clipboard as text (tab-separated)
        rows = []
        headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
        rows.append("\t".join(headers))
        for r in range(self.table.rowCount()):
            row_data = []
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                row_data.append(item.text() if item else "")
            rows.append("\t".join(row_data))
        text = "\n".join(rows)
        QApplication.clipboard().setText(text)

class MainWindow(QWidget):
    """
    Main GUI window class for LVM Snapshot Manager.
    """

    def __init__(self):
        super().__init__()
        self.lvm = LvmManager()
        self.setWindowTitle("LVM Snapshot Manager")

        # Check installed LVM version and warn if newer than tested
        tested_version = (2, 3, 30)
        current_version = check_lvm_version()
        if current_version is not None:
            if current_version > tested_version:
                QMessageBox.warning(
                    self,
                    "Warning",
                    f"Detected LVM version {'.'.join(map(str, current_version))} "
                    f"is newer than tested version 2.3.30.\n"
                    "Some features may not work as expected."
                )

        # Set up main layout and widgets
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        # Top bar layout for info button
        top_bar = QHBoxLayout()
        top_bar.addStretch()  # Push button to the right

        # Info icon button
        info_btn = QPushButton()
        info_btn.setText("🛈")
        info_btn.setToolTip("About this application")
        info_btn.setFixedSize(24, 24)
        info_btn.setIconSize(info_btn.size())
        info_btn.setFlat(True)  # remove frame

        info_btn.clicked.connect(self.show_about_dialog)
        top_bar.addWidget(info_btn)
        self.layout.addLayout(top_bar)

        # List widget for logical volumes
        self.lv_list = QListWidget()
        self.lv_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lv_list.customContextMenuRequested.connect(self.show_context_menu)
        self.lv_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.layout.addWidget(QLabel("Logical Volumes:"))
        self.layout.addWidget(self.lv_list)

        # Input for snapshot name
        self.snap_name_edit = QLineEdit()
        self.snap_name_edit.setPlaceholderText("Snapshot name")
        self.layout.addWidget(self.snap_name_edit)

        # Dropdown for snapshot size selection
        self.size_combo = QComboBox()
        self.size_combo.addItems(["100M", "500M", "1G", "5G", "10G"])
        self.layout.addWidget(QLabel("Snapshot Size:"))
        self.layout.addWidget(self.size_combo)

        # Buttons for create, delete and mount snapshot
        btn_layout = QHBoxLayout()
        self.create_btn = QPushButton("Create Snapshot")
        self.create_btn.setToolTip("Create a snapshot of the selected logical volume")
        self.delete_btn = QPushButton("Delete Snapshot")
        self.delete_btn.setToolTip("Delete selected snapshot volume")
        self.mount_btn = QPushButton("Mount Snapshot")
        self.mount_btn.setToolTip("Mount the selected snapshot to a mount point")
        btn_layout.addWidget(self.create_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.mount_btn)
        self.layout.addLayout(btn_layout)

        # Label and progress bar for usage display
        self.usage_label = QLabel("Snapshot Usage:")
        self.usage_bar = QProgressBar()
        self.layout.addWidget(self.usage_label)
        self.layout.addWidget(self.usage_bar)

        # Status label for messages
        self.status_label = QLabel("")
        self.layout.addWidget(self.status_label)

        # Connect signals to slots
        self.create_btn.clicked.connect(self.create_snapshot)
        self.delete_btn.clicked.connect(self.delete_snapshot)
        self.mount_btn.clicked.connect(self.mount_snapshot)
        self.lv_list.itemSelectionChanged.connect(self.update_usage)
        self.lv_list.itemSelectionChanged.connect(self.update_buttons_state)

        # Load logical volumes into list
        self.refresh_lv_list()
        self.update_buttons_state()  # Set correct state of buttons at startup

    def refresh_lv_list(self):
        """
        Refresh the list of logical volumes shown in the GUI.
        """
        self.lv_list.clear()
        try:
            self.lvs = self.lvm.list_logical_volumes()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to list logical volumes:\n{e}")
            return
        for vg, lv, is_snap in self.lvs:
            item_text = f"{vg}/{lv}"
            if is_snap:
                item_text += " [snapshot]"
            self.lv_list.addItem(item_text)
        self.lv_list.update()

    def create_snapshot(self):
        """
        Handler for creating snapshot from selected logical volume.
        """
        selected = self.lv_list.currentItem()
        snap_name = self.snap_name_edit.text().strip()
        size = self.size_combo.currentText()

        if not selected:
            QMessageBox.warning(self, "Warning", "Please select a logical volume first.")
            return
        if not snap_name:
            QMessageBox.warning(self, "Warning", "Please enter a snapshot name.")
            return

        vg, lv = selected.text().split("/", 1)
        vg = vg.strip()
        lv = lv.split()[0]  # remove "[snapshot]" suffix if any

        # Run create_snapshot in background thread with loading dialog
        self.run_with_loading(self.lvm.create_snapshot, vg, lv, snap_name, size, callback=self.on_snapshot_created)

    def on_snapshot_created(self, success, msg):
        if success:
            self.status_label.setText("Snapshot created successfully.")
            self.refresh_lv_list()
            self.update_buttons_state()
        else:
            QMessageBox.critical(self, "Error", f"Failed to create snapshot:\n{msg}")

    def delete_snapshot(self):
        """
        Handler for deleting selected snapshot.
        """
        selected = self.lv_list.currentItem()
        if not selected:
            QMessageBox.warning(self, "Warning", "Please select a snapshot to delete.")
            return

        vg, lv = selected.text().split("/", 1)
        vg = vg.strip()
        lv = lv.split()[0]

        confirm = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete snapshot '{lv}' from volume group '{vg}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.run_with_loading(self.lvm.remove_snapshot, vg, lv, callback=self.on_snapshot_deleted)

    def on_snapshot_deleted(self, success, msg):
        if success:
            self.status_label.setText("Snapshot deleted successfully.")
            self.refresh_lv_list()
            self.update_buttons_state()
        else:
            QMessageBox.critical(self, "Error", f"Failed to delete snapshot:\n{msg}")

    def mount_snapshot(self):
        """
        Handler for mounting selected snapshot.
        Asks user for mount point path (simple input dialog) and mounts snapshot.
        """
        selected = self.lv_list.currentItem()
        if not selected:
            QMessageBox.warning(self, "Warning", "Please select a snapshot to mount.")
            return
        index = self.lv_list.currentRow()
        vg, lv, is_snap = self.lvs[index]

        if not is_snap:
            QMessageBox.warning(self, "Warning", "Selected volume is not a snapshot.")
            return

        # Ask for mount point path
        mount_point, ok = QInputDialog.getText(self, "Mount Snapshot", "Enter mount point path (must exist or will be created):")
        if not ok or not mount_point.strip():
            return
        mount_point = mount_point.strip()

        # Run mount_snapshot in background thread with loading dialog
        self.run_with_loading(self.lvm.mount_snapshot, vg, lv, mount_point, callback=self.on_snapshot_mounted)

    def on_snapshot_mounted(self, success, msg):
        if success:
            self.status_label.setText("Snapshot mounted successfully.")
            QMessageBox.information(self, "Mount Snapshot", "Snapshot mounted successfully.")
        else:
            QMessageBox.critical(self, "Error", f"Failed to mount snapshot:\n{msg}")

    def update_usage(self):
        """
        Update the usage progress bar and label based on selected LV or snapshot.
        """
        selected = self.lv_list.currentItem()
        if not selected:
            self.usage_bar.setValue(0)
            self.usage_label.setText("Snapshot Usage:")
            return

        index = self.lv_list.currentRow()
        vg, lv, is_snap = self.lvs[index]

        if is_snap:
            # Show snapshot usage percent and MB info
            used_mb, size_mb = self.lvm.get_snapshot_info(vg, lv, is_snap)
            if used_mb is None or size_mb is None:
                self.usage_bar.setValue(0)
                self.usage_label.setText("Snapshot Usage: Unknown or not a snapshot")
            else:
                percent = (used_mb / size_mb) * 100 if size_mb else 0
                self.usage_bar.setValue(int(percent))
                self.usage_label.setText(f"Snapshot Usage: {percent:.2f}% ({used_mb:.1f} MB / {size_mb:.1f} MB)")
        else:
            # Show volume group free/used space info if selected LV is not a snapshot
            free_mb, size_mb = self.lvm.get_vg_free_space(vg)
            if free_mb is None or size_mb is None:
                self.usage_bar.setValue(0)
                self.usage_label.setText("VG Free Space: Unknown")
            else:
                used_mb = size_mb - free_mb
                percent = (used_mb / size_mb) * 100 if size_mb else 0
                self.usage_bar.setValue(int(percent))
                self.usage_label.setText(
                    f"VG Usage: {percent:.2f}% ({used_mb:.1f} MB used / {free_mb:.1f} MB free / {size_mb:.1f} MB total)"
                )

    def update_buttons_state(self):
        """
        Enable or disable Delete and Mount Snapshot buttons depending on whether
        selected item is a snapshot.
        """
        selected = self.lv_list.currentItem()
        if not selected:
            self.delete_btn.setEnabled(False)
            self.mount_btn.setEnabled(False)
            return
        index = self.lv_list.currentRow()
        _, _, is_snap = self.lvs[index]
        self.delete_btn.setEnabled(is_snap)
        self.mount_btn.setEnabled(is_snap)

    def run_with_loading(self, func, *args, callback=None):
        """
        Runs a blocking function func(*args) in a separate thread with
        modal loading dialog. Calls callback(success, msg) when done.
        """
        self.loading_dialog = LoadingDialog(self)
        self.thread = CommandThread(func, *args)
        self.thread.finished_signal.connect(self.loading_finished)
        if callback:
            self._callback = callback
        else:
            self._callback = None
        self.thread.start()
        self.loading_dialog.show()

    def loading_finished(self, success, msg):
        self.loading_dialog.close()
        if self._callback:
            self._callback(success, msg)

    def show_about_dialog(self):
        """
        Show About dialog with version and author info.
        """
        QMessageBox.information(
            self,
            "About LVM Manager",
            "LVM Snapshot Manager\n"
            "Version: 0.0.4\n"
            "Author: Shieldziak\n"
            "License: MIT\n"
            "GitHub: https://github.com/Shieldowskyy/lvm-manager\n\n"
            "This tool allows you to create, delete, and mount LVM snapshots via GUI.\n"
            "Tested on LVM 2.03.30."
        )

    def show_context_menu(self, position):
        item = self.lv_list.itemAt(position)
        if item is None:
            return
        index = self.lv_list.row(item)
        vg, lv, _ = self.lvs[index]

        menu = QMenu()
        details_action = menu.addAction("Show LV details")
        action = menu.exec(self.lv_list.mapToGlobal(position))

        if action == details_action:
            info = self.lvm.get_detailed_lv_info(vg, lv)
            if info is None:
                QMessageBox.critical(self, "Error", "The volume details could not be retrieved.")
                return

            # Automatically copy to clipboard (optional)
            QApplication.clipboard().setText(info)

            dlg = DetailsTableDialog(f"Details for {vg}/{lv}", info, self)
            dlg.exec()

# We need to import QInputDialog used in mount_snapshot
from PyQt6.QtWidgets import QInputDialog


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(500, 550)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
