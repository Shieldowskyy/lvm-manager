import sys
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QLabel,
    QLineEdit, QPushButton, QComboBox, QMessageBox, QProgressBar
)
from PyQt6.QtCore import Qt


def parse_version(version_str):
    ver = version_str.split('(')[0]
    parts = ver.strip().split('.')
    parsed = []
    for p in parts:
        try:
            parsed.append(int(p))
        except:
            digits = ''.join(filter(str.isdigit, p))
            parsed.append(int(digits) if digits else 0)
    return tuple(parsed)


def check_lvm_version():
    result = subprocess.run(["lvm", "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("LVM version:"):
            version_part = line.split(':', 1)[1].strip().split()[0]
            return parse_version(version_part)
    return None


class LvmManager:
    def list_logical_volumes(self):
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
            is_snap = bool(origin)
            lvs.append((vg_name, lv_name, is_snap))
        return lvs

    def get_snapshot_info(self, vg_name, lv_name, is_snap):
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
        path = f"/dev/{vg_name}/{lv_name}"
        result = subprocess.run(
            ["lvcreate", "-L", size, "-s", "-n", snap_name, path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, result.stdout.strip()

    def remove_snapshot(self, vg_name, snap_name):
        path = f"/dev/{vg_name}/{snap_name}"
        result = subprocess.run(
            ["lvremove", "-f", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, result.stdout.strip()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.lvm = LvmManager()
        self.setWindowTitle("LVM Snapshot Manager")

        # Check LVM version and warn if newer than tested
        tested_version = (2, 3, 30)
        current_version = check_lvm_version()
        if current_version is not None:
            if current_version > tested_version:
                QMessageBox.warning(
                    self,
                    "Warning",
                    f"Detected LVM version {'.'.join(map(str, current_version))} is newer than tested version 2.3.30.\n"
                    "Some features may not work as expected."
                )

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.lv_list = QListWidget()
        self.lv_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.layout.addWidget(QLabel("Logical Volumes:"))
        self.layout.addWidget(self.lv_list)

        self.snap_name_edit = QLineEdit()
        self.snap_name_edit.setPlaceholderText("Snapshot name")
        self.layout.addWidget(self.snap_name_edit)

        self.size_combo = QComboBox()
        self.size_combo.addItems(["100M", "500M", "1G", "5G", "10G"])
        self.layout.addWidget(QLabel("Snapshot Size:"))
        self.layout.addWidget(self.size_combo)

        btn_layout = QHBoxLayout()
        self.create_btn = QPushButton("Create Snapshot")
        self.create_btn.setToolTip("Create a snapshot of the selected logical volume")
        self.delete_btn = QPushButton("Delete Snapshot")
        self.delete_btn.setToolTip("Delete selected snapshot volume")
        btn_layout.addWidget(self.create_btn)
        btn_layout.addWidget(self.delete_btn)
        self.layout.addLayout(btn_layout)

        self.usage_label = QLabel("Snapshot Usage:")
        self.usage_bar = QProgressBar()
        self.layout.addWidget(self.usage_label)
        self.layout.addWidget(self.usage_bar)

        self.status_label = QLabel("")
        self.layout.addWidget(self.status_label)

        self.create_btn.clicked.connect(self.create_snapshot)
        self.delete_btn.clicked.connect(self.delete_snapshot)
        self.lv_list.itemSelectionChanged.connect(self.update_usage)
        self.lv_list.itemSelectionChanged.connect(self.update_buttons_state)

        self.refresh_lv_list()
        self.update_buttons_state()

    def refresh_lv_list(self):
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
        lv = lv.split()[0]

        success, msg = self.lvm.create_snapshot(vg, lv, snap_name, size)
        if success:
            self.status_label.setText(f"Snapshot '{snap_name}' created successfully.")
            self.refresh_lv_list()
            self.update_buttons_state()
        else:
            QMessageBox.critical(self, "Error", f"Failed to create snapshot:\n{msg}")

    def delete_snapshot(self):
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

        success, msg = self.lvm.remove_snapshot(vg, lv)
        if success:
            self.status_label.setText(f"Snapshot '{lv}' deleted successfully.")
            self.refresh_lv_list()
            self.update_buttons_state()
        else:
            QMessageBox.critical(self, "Error", f"Failed to delete snapshot:\n{msg}")

    def update_usage(self):
        selected = self.lv_list.currentItem()
        if not selected:
            self.usage_bar.setValue(0)
            self.usage_label.setText("Snapshot Usage:")
            return
        index = self.lv_list.currentRow()
        vg, lv, is_snap = self.lvs[index]
        if is_snap:
            used_mb, size_mb = self.lvm.get_snapshot_info(vg, lv, is_snap)
            if used_mb is None or size_mb is None:
                self.usage_bar.setValue(0)
                self.usage_label.setText("Snapshot Usage: Unknown or not a snapshot")
            else:
                percent = (used_mb / size_mb) * 100 if size_mb else 0
                self.usage_bar.setValue(int(percent))
                self.usage_label.setText(f"Snapshot Usage: {percent:.2f}% ({used_mb:.1f} MB / {size_mb:.1f} MB)")
        else:
            free_mb, size_mb = self.lvm.get_vg_free_space(vg)
            if free_mb is None or size_mb is None:
                self.usage_bar.setValue(0)
                self.usage_label.setText("VG Free Space: Unknown")
            else:
                used_mb = size_mb - free_mb
                percent = (used_mb / size_mb) * 100 if size_mb else 0
                self.usage_bar.setValue(int(percent))
                self.usage_label.setText(f"VG Usage: {percent:.2f}% ({used_mb:.1f} MB used / {free_mb:.1f} MB free / {size_mb:.1f} MB total)")

    def update_buttons_state(self):
        selected = self.lv_list.currentItem()
        if not selected:
            self.delete_btn.setEnabled(False)
            return
        index = self.lv_list.currentRow()
        _, _, is_snap = self.lvs[index]
        self.delete_btn.setEnabled(is_snap)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(400, 500)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
