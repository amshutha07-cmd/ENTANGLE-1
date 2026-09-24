import os
import json
import logging
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QVBoxLayout, QWidget, QFrame, QApplication, QComboBox,
    QListWidget, QListWidgetItem
)
from security_core import SecurityCore
import transfer
from relay_client import RelayError

logger = logging.getLogger(__name__)


# This script holds the massive replacement methods for ANSxVault class
class UI_Updates:
    
    @staticmethod
    def build_vault_ui(self) -> None:
        layout = QVBoxLayout(self._page_vault)
        layout.setContentsMargins(60, 50, 60, 50)

        header = QLabel("🔐 STAGE 1: SECURE VAULT")
        header.setStyleSheet("color: #ffffff; font-size: 32px; font-weight: bold; font-family: 'Courier New';")
        layout.addWidget(header)
        layout.addSpacing(20)
        
        info = QLabel("Shatter your payload strictly for self-storage. No receiver needed yet.")
        info.setStyleSheet("color: #aaa;")
        layout.addWidget(info)
        layout.addSpacing(40)

        action_panel = QFrame()
        action_panel.setStyleSheet("background-color: #0c0d14; border: 1px solid #1a1c23; border-radius: 12px;")
        ap_layout = QVBoxLayout(action_panel)
        ap_layout.setContentsMargins(40, 40, 40, 40)

        self._vault_title = QLabel("AWAITING PAYLOAD TO VAULT")
        self._vault_title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: bold;")
        self._vault_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ap_layout.addWidget(self._vault_title)

        self._vault_progress = QProgressBar()
        self._vault_progress.hide()
        ap_layout.addWidget(self._vault_progress)

        self._vault_btn = QPushButton("SELECT FILE & VAULT")
        self._vault_btn.setObjectName("PrimaryBtn")
        self._vault_btn.clicked.connect(self._initiate_vault)
        ap_layout.addWidget(self._vault_btn)

        layout.addWidget(action_panel)
        layout.addSpacing(30)

        # ── Vault Ledger Gallery ────────────────────────────────────────────────
        gallery_header = QLabel("YOUR VAULTED FILES")
        gallery_header.setStyleSheet("color: #7AA2F7; font-size: 14px; font-weight: bold; letter-spacing: 1px;")
        layout.addWidget(gallery_header)

        self._vault_list = QListWidget()
        self._vault_list.setStyleSheet(
            "background: #0a0b10; color: #fff; border: 1px solid #1a1c23; border-radius: 8px;"
            " padding: 10px; font-size: 13px; font-family: monospace;"
        )
        layout.addWidget(self._vault_list, 1)

        # Populate the gallery initially
        if hasattr(self, '_refresh_vault_gallery'):
            self._refresh_vault_gallery()
        else:
            # Inject the method if not already present
            def refresh_gallery():
                from security_core import VaultLedger
                self._vault_list.clear()
                ledger = VaultLedger.load()
                if not ledger:
                    self._vault_list.addItem("Vault is empty. Shatter a payload above to begin.")
                    return
                for item in ledger:
                    display = f"📁 {item['original_filename']}  |  Vaulted: {item['date_vaulted']}"
                    self._vault_list.addItem(display)
            
            self._refresh_vault_gallery = refresh_gallery
            self._refresh_vault_gallery()

    @staticmethod
    def initiate_vault(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Payload", "", "All Files (*)")
        if not file_path: return

        identity = SecurityCore.load_identity_for_user(self.current_operator)
        if not identity:
            QMessageBox.critical(self, "Error", "No identity loaded.")
            return

        self._vault_btn.setEnabled(False)
        self._vault_btn.setText("SHATTERING…")
        self._vault_title.setText(">> ENGAGING GALOIS FIELD MATH <<")
        self._vault_title.setStyleSheet("color: #ff3366; font-size: 20px; font-weight: bold;")
        self._vault_progress.show()
        self._vault_progress.setRange(0, 0)

        # Generating Ephemeral AES Key
        self._ephemeral_master_key = os.urandom(32).hex()

        # The vault worker is ShatterWorker
        # We need to import ShatterWorker locally or assume it's attached
        self._shatter_worker = self.__class__.ShatterWorkerClass(file_path, self._ephemeral_master_key)
        self._shatter_worker.finished.connect(lambda exit_code: UI_Updates.on_vault_complete(self, exit_code, file_path))
        QTimer.singleShot(800, self._shatter_worker.start)

    @staticmethod
    def on_vault_complete(self, exit_code: int, file_path: str) -> None:
        self._vault_progress.setRange(0, 100)
        self._vault_progress.setValue(100)

        if exit_code == 0:
            self._vault_title.setText("PAYLOAD SHATTERED. STORING SHARDS...")
            self._vault_btn.setText("DISPATCHING...")
            
            # Start Dispatch worker
            self._dispatch_worker = self.__class__.DispatchWorkerClass()
            self._dispatch_worker.finished.connect(lambda info: UI_Updates.on_vault_dispatch_complete(self, file_path, info))
            self._dispatch_worker.start()
        else:
            import engine
            self._vault_title.setText(f"ENGINE FAILURE: {engine.ERRORS.get(exit_code, exit_code)}")
            self._vault_btn.setEnabled(True)
            self._vault_btn.setText("RETRY")

    @staticmethod
    def on_vault_dispatch_complete(self, file_path: str, info: dict) -> None:
        import datetime
        import courier
        from ghost_map import GhostMap
        from security_core import VaultLedger

        outbox_dir = os.path.expanduser("~/.ansx_vault/outbox")
        os.makedirs(outbox_dir, mode=0o700, exist_ok=True)
        orig_name = os.path.basename(file_path)

        identity = SecurityCore.load_identity_for_user(self.current_operator) or {}
        own_pub = identity.get("public_key", "")
        urls, refs = info.get("urls", {}), info.get("refs", {})
        errors = info.get("errors", {})

        carrier = os.path.join(outbox_dir, "temp_carrier.png")
        ghost_map_path = os.path.join(outbox_dir, f"ghost_map_{orig_name}.png")
        try:
            manifest = courier.build_manifest(
                file_path, self._ephemeral_master_key,
                os.path.expanduser("~/.ansx_vault/shards"), urls, refs)
            payload_str = json.dumps(manifest)
            GhostMap.make_carrier(carrier, len(payload_str) + 64)
            # The key is wrapped to the operator's OWN public key; there is no unencrypted fallback.
            GhostMap.hide_payload_in_image(payload_str, own_pub, carrier, ghost_map_path)
        except Exception as e:
            logger.exception("Vault packaging failed")
            self._vault_title.setText(f"❌ VAULT PACKAGING FAILED: {e}")
            self._vault_title.setStyleSheet("color: #ff3333; font-size: 14px; font-weight: bold;")
            self._vault_btn.setText("RETRY")
            self._vault_btn.setEnabled(True)
            return
        finally:
            if os.path.exists(carrier):
                os.remove(carrier)

        n_cloud = len(urls)
        n_inline = len(manifest["shard_payload"])
        if not info.get("configured"):
            where = f"{n_inline}/12 shards carried inline (no cloud storage configured)"
        elif errors:
            where = f"{n_cloud} in cloud, {n_inline} inline ({len(errors)} uploads FAILED: {next(iter(errors.values()))})"
        else:
            where = f"{n_cloud} in cloud storage, {n_inline} inline"

        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        VaultLedger.add_entry(orig_name, ghost_map_path, date_str)

        self._vault_title.setText(f"✅ VAULTED: {orig_name} — {where}")
        self._vault_title.setStyleSheet("color: #00ffcc; font-size: 14px; font-weight: bold;")
        self._vault_btn.setText("VAULT ANOTHER FILE")
        self._vault_btn.setEnabled(True)

        if hasattr(self, '_refresh_vault_gallery'):
            self._refresh_vault_gallery()
        if hasattr(self, '_refresh_send_file_dropdown'):
            self._refresh_send_file_dropdown()

    @staticmethod
    def build_send_ui(self) -> None:
        layout = QVBoxLayout(self._page_send)
        layout.setContentsMargins(60, 50, 60, 50)

        header = QLabel("✉️ STAGE 2: GHOST COURIER")
        header.setStyleSheet("color: #ffffff; font-size: 32px; font-weight: bold; font-family: 'Courier New';")
        layout.addWidget(header)
        layout.addSpacing(20)

        contacts_layout = QHBoxLayout()
        self._receiver_combo = QComboBox()
        self._receiver_combo.setStyleSheet(
            "background-color: #0f1520; color: #00ffcc; padding: 8px; "
            "font-weight: bold; font-size: 14px; border: 1px solid #333;"
        )
        self._receiver_combo.addItem("— select receiver —")

        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedWidth(40)
        refresh_btn.setToolTip("Refresh user list from Web3 registry")
        refresh_btn.setStyleSheet(
            "background-color: #1a1c23; color: #7AA2F7; font-size: 18px; "
            "border: 1px solid #333; border-radius: 4px;"
        )
        refresh_btn.clicked.connect(lambda: UI_Updates._refresh_user_dropdown(self))

        contacts_layout.addWidget(QLabel("TARGET RECEIVER: "))
        contacts_layout.addWidget(self._receiver_combo, 1)
        contacts_layout.addWidget(refresh_btn)
        layout.addLayout(contacts_layout)

        # ── Payload dropdown ───────────────────────────────────────────────
        payload_layout = QHBoxLayout()
        self._payload_combo = QComboBox()
        self._payload_combo.setStyleSheet(
            "background-color: #0f1520; color: #ffcc00; padding: 8px; "
            "font-weight: bold; font-size: 14px; border: 1px solid #333;"
        )
        self._payload_combo.addItem("— select vaulted file to send —")
        
        # Method to populate this dropdown
        def refresh_payloads():
            from security_core import VaultLedger
            self._payload_combo.clear()
            self._payload_combo.addItem("— select vaulted file to send —", None)
            ledger = VaultLedger.load()
            for item in ledger:
                display = f"{item['original_filename']} (Vaulted: {item['date_vaulted']})"
                self._payload_combo.addItem(display, item['ghost_map_path'])
        
        self._refresh_send_file_dropdown = refresh_payloads
        
        payload_layout.addWidget(QLabel("PAYLOAD TO SEND: "))
        payload_layout.addWidget(self._payload_combo, 1)
        layout.addLayout(payload_layout)

        # Populate immediately on page load
        UI_Updates._refresh_user_dropdown(self)
        layout.addSpacing(20)

        action_panel = QFrame()
        action_panel.setStyleSheet("background-color: #0c0d14; border: 1px solid #1a1c23; border-radius: 12px;")
        ap_layout = QVBoxLayout(action_panel)
        ap_layout.setContentsMargins(40, 40, 40, 40)

        # Ghost map preview — shows image created during vault stage
        self._ghost_map_preview = QLabel()
        self._ghost_map_preview.setFixedHeight(120)
        self._ghost_map_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ghost_map_preview.setStyleSheet(
            "border: 2px dashed #333; border-radius: 8px; background: #0a0b10; color: #444; font-size: 12px;"
        )
        self._ghost_map_preview.setText("Select a file above to view carrier")
        ap_layout.addWidget(self._ghost_map_preview)
        
        # Update preview when file selected
        self._payload_combo.currentIndexChanged.connect(
            lambda idx: UI_Updates.set_ghost_map_preview(
                self, self._payload_combo.itemData(idx) if idx > 0 else None
            )
        )

        self._ghost_map_label = QLabel("")
        self._ghost_map_label.setStyleSheet("color: #00ffcc; font-size: 11px; font-family: monospace;")
        self._ghost_map_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ap_layout.addWidget(self._ghost_map_label)

        self._send_title = QLabel("SELECT RECEIVER AND VAULTED FILE TO PROCEED")
        self._send_title.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        self._send_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._send_title.setWordWrap(True)
        ap_layout.addWidget(self._send_title)

        self._send_progress = QProgressBar()
        self._send_progress.hide()
        self._send_progress.setFixedHeight(8)
        ap_layout.addWidget(self._send_progress)

        btn_row = QHBoxLayout()
        self._send_btn = QPushButton("🔒 SEND SECURELY")
        self._send_btn.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #5d259e, stop:1 #0d47a1);"
            " color: white; border-radius: 6px; font-weight: bold; font-size: 16px; height: 60px;"
        )
        self._send_btn.clicked.connect(self._initiate_send)
        self._send_cancel_btn = QPushButton("CANCEL")
        self._send_cancel_btn.setFixedHeight(60)
        self._send_cancel_btn.setStyleSheet(
            "background: #2a1010; color: #ff6666; border: 1px solid #ff3333; border-radius: 6px; font-weight: bold;")
        self._send_cancel_btn.clicked.connect(self._cancel_send)
        self._send_cancel_btn.hide()
        btn_row.addWidget(self._send_btn, 1)
        btn_row.addWidget(self._send_cancel_btn)
        ap_layout.addLayout(btn_row)

        note = QLabel("End-to-end encrypted: only the receiver's private key can open it. "
                      "The relay only learns who sent to whom, when, and how large — never the contents. "
                      "The receiver must accept before anything is opened.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        ap_layout.addWidget(note)

        layout.addWidget(action_panel)
        layout.addSpacing(16)
        sent_hdr = QLabel("SENT ITEMS")
        sent_hdr.setStyleSheet("color: #7AA2F7; font-size: 14px; font-weight: bold; letter-spacing: 1px;")
        layout.addWidget(sent_hdr)
        self._outbox_list = QListWidget()
        self._outbox_list.setStyleSheet(
            "background: #0a0b10; color: #ddd; border: 1px solid #1a1c23; border-radius: 8px;"
            " padding: 8px; font-size: 12px; font-family: monospace;")
        layout.addWidget(self._outbox_list, 1)


    @staticmethod
    def _refresh_user_dropdown(self) -> None:
        """Fetches all registered users from Web3 and populates the dropdown."""
        try:
            import web3_bridge
            engine = web3_bridge.get_web3_engine()
            users = engine.get_all_users()
        except Exception as e:
            logger.warning("Could not fetch Web3 users: %s", e)
            users = []

        current = self._receiver_combo.currentText()
        self._receiver_combo.clear()
        self._receiver_combo.addItem("— select receiver —")
        for u in users:
            self._receiver_combo.addItem(u)

        # Try to restore previous selection
        idx = self._receiver_combo.findText(current)
        if idx >= 0:
            self._receiver_combo.setCurrentIndex(idx)

    @staticmethod
    def _create_ghost_map(self) -> str:
        """Re-wrap the selected vault's manifest for the chosen receiver. Returns the outgoing PNG path."""
        import courier
        from ghost_map import GhostMap
        from cloud_dispatcher import CloudDispatcher

        target_user = self._receiver_combo.currentText().strip()
        if not target_user or target_user.startswith("—"):
            QMessageBox.warning(self, "Hold", "Select a receiver from the dropdown.")
            return None

        if not hasattr(self, '_payload_combo') or self._payload_combo.currentIndex() <= 0:
            QMessageBox.warning(self, "Hold", "No Vaulted File selected. Please vault a file first and select it.")
            return None
        vault_map = self._payload_combo.itemData(self._payload_combo.currentIndex())
        if not vault_map or not os.path.exists(vault_map):
            QMessageBox.warning(self, "Hold", "Selected file not found on disk.")
            return None

        identity = SecurityCore.load_identity_for_user(self.current_operator) or {}
        if not identity.get("private_key"):
            QMessageBox.warning(self, "Locked", "Your keys are locked. Log in again with your NFC card.")
            return None

        # Receiver's key must match the pinned key (trust-on-first-use); a swapped key is refused.
        try:
            import web3_bridge
            engine = web3_bridge.get_web3_engine()
            receiver_pub = engine.resolve_trusted_public_key(target_user)
        except Exception as e:
            QMessageBox.critical(self, "Receiver key rejected", str(e))
            return None

        outbox = os.path.expanduser("~/.ansx_vault/outbox")
        os.makedirs(outbox, mode=0o700, exist_ok=True)
        carrier = os.path.join(outbox, "temp_send_carrier.png")
        out_img = os.path.join(outbox, f"send_{target_user}_{os.path.basename(vault_map)}")
        try:
            manifest = json.loads(GhostMap.extract_payload_from_image(identity["private_key"], vault_map))
            manifest = courier.refresh_manifest_urls(manifest, CloudDispatcher())
            payload_str = json.dumps(manifest)
            GhostMap.make_carrier(carrier, len(payload_str) + 64)
            GhostMap.hide_payload_in_image(payload_str, receiver_pub, carrier, out_img)
        except Exception as e:
            logger.exception("Ghost map re-wrap failed")
            QMessageBox.critical(self, "Ghost Map Error", str(e))
            return None
        finally:
            if os.path.exists(carrier):
                os.remove(carrier)

        info = SecurityCore.get_contact_info(target_user) or {}
        fp = info.get("fingerprint", "")
        trust = "verified" if info.get("verified") else "UNVERIFIED — confirm this fingerprint with them"
        self._send_title.setText(f"GHOST MAP ENCRYPTED FOR {target_user.upper()} ✔  key {fp[:17]}… ({trust})")
        self._send_title.setStyleSheet("color: #00ffcc; font-size: 13px;")
        return out_img

    @staticmethod
    def set_ghost_map_preview(self, image_path: str) -> None:

        """Update the Send page to show a thumbnail of the ghost map image."""
        from PyQt6.QtGui import QPixmap
        if not hasattr(self, "_ghost_map_preview"):
            return
        if image_path and os.path.exists(image_path):
            pix = QPixmap(image_path).scaledToHeight(
                150,
                Qt.TransformationMode.SmoothTransformation
            )
            self._ghost_map_preview.setPixmap(pix)
            self._ghost_map_label.setText(f"✔ Ghost Map: {os.path.basename(image_path)}")
            self._send_title.setText("ENTER RECEIVER USERNAME → CLICK SEND")
            self._send_title.setStyleSheet("color: #ffcc00; font-size: 15px; font-weight: bold;")
        else:
            self._ghost_map_preview.setText("No Ghost Map yet — Vault a file first")
            self._ghost_map_label.setText("")

    @staticmethod
    def initiate_send(self) -> None:
        client = getattr(self, "_relay", None)
        if client is None:
            QMessageBox.warning(self, "Not connected", "Log in with your NFC card first.")
            return
        recipient = self._receiver_combo.currentText().strip()
        vault_map = self._payload_combo.itemData(self._payload_combo.currentIndex()) \
            if self._payload_combo.currentIndex() > 0 else None

        # Resume an interrupted upload of the SAME vault to the SAME person (re-wrapping would change the bytes).
        resume = getattr(self, "_resume_send", None)
        if resume and resume["key"] == (vault_map, recipient) and os.path.exists(resume["path"]):
            out_img, resume_id = resume["path"], resume["id"]
        else:
            out_img, resume_id = UI_Updates._create_ghost_map(self), None
            if not out_img:
                return
        key = (vault_map, recipient)

        self._send_btn.setEnabled(False)
        self._send_cancel_btn.show()
        self._send_progress.show()
        self._send_progress.setRange(0, 100)
        self._send_progress.setValue(0)
        self._send_title.setStyleSheet("color: #B14CFF; font-size: 13px; font-weight: bold;")

        self._send_worker = transfer.SendWorker(client, out_img, recipient, resume_id)
        self._send_worker.status.connect(self._send_title.setText)
        self._send_worker.progress.connect(self._send_progress.setValue)
        self._send_worker.finished.connect(
            lambda ok, msg, tid: UI_Updates.on_send_finished(self, ok, msg, tid, key, out_img))
        self._send_worker.start()

    @staticmethod
    def cancel_send(self) -> None:
        worker = getattr(self, "_send_worker", None)
        if worker is not None and worker.isRunning():
            worker.cancel()
            self._send_cancel_btn.setEnabled(False)

    @staticmethod
    def on_send_finished(self, ok: bool, message: str, transfer_id: str, key: tuple, out_img: str) -> None:
        self._send_btn.setEnabled(True)
        self._send_cancel_btn.hide()
        self._send_cancel_btn.setEnabled(True)
        self._send_progress.hide()
        if ok:
            self._resume_send = None
            self._send_title.setText(f"✅ {message}")
            self._send_title.setStyleSheet("color: #00ffcc; font-size: 14px; font-weight: bold;")
            try:
                os.remove(out_img)        # the sender can no longer open it anyway; do not leave copies around
            except OSError:
                pass
        else:
            # keep the wrapped file only if the relay still holds a partial upload we can resume
            self._resume_send = {"key": key, "path": out_img, "id": transfer_id} if transfer_id else None
            if not transfer_id:
                try:
                    os.remove(out_img)
                except OSError:
                    pass
            self._send_title.setText(f"❌ {message}")
            self._send_title.setStyleSheet("color: #ff3333; font-size: 13px;")

    @staticmethod
    def on_outbox_updated(self, items: list) -> None:
        import datetime
        labels = {"uploading": "⬆ uploading", "ready": "⏳ waiting for pickup", "delivered": "✔ delivered",
                  "rejected": "✖ declined by receiver", "expired": "⌛ expired unclaimed", "cancelled": "⊘ cancelled"}
        self._outbox_list.clear()
        if not items:
            self._outbox_list.addItem("Nothing sent yet.")
            return
        for it in items:
            when = datetime.datetime.fromtimestamp(it["created"]).strftime("%b %d %H:%M")
            self._outbox_list.addItem(
                f"→ {it['to']:<14} {transfer.human_size(it['size']):>9}   {labels.get(it['state'], it['state'])}   {when}")

    @staticmethod
    def build_receive_ui(self) -> None:
        layout = QVBoxLayout(self._page_receive)
        layout.setContentsMargins(60, 50, 60, 50)

        header = QLabel("📥 STAGE 3: RECEIVE & RECONSTRUCT")
        header.setStyleSheet("color: #ffffff; font-size: 32px; font-weight: bold; font-family: 'Courier New';")
        layout.addWidget(header)
        layout.addSpacing(10)

        self._rx_relay_status = QLabel("Log in to connect to the relay.")
        self._rx_relay_status.setStyleSheet("color: #888; font-size: 12px; font-family: monospace;")
        layout.addWidget(self._rx_relay_status)
        layout.addSpacing(10)

        inbox_hdr = QLabel("INCOMING — nothing is opened until you accept")
        inbox_hdr.setStyleSheet("color: #7AA2F7; font-size: 14px; font-weight: bold; letter-spacing: 1px;")
        layout.addWidget(inbox_hdr)
        self._inbox_list = QListWidget()
        self._inbox_list.setStyleSheet(
            "background: #0a0b10; color: #ddd; border: 1px solid #B14CFF; border-radius: 8px;"
            " padding: 8px; font-size: 13px; font-family: monospace;")
        self._inbox_list.setMinimumHeight(150)
        layout.addWidget(self._inbox_list, 1)

        row = QHBoxLayout()
        self._rx_accept_btn = QPushButton("✔ ACCEPT & UNLOCK")
        self._rx_accept_btn.setStyleSheet(
            "background: #0d47a1; color: white; border-radius: 6px; font-weight: bold; height: 44px;")
        self._rx_accept_btn.clicked.connect(self._accept_selected)
        self._rx_reject_btn = QPushButton("✖ DECLINE")
        self._rx_reject_btn.setStyleSheet(
            "background: #2a1010; color: #ff6666; border: 1px solid #ff3333; border-radius: 6px; font-weight: bold; height: 44px;")
        self._rx_reject_btn.clicked.connect(self._reject_selected)
        self._rx_open_btn = QPushButton("📂 OPEN A GHOST MAP FILE…")
        self._rx_open_btn.setStyleSheet(
            "background: #1a1c23; color: #aaa; border: 1px solid #333; border-radius: 6px; height: 44px;")
        self._rx_open_btn.clicked.connect(self._initiate_receive)
        self._rx_open_btn.setToolTip("For a ghost map you received outside the relay (USB stick, chat, e-mail).")
        row.addWidget(self._rx_accept_btn, 2)
        row.addWidget(self._rx_reject_btn, 1)
        row.addWidget(self._rx_open_btn, 2)
        layout.addLayout(row)

        self._rx_progress = QProgressBar()
        self._rx_progress.setFixedHeight(8)
        self._rx_progress.hide()
        layout.addWidget(self._rx_progress)

        action_panel = QFrame()
        action_panel.setStyleSheet("background-color: #0c0d14; border: 1px solid #1a1c23; border-radius: 12px;")
        ap_layout = QVBoxLayout(action_panel)
        self._rx_title = QLabel("AWAITING INCOMING TRANSFERS")
        self._rx_title.setStyleSheet("color: #ffffff; font-size: 16px; font-weight: bold;")
        self._rx_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rx_title.setWordWrap(True)
        ap_layout.addWidget(self._rx_title)
        layout.addWidget(action_panel)

    @staticmethod
    def _trust_label(sender: str, relay_fp: str) -> str:
        info = SecurityCore.get_contact_info(sender)
        if not info:
            return "key not pinned yet"
        if relay_fp and info.get("fingerprint") != relay_fp:
            return "⚠ KEY CHANGED"
        return "✔ verified key" if info.get("verified") else "unverified key"

    @staticmethod
    def on_inbox_updated(self, items: list) -> None:
        import datetime
        selected = self._inbox_list.currentItem().data(Qt.ItemDataRole.UserRole)["id"] \
            if self._inbox_list.currentItem() else None
        self._inbox_items = items
        self._inbox_list.clear()
        if not items:
            self._inbox_list.addItem("Inbox empty.")
            self._rx_accept_btn.setEnabled(False)
            self._rx_reject_btn.setEnabled(False)
            return
        self._rx_accept_btn.setEnabled(True)
        self._rx_reject_btn.setEnabled(True)
        for it in items:
            when = datetime.datetime.fromtimestamp(it["created"]).strftime("%b %d %H:%M")
            trust = UI_Updates._trust_label(it["from"], it.get("sender_fingerprint", ""))
            entry = QListWidgetItem(f"{it['from']:<14} {transfer.human_size(it['size']):>9}   {when}   [{trust}]")
            entry.setData(Qt.ItemDataRole.UserRole, it)
            self._inbox_list.addItem(entry)
            if it["id"] == selected:
                self._inbox_list.setCurrentItem(entry)
        if self._inbox_list.currentItem() is None:
            self._inbox_list.setCurrentRow(0)

    @staticmethod
    def on_new_transfers(self, items: list) -> None:
        names = ", ".join(sorted({i["from"] for i in items}))
        self._rx_title.setText(f"📥 New transfer from {names} — review it in the list above")
        self._rx_title.setStyleSheet("color: #B14CFF; font-size: 16px; font-weight: bold;")
        QApplication.alert(self)          # flash the taskbar / dock; never a blocking popup

    @staticmethod
    def _selected_item(self):
        cur = self._inbox_list.currentItem()
        data = cur.data(Qt.ItemDataRole.UserRole) if cur else None
        return data if isinstance(data, dict) else None

    @staticmethod
    def accept_selected(self) -> None:
        item = UI_Updates._selected_item(self)
        client = getattr(self, "_relay", None)
        if not item or client is None:
            return
        trust = UI_Updates._trust_label(item["from"], item.get("sender_fingerprint", ""))
        fp = item.get("sender_fingerprint", "unknown")
        warn = "\n\n⚠ This sender's key differs from the one you pinned. Do NOT accept unless you know why." \
            if "CHANGED" in trust else ""
        answer = QMessageBox.question(
            self, "Accept transfer?",
            f"Accept {transfer.human_size(item['size'])} from '{item['from']}'?\n\n"
            f"Sender key: {trust}\nFingerprint: {fp}{warn}\n\n"
            "It will be downloaded, checked against the sender's hash, and only then opened with your key.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._rx_accept_btn.setEnabled(False)
        self._rx_progress.setRange(0, 100)
        self._rx_progress.setValue(0)
        self._rx_progress.show()
        self._dl_worker = transfer.DownloadWorker(client, item)
        self._dl_worker.status.connect(self._rx_title.setText)
        self._dl_worker.progress.connect(self._rx_progress.setValue)
        self._dl_worker.finished.connect(lambda ok, msg, path: UI_Updates.on_download_finished(self, ok, msg, path))
        self._dl_worker.start()

    @staticmethod
    def on_download_finished(self, ok: bool, message: str, path: str) -> None:
        self._rx_progress.hide()
        self._rx_accept_btn.setEnabled(True)
        if not ok:
            self._rx_title.setText(f"❌ {message}")
            self._rx_title.setStyleSheet("color: #ff3333; font-size: 15px; font-weight: bold;")
            return
        self._rx_title.setText("✅ Downloaded and verified. Choose where to save the reconstructed file…")
        self._rx_title.setStyleSheet("color: #00ffcc; font-size: 15px; font-weight: bold;")
        self._pending_incoming_ghost_map = path
        UI_Updates.initiate_receive(self)

    @staticmethod
    def reject_selected(self) -> None:
        item = UI_Updates._selected_item(self)
        client = getattr(self, "_relay", None)
        if not item or client is None:
            return
        if QMessageBox.question(self, "Decline transfer?", f"Decline the transfer from '{item['from']}'? "
                                "It will be deleted from the relay.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            client.reject(item["id"])
            self._rx_title.setText("Transfer declined and deleted from the relay.")
            self._rx_title.setStyleSheet("color: #aaa; font-size: 15px; font-weight: bold;")
        except RelayError as exc:
            self._rx_title.setText(f"❌ {exc.detail}")

    @staticmethod
    def initiate_receive(self) -> None:
        # If a ghost map arrived automatically via P2P, use it without asking
        p2p_map = getattr(self, "_pending_incoming_ghost_map", None)
        if p2p_map and os.path.exists(p2p_map):
            courier_img = p2p_map
            self._pending_incoming_ghost_map = None  # consume it
        else:
            courier_img, _ = QFileDialog.getOpenFileName(
                self, "Select Ghost Map Image",
                transfer.INCOMING_DIR,
                "Images (*.png)"
            )
            if not courier_img:
                return

        out_file, _ = QFileDialog.getSaveFileName(self, "Save Reconstructed File As")
        if not out_file:
            return

        from ghost_map import GhostMap
        identity = SecurityCore.load_identity_for_user(self.current_operator)
        private_key = identity.get("private_key", "") if identity else ""

        try:
            decrypted_json_str = GhostMap.extract_payload_from_image(private_key, courier_img)
            manifest = json.loads(decrypted_json_str)
        except Exception as e:
            QMessageBox.critical(self, "DNA Failure", f"Failed to extract Super-Payload.\n{str(e)}")
            return

        self._rx_title.setText(">> GHOST MAP DECRYPTED. DOWNLOADING FROM CLOUD... <<")
        self._rx_title.setStyleSheet("color: #ff3366;")
        QApplication.processEvents()


        import courier
        from cloud_dispatcher import CloudDispatcher
        shard_dir = os.path.expanduser("~/.ansx_vault/downloaded_shards")
        try:
            # If these are the owner's own vault URLs and they expired, re-issue them (needs the storage credentials).
            manifest = courier.refresh_manifest_urls(manifest, CloudDispatcher())
            n_valid = courier.fetch_shards(manifest, shard_dir)
        except courier.CourierError as e:
            QMessageBox.critical(self, "Shard retrieval failed", str(e))
            self._rx_title.setText(f"❌ {e}")
            self._rx_title.setStyleSheet("color: #ff3333;")
            return

        decrypted_aes_key = manifest.get("ephemeral_key", "")
        if not decrypted_aes_key:
            QMessageBox.critical(self, "Error", "Manifest carries no key.")
            return

        self._rx_title.setText(f">> {n_valid} VERIFIED SHARDS. RECONSTRUCTING… <<")
        QApplication.processEvents()

        # Initiate Unshatter Worker
        self._unshatter_worker = self.__class__.UnshatterWorkerClass(shard_dir, out_file, decrypted_aes_key)
        self._unshatter_worker.finished.connect(lambda code: UI_Updates.on_unshatter_complete(self, code))
        self._unshatter_worker.start()

    @staticmethod
    def on_unshatter_complete(self, exit_code: int) -> None:
        import shutil
        import engine
        shutil.rmtree(os.path.expanduser("~/.ansx_vault/downloaded_shards"), ignore_errors=True)
        if exit_code == 0:
            self._rx_title.setText("SUCCESS: PAYLOAD RECONSTRUCTED AND AUTHENTICATED.")
            self._rx_title.setStyleSheet("color: #00ffcc;")
        else:
            self._rx_title.setText(f"❌ {engine.ERRORS.get(exit_code, f'ENGINE ERROR {exit_code}')}")
            self._rx_title.setStyleSheet("color: #ff3333;")

# Injecting into the App Class dynamically
def inject(app_class, shatter_cls, dispatch_cls, unshatter_cls):
    app_class.ShatterWorkerClass   = shatter_cls
    app_class.DispatchWorkerClass  = dispatch_cls
    app_class.UnshatterWorkerClass = unshatter_cls

    app_class._build_vault_ui      = UI_Updates.build_vault_ui
    app_class._initiate_vault      = UI_Updates.initiate_vault
    app_class._on_vault_complete   = UI_Updates.on_vault_complete
    app_class._build_send_ui       = UI_Updates.build_send_ui
    app_class._initiate_send       = UI_Updates.initiate_send
    app_class._cancel_send         = UI_Updates.cancel_send
    app_class._on_outbox_updated   = UI_Updates.on_outbox_updated
    app_class._on_inbox_updated    = UI_Updates.on_inbox_updated
    app_class._on_new_transfers    = UI_Updates.on_new_transfers
    app_class._accept_selected     = UI_Updates.accept_selected
    app_class._reject_selected     = UI_Updates.reject_selected
    app_class._build_receive_ui    = UI_Updates.build_receive_ui
    app_class._initiate_receive    = UI_Updates.initiate_receive
    app_class._on_unshatter_complete = UI_Updates.on_unshatter_complete
    app_class._refresh_user_dropdown = UI_Updates._refresh_user_dropdown
    app_class._create_ghost_map    = UI_Updates._create_ghost_map
