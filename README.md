# LVM Snapshot Manager (PyQt6)

Simple graphical GUI for managing LVM snapshots: create, delete, view usage (in % and MB), and see free space in volume groups.

![LVM Snapshot Manager GUI](https://github.com/user-attachments/assets/aa7a769f-243d-4810-a894-5cbc77847b9e)

*Screenshot of the LVM Snapshot Manager GUI on Fedora 42 KDE Plasma (with aerothemeplasma theme)*
## Features

- List logical volumes (LVs) with snapshots marked  
- Create snapshots (select snapshot size)  
- Delete snapshots (with confirmation)  
- Show snapshot usage in percent and MB  
- Show free and used space in volume group (for LVs that are not snapshots)  
- "Delete Snapshot" button enabled only for snapshots  
- Tooltips and simple, intuitive interface  

## Requirements

- Python 3.8+  
- PyQt6  
- LVM command line tools available (`lvs`, `lvcreate`, `lvremove`, `vgs`) on Linux  
- Proper permissions to manage LVM (usually root or sudo)  

## Installation

```bash
sudo pip install PyQt6
````

## Usage

```bash
sudo python lvm_manager.py
```

1. Select a logical volume from the list.
2. If it’s not a snapshot, you can create a snapshot by entering a name and selecting size, then clicking "Create Snapshot".
3. If it’s a snapshot, you can delete it by clicking "Delete Snapshot".
4. Below the list, snapshot usage or VG free space is displayed.

## Notes

* The app calls system LVM tools and needs appropriate permissions (run as root or with sudo).
* The GUI is minimal but functional. Feel free to extend it.
* Not production ready - needs testing!!!
