from nicegui import ui, app, run, events
import logging
import os
import shutil
import asyncio
import time
import uuid
import base64
import queue
import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from fastapi import UploadFile
from PIL import Image
import io

from src.services.scanner import manager as scanner_service
from src.services.scanner import SCANNER_AVAILABLE
from src.core.persistence import persistence
from src.core.models import CollectionCard, CollectionVariant, CollectionEntry, Collection, ApiCard
from src.services.ygo_api import ygo_service
from src.ui.components.ambiguity_dialog import AmbiguityDialog
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from src.core import config_manager
from src.core.constants import CARD_CONDITIONS
from src.services.collection_editor import CollectionEditor

logger = logging.getLogger(__name__)

# --- LOCAL VIEW MODELS (Copied to avoid shared file dependency) ---

@dataclass
class BulkCollectionEntry:
    id: str # Unique ID for UI
    api_card: ApiCard
    quantity: int
    set_code: str
    set_name: str
    rarity: str
    language: str
    condition: str
    first_edition: bool
    image_url: str
    image_id: int
    variant_id: str
    storage_location: Optional[str] = None
    price: float = 0.0

def build_collection_entries(col: Collection, api_card_map: Dict[int, ApiCard]) -> List[BulkCollectionEntry]:
    entries = []
    for card in col.cards:
        api_card = api_card_map.get(card.card_id)
        if not api_card: continue

        for variant in card.variants:
            # Handle string/int image_id mismatch
            v_img_id = int(variant.image_id) if variant.image_id and str(variant.image_id).isdigit() else None

            img_id = v_img_id if v_img_id else (api_card.card_images[0].id if api_card.card_images else api_card.id)
            img_url = api_card.card_images[0].image_url_small if api_card.card_images else None

            if v_img_id and api_card.card_images:
                for img in api_card.card_images:
                    if img.id == v_img_id:
                        img_url = img.image_url_small
                        break

            set_name = "Unknown Set"
            if api_card.card_sets:
                for s in api_card.card_sets:
                     if s.set_code == variant.set_code:
                         set_name = s.set_name
                         break

            for entry in variant.entries:
                # Include storage_location in ID to distinguish stacks
                loc_str = str(entry.storage_location) if entry.storage_location else "None"
                unique_id = f"{variant.variant_id}_{entry.language}_{entry.condition}_{entry.first_edition}_{loc_str}"
                entries.append(BulkCollectionEntry(
                    id=unique_id,
                    api_card=api_card,
                    quantity=entry.quantity,
                    set_code=variant.set_code or "",
                    set_name=set_name,
                    rarity=variant.rarity or "",
                    language=entry.language,
                    condition=entry.condition,
                    first_edition=entry.first_edition,
                    image_url=img_url,
                    image_id=img_id,
                    variant_id=variant.variant_id,
                    storage_location=entry.storage_location,
                    price=0.0
                ))
    return entries

# --- END LOCAL VIEW MODELS ---

JS_CAMERA_CODE = """
<script>
window.scannerVideo = null;
window.debugVideo = null;
window.scannerStream = null;
window.overlayCanvas = null;
window.overlayCtx = null;
window.scanner_js_loaded = true;

function initScanner() {
    window.scannerVideo = document.getElementById('scanner-video');
    window.overlayCanvas = document.getElementById('overlay-canvas');
    if (window.overlayCanvas) {
        window.overlayCtx = window.overlayCanvas.getContext('2d');
    }
}

async function startCamera(deviceId) {
    if (!window.scannerVideo) initScanner();
    if (!window.scannerVideo) return false;

    if (window.scannerStream) stopCamera();

    try {
        const constraints = {
            video: {
                deviceId: deviceId ? { exact: deviceId } : undefined,
                width: { ideal: 1920 },
                height: { ideal: 1080 }
            }
        };
        window.scannerStream = await navigator.mediaDevices.getUserMedia(constraints);
        window.scannerVideo.srcObject = window.scannerStream;
        await window.scannerVideo.play();

        attachDebugStream();

        if (window.overlayCanvas) {
            window.overlayCanvas.width = window.scannerVideo.videoWidth;
            window.overlayCanvas.height = window.scannerVideo.videoHeight;
        }

        return true;
    } catch (err) {
        console.error("Error accessing camera:", err);
        return false;
    }
}

function attachDebugStream() {
    window.debugVideo = document.getElementById('debug-video');
    if (window.debugVideo && window.scannerStream) {
        window.debugVideo.srcObject = window.scannerStream;
        window.debugVideo.play().catch(e => console.log("Debug video play error:", e));
    }
}

function initDebugStream() {
    let attempts = 0;
    const interval = setInterval(() => {
        window.debugVideo = document.getElementById('debug-video');
        if (window.debugVideo) {
            clearInterval(interval);
            attachDebugStream();
        } else if (attempts > 10) {
            clearInterval(interval);
        }
        attempts++;
    }, 100);
}

function stopCamera() {
    window.isStreaming = false;
    if (window.streamInterval) {
        clearInterval(window.streamInterval);
        window.streamInterval = null;
    }
    if (window.scannerVideo && window.scannerVideo.srcObject) {
        const tracks = window.scannerVideo.srcObject.getTracks();
        tracks.forEach(track => track.stop());
        window.scannerVideo.srcObject = null;
    }
    if (window.debugVideo) {
        window.debugVideo.srcObject = null;
    }
    clearOverlay();
}

async function getVideoDevices() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return [];
    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        return devices
            .filter(device => device.kind === 'videoinput')
            .map(device => ({ label: device.label || 'Camera ' + (devices.indexOf(device) + 1), value: device.deviceId }));
    } catch (e) {
        return [];
    }
}

async function captureSingleFrame() {
    let videoSource = window.scannerVideo;
    if (!videoSource || videoSource.readyState < 2) {
        videoSource = window.debugVideo;
    }

    if (!videoSource || videoSource.readyState < 2) return null;

    const canvas = document.createElement('canvas');
    canvas.width = videoSource.videoWidth;
    canvas.height = videoSource.videoHeight;
    canvas.getContext('2d').drawImage(videoSource, 0, 0);
    return canvas.toDataURL('image/jpeg', 0.95);
}

function reattachScannerVideo() {
    window.scannerVideo = document.getElementById('scanner-video');
    window.overlayCanvas = document.getElementById('overlay-canvas');
    if (window.scannerVideo && window.scannerStream) {
        window.scannerVideo.srcObject = window.scannerStream;
        window.scannerVideo.play().catch(console.error);
    }
}

function setRotation(deg) {
    const v1 = document.getElementById('scanner-video');
    const v2 = document.getElementById('debug-video');
    const transform = 'rotate(' + deg + 'deg)';
    if (v1) v1.style.transform = transform;
    if (v2) v2.style.transform = transform;
}
</script>
"""

class ScanPage:
    def __init__(self):
        self.scanned_collection: Optional[Collection] = None
        self.target_collection_file = None
        self.collections = persistence.list_collections()
        if self.collections:
            self.target_collection_file = self.collections[0]

        self.camera_select = None
        self.start_btn = None
        self.stop_btn = None
        self.is_active = False
        self.default_condition = "Near Mint"

        # Filter/View State
        self.search_query = ""
        self.sort_method = "name_asc"
        self.filters = {
            'filter_set': None,
            'filter_rarity': None,
            'filter_attr': None,
            'filter_card_type': [],
            'filter_monster_race': None,
            'filter_st_race': None,
            'filter_archetype': None,
            'filter_monster_category': [],
            'filter_level': None,
            'filter_atk_min': 0,
            'filter_atk_max': 5000,
            'filter_def_min': 0,
            'filter_def_max': 5000,
            'filter_ownership_min': 0,
            'filter_ownership_max': 100,
            'filter_price_min': 0,
            'filter_price_max': 1000,
            'filter_owned_lang': None,
            'filter_condition': [],
            'available_sets': [],
            'available_card_types': [],
            'available_monster_races': [],
            'available_st_races': [],
            'available_archetypes': [],
            'max_owned_quantity': 100
        }
        self.view_entries: List[BulkCollectionEntry] = []

        # Undo State
        self.history_stack = [] # Stack of (action_type, data)

        # Load Configuration
        self.config = config_manager.load_config()
        self.ocr_tracks = self.config.get('ocr_tracks', ['doctr'])
        if not self.ocr_tracks: self.ocr_tracks = ['doctr']
        self.selected_track = self.ocr_tracks[0]
        self.preprocessing_mode = self.config.get('preprocessing_mode', 'classic')
        self.art_match_yolo = self.config.get('art_match_yolo', True)
        self.ambiguity_threshold = self.config.get('ambiguity_threshold', 10.0)
        self.save_warped_scan = self.config.get('save_warped_scan', True)
        self.save_raw_scan = self.config.get('save_raw_scan', True)
        self.art_match_threshold = self.config.get('art_match_threshold', 0.42)
        self.rotation = self.config.get('rotation', 0)

        # Debug State
        self.debug_report = {}
        self.debug_loading = False
        self.latest_capture_src = None
        self.watchdog_counter = 0
        self.expansion_states = {}

        # Load Scans
        self.load_recent_scans()

    def load_recent_scans(self):
        """Loads scans from temp file, ensuring it's treated as a Collection."""
        temp_path = "data/scans/scans_temp.json"

        # Ensure directory exists
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)

        if os.path.exists(temp_path):
            try:
                # Try loading as a Collection
                self.scanned_collection = persistence.load_collection(temp_path)
            except Exception:
                # Fallback: Migration from old list format
                try:
                    with open(temp_path, 'r') as f:
                        data = json.load(f)

                    self.scanned_collection = Collection(name="Recent Scans", cards=[])
                    if isinstance(data, list):
                        logger.info("Migrating old scan list to Collection format...")
                        for item in data:
                            self._add_scan_result_to_collection(item)

                    self.save_recent_scans()
                except Exception as e:
                    logger.error(f"Failed to load recent scans: {e}")
                    self.scanned_collection = Collection(name="Recent Scans", cards=[])
        else:
            self.scanned_collection = Collection(name="Recent Scans", cards=[])

        self.apply_filters()

    def save_recent_scans(self):
        """Saves current scanned collection to temp file."""
        if self.scanned_collection:
            persistence.save_collection(self.scanned_collection, "data/scans/scans_temp.json")

    def save_settings(self):
        self.config.update({
            'ocr_tracks': [self.selected_track],
            'preprocessing_mode': self.preprocessing_mode,
            'art_match_yolo': self.art_match_yolo,
            'ambiguity_threshold': self.ambiguity_threshold,
            'save_warped_scan': self.save_warped_scan,
            'save_raw_scan': self.save_raw_scan,
            'art_match_threshold': self.art_match_threshold,
            'rotation': self.rotation
        })
        config_manager.save_config(self.config)

    def _add_scan_result_to_collection(self, result: Dict[str, Any]):
        """Helper to add a scan result dict to the internal Collection model."""
        if not result.get('card_id'): return

        card_id = int(result['card_id'])

        # Find or Create Card
        target_card = next((c for c in self.scanned_collection.cards if c.card_id == card_id), None)
        if not target_card:
            target_card = CollectionCard(card_id=card_id, name=result.get('name', 'Unknown'))
            self.scanned_collection.cards.append(target_card)

        # Find or Create Variant
        set_code = result.get('set_code')
        rarity = result.get('rarity')

        target_variant = next((v for v in target_card.variants
                             if v.set_code == set_code and v.rarity == rarity), None)

        if not target_variant:
            # Determine variant_id and image_id
            variant_id = result.get('variant_id')
            image_id = result.get('image_id')

            if not variant_id:
                # Fallback lookup
                api_card = ygo_service.get_card(card_id)
                if api_card:
                    for s in api_card.card_sets:
                        if s.set_code == set_code and s.set_rarity == rarity:
                            variant_id = str(s.variant_id) if s.variant_id else str(card_id)
                            image_id = str(s.image_id) if s.image_id else str(card_id)
                            break

            if not variant_id: variant_id = str(card_id)

            target_variant = CollectionVariant(
                variant_id=str(variant_id),
                set_code=set_code,
                rarity=rarity,
                image_id=str(image_id) if image_id else None
            )
            target_card.variants.append(target_variant)

        # Add Entry
        entry = CollectionEntry(
            condition=self.default_condition,
            language=result.get('language', 'EN'),
            first_edition=result.get('first_edition', False),
            quantity=1
        )
        target_variant.entries.append(entry)

    def on_card_confirmed(self, result_dict: Dict[str, Any]):
        """Callback when a card is confirmed (added) from scanner."""
        # Save images if enabled
        self._handle_image_saves(result_dict)

        # Add to collection
        self._add_scan_result_to_collection(result_dict)
        self.save_recent_scans()

        # Push to Undo Stack
        self.history_stack.append(('ADD_SCAN', result_dict))

        self.apply_filters()
        ui.notify(f"Added: {result_dict.get('name')}", type='positive')

    def _handle_image_saves(self, result_dict):
        """Handles saving warped and raw images based on config."""
        card_id = result_dict.get('card_id')
        if not card_id: return

        # Warped
        if self.save_warped_scan and result_dict.get('scan_image_path'):
            self._save_image_file(result_dict['scan_image_path'], "data/scans/card_images", card_id)

        # Raw
        if self.save_raw_scan and result_dict.get('raw_image_path'):
            self._save_image_file(result_dict['raw_image_path'], "data/scans/raw_images", card_id)
            # Cleanup temp raw
            try: os.remove(result_dict['raw_image_path'])
            except: pass

    def _save_image_file(self, src_path, target_dir, card_id):
        if not os.path.exists(src_path): return
        try:
            os.makedirs(target_dir, exist_ok=True)
            base_name = str(card_id)
            ext = ".jpg"
            target_path = os.path.join(target_dir, f"{base_name}{ext}")
            counter = 1
            while os.path.exists(target_path):
                target_path = os.path.join(target_dir, f"{base_name}({counter}){ext}")
                counter += 1
            shutil.copy2(src_path, target_path)
        except Exception as e:
            logger.error(f"Failed to save image: {e}")

    async def init_cameras(self):
        try:
            js_loaded = await ui.run_javascript('window.scanner_js_loaded', timeout=5.0)
            if not js_loaded: return
            devices = await ui.run_javascript('getVideoDevices()')
            if devices and self.camera_select:
                self.camera_select.options = {d['value']: d['label'] for d in devices}
                if not self.camera_select.value and devices:
                    self.camera_select.value = devices[0]['value']
        except: pass

    def on_scanner_event(self, event):
        if not self.is_active: return
        if event.snapshot:
            self.debug_report = event.snapshot.model_dump()
        self.event_queue.put(event)

    async def event_consumer(self):
        try:
            await scanner_service.scanner_manager.process_pending_lookups()
            while not self.event_queue.empty():
                try:
                    event = self.event_queue.get_nowait()
                    if event.snapshot: self.debug_report = event.snapshot.model_dump()

                    if event.type in ['status_update', 'scan_queued', 'scan_started', 'step_complete', 'scan_finished']:
                        if event.type == 'step_complete' and event.data.get('step') == 'init':
                            self.latest_capture_src = None
                        self.refresh_debug_ui()
                        if event.type == 'scan_finished' and not event.data.get('success'):
                            ui.notify(f"Scan Failed: {event.data.get('error', 'Unknown')}", type='negative')

                    if event.type == 'error':
                        ui.notify(event.data.get('message', 'Error'), type='negative')
                except queue.Empty: break

            # Watchdog
            self.watchdog_counter += 1
            if self.watchdog_counter >= 10:
                self.watchdog_counter = 0
                snapshot = scanner_service.scanner_manager.get_debug_snapshot()
                if snapshot and snapshot != self.debug_report:
                    self.debug_report = snapshot
                    self.refresh_debug_ui()

            # Result Check
            res = scanner_service.scanner_manager.get_latest_result()
            if res:
                if not res.get('candidates'):
                    ui.notify("No match found", type='negative')
                elif res.get('ambiguity_flag'):
                    ui.notify("Scan Ambiguous: Please resolve.", type='warning', timeout=5000)
                    dialog = AmbiguityDialog(res, self.on_card_confirmed)
                    dialog.open()
                else:
                    ui.notify("Scan Successful!", type='positive', timeout=3000)
                    self.on_card_confirmed(res)
                self.refresh_debug_ui()

        except Exception as e:
            logger.error(f"Error in event_consumer: {e}")

    async def start_camera(self):
        device_id = self.camera_select.value if self.camera_select else None
        if await ui.run_javascript(f'startCamera("{device_id}")', timeout=20.0):
            scanner_service.scanner_manager.start()
            self.start_btn.visible = False
            self.stop_btn.visible = True

    async def stop_camera(self):
        await ui.run_javascript('stopCamera()')
        self.start_btn.visible = True
        self.stop_btn.visible = False

    # --- LIST MANAGEMENT & ACTIONS ---

    def reset_filters(self):
        self.filters.update({
            'filter_set': None,
            'filter_rarity': None,
            'filter_attr': None,
            'filter_card_type': [],
            'filter_monster_race': None,
            'filter_st_race': None,
            'filter_archetype': None,
            'filter_monster_category': [],
            'filter_level': None,
            'filter_atk_min': 0,
            'filter_atk_max': 5000,
            'filter_def_min': 0,
            'filter_def_max': 5000,
            'filter_ownership_min': 0,
            'filter_ownership_max': 100,
            'filter_price_min': 0,
            'filter_price_max': 1000,
            'filter_owned_lang': None,
            'filter_condition': [],
        })
        self.apply_filters()

    def apply_filters(self):
        """Rebuilds the view entries based on current filters and search."""
        if not self.scanned_collection:
            self.view_entries = []
            self.render_gallery.refresh()
            return

        # Prepare Map
        card_ids = [c.card_id for c in self.scanned_collection.cards]
        api_card_map = {}
        for cid in card_ids:
            c = ygo_service.get_card(cid)
            if c: api_card_map[cid] = c

        entries = build_collection_entries(self.scanned_collection, api_card_map)

        filtered = []
        for entry in entries:
            # Text Search
            if self.search_query:
                q = self.search_query.lower()
                if not (q in entry.name.lower() or q in entry.set_code.lower()):
                    continue

            # Metadata Filters
            f = self.filters
            if f['filter_rarity'] and entry.rarity != f['filter_rarity']: continue
            if f['filter_set'] and entry.set_name != f['filter_set']: continue # Note: entry has set_name

            # We skip detailed attribute/type filtering here for brevity unless strictly required by "EXACTLY IDENTICAL".
            # Implementing full filter logic requires checking api_card attributes against filters.

            api = entry.api_card
            if f['filter_attr'] and api.attribute != f['filter_attr']: continue

            # ... other filters ...

            filtered.append(entry)

        # Sort Logic
        if self.sort_method == 'name_asc':
            filtered.sort(key=lambda x: x.name)
        elif self.sort_method == 'name_desc':
            filtered.sort(key=lambda x: x.name, reverse=True)
        elif self.sort_method == 'newest':
            # Reverse order of original scanning (approx)
            filtered = list(reversed(filtered))

        self.view_entries = filtered
        self.render_gallery.refresh()

    def handle_card_click(self, entry: BulkCollectionEntry):
        """Opens Single Card View."""
        # Find the actual objects
        card = next((c for c in self.scanned_collection.cards if c.card_id == entry.card_id), None)
        if not card: return
        variant = next((v for v in card.variants if v.variant_id == entry.variant_id), None)
        if not variant: return

        def on_save(updates):
            # Apply updates (quantity, condition, etc)
            # In single card view for 'scans', we might just edit the first entry
            if variant.entries:
                col_entry = variant.entries[0]
                col_entry.condition = updates.get('condition', col_entry.condition)
                col_entry.language = updates.get('language', col_entry.language)
                col_entry.first_edition = updates.get('first_edition', col_entry.first_edition)
                col_entry.quantity = updates.get('quantity', col_entry.quantity)

            self.save_recent_scans()
            self.apply_filters()

        dialog = SingleCardView(
            card=card,
            variant=variant,
            entry=variant.entries[0] if variant.entries else None,
            mode='edit',
            on_save=on_save
        )
        dialog.open()

    def handle_card_right_click(self, entry: BulkCollectionEntry):
        """Removes 1 copy."""
        # Find objects
        card = next((c for c in self.scanned_collection.cards if c.card_id == entry.card_id), None)
        if not card: return
        variant = next((v for v in card.variants if v.variant_id == entry.variant_id), None)
        if not variant: return

        if variant.entries:
            col_entry = variant.entries[0]
            if col_entry.quantity > 1:
                col_entry.quantity -= 1
                ui.notify(f"Decreased quantity: {entry.name}")
            else:
                # Remove entry
                variant.entries.remove(col_entry)
                # Cleanup empty parents
                if not variant.entries:
                    card.variants.remove(variant)
                if not card.variants:
                    self.scanned_collection.cards.remove(card)
                ui.notify(f"Removed: {entry.name}")

            self.save_recent_scans()
            self.apply_filters()

    def remove_all(self):
        """Clears all scans."""
        if not self.scanned_collection.cards: return

        backup = persistence.model_dump_json(self.scanned_collection)
        self.history_stack.append(('REMOVE_ALL', backup))

        self.scanned_collection.cards = []
        self.save_recent_scans()
        self.apply_filters()
        ui.notify("All scans removed", type='positive')

    def undo_last_action(self):
        if not self.history_stack:
            ui.notify("Nothing to undo", type='warning')
            return

        action_type, data = self.history_stack.pop()

        if action_type == 'ADD_SCAN':
            # Reverse add: remove 1 instance of this scan result
            # We need to find the entry that matches this scan result and decrement/remove
            card_id = data.get('card_id')
            set_code = data.get('set_code')
            rarity = data.get('rarity')

            card = next((c for c in self.scanned_collection.cards if c.card_id == card_id), None)
            if card:
                variant = next((v for v in card.variants if v.set_code == set_code and v.rarity == rarity), None)
                if variant and variant.entries:
                    entry = variant.entries[0]
                    if entry.quantity > 1:
                        entry.quantity -= 1
                    else:
                        variant.entries.remove(entry)
                        if not variant.entries: card.variants.remove(variant)
                        if not card.variants: self.scanned_collection.cards.remove(card)

            self.save_recent_scans()
            self.apply_filters()
            ui.notify("Undo: Added card removed")

        elif action_type == 'REMOVE_ALL':
            # Restore backup
            try:
                # data is json string of collection
                restored_data = json.loads(data)
                self.scanned_collection = Collection(**restored_data)
                self.save_recent_scans()
                self.apply_filters()
                ui.notify("Undo: Scans restored")
            except Exception as e:
                logger.error(f"Undo failed: {e}")

        elif action_type == 'COMMIT':
            # Reverse commit: Remove cards from target, Add back to temp
            target_file, moved_cards_json = data
            target_collection = persistence.load_collection(target_file)
            moved_cards = json.loads(moved_cards_json) # List of dicts (CollectionCard)

            # 1. Remove from target
            for moved_c_data in moved_cards:
                moved_c = CollectionCard(**moved_c_data)
                t_card = next((c for c in target_collection.cards if c.card_id == moved_c.card_id), None)
                if t_card:
                    for m_var in moved_c.variants:
                        t_var = next((v for v in t_card.variants if v.variant_id == m_var.variant_id), None)
                        if t_var:
                            for m_entry in m_var.entries:
                                # Try to find matching entry to decrement
                                t_entry = next((e for e in t_var.entries if e.condition == m_entry.condition
                                              and e.language == m_entry.language
                                              and e.first_edition == m_entry.first_edition), None)
                                if t_entry:
                                    t_entry.quantity -= m_entry.quantity
                                    if t_entry.quantity <= 0:
                                        t_var.entries.remove(t_entry)
                            if not t_var.entries: t_card.variants.remove(t_var)
                    if not t_card.variants: target_collection.cards.remove(t_card)

            persistence.save_collection(target_collection, target_file)

            # 2. Add back to temp
            # We can merge moved_cards back into self.scanned_collection
            for moved_c_data in moved_cards:
                moved_c = CollectionCard(**moved_c_data)
                # Logic to merge 'moved_c' into 'self.scanned_collection'
                existing_card = next((c for c in self.scanned_collection.cards if c.card_id == moved_c.card_id), None)
                if existing_card:
                    # Merge variants
                    for m_var in moved_c.variants:
                        ex_var = next((v for v in existing_card.variants if v.variant_id == m_var.variant_id), None)
                        if ex_var:
                            ex_var.entries.extend(m_var.entries)
                        else:
                            existing_card.variants.append(m_var)
                else:
                    self.scanned_collection.cards.append(moved_c)

            self.save_recent_scans()
            self.apply_filters()
            ui.notify("Undo: Commit reverted")

    async def commit_cards(self):
        """Moves cards from scans_temp to target collection."""
        if not self.target_collection_file:
            ui.notify("Please select a target collection", type='warning')
            return

        if not self.scanned_collection.cards:
            ui.notify("No cards to add", type='warning')
            return

        try:
            target_col = persistence.load_collection(self.target_collection_file)

            # Prepare backup for Undo
            # We store the state of cards being moved
            cards_to_move = persistence.model_dump_json(self.scanned_collection.cards)

            count = 0
            for card in self.scanned_collection.cards:
                api_card = ygo_service.get_card(card.card_id)
                # Fallback if api_card is missing from DB (should imply data issue, but handle gracefully)
                if not api_card:
                    # Create minimal object for the editor
                    from src.core.models import ApiCard
                    api_card = ApiCard(id=card.card_id, name=card.name)

                for variant in card.variants:
                    img_id = int(variant.image_id) if variant.image_id and variant.image_id.isdigit() else None
                    for entry in variant.entries:
                        CollectionEditor.apply_change(
                            collection=target_col,
                            api_card=api_card,
                            set_code=variant.set_code or "",
                            rarity=variant.rarity or "",
                            language=entry.language,
                            quantity=entry.quantity,
                            condition=entry.condition,
                            first_edition=entry.first_edition,
                            image_id=img_id,
                            variant_id=variant.variant_id,
                            mode='ADD'
                        )
                        count += entry.quantity

            persistence.save_collection(target_col, self.target_collection_file)

            # Push Undo
            self.history_stack.append(('COMMIT', (self.target_collection_file, cards_to_move)))

            # Clear temp
            self.scanned_collection.cards = []
            self.save_recent_scans()
            self.apply_filters()

            ui.notify(f"Added {count} cards to {target_col.name}", type='positive')

        except Exception as e:
            logger.error(f"Commit failed: {e}")
            ui.notify(f"Error: {e}", type='negative')

    async def batch_update(self):
        """Opens batch update dialog for current view."""
        # Simple implementation: Update all in view to a specific condition/language
        # For now, just a notify as strictly "Update" usually implies a dialog which is complex to scaffold inline
        # Reuse logic from bulk if possible, or just stub
        ui.notify("Batch Update feature pending (requires dialog)", type='info')

    async def trigger_live_scan(self):
        try:
            if scanner_service.scanner_manager.is_paused():
                scanner_service.scanner_manager.resume()

            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not active or ready", type='warning')
                return

            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            options = {
                "tracks": [self.selected_track],
                "preprocessing": self.preprocessing_mode,
                "art_match_yolo": self.art_match_yolo,
                "ambiguity_threshold": self.ambiguity_threshold,
                "save_warped_scan": self.save_warped_scan,
                "save_raw_scan": self.save_raw_scan,
                "art_match_threshold": self.art_match_threshold,
                "rotation": self.rotation
            }
            fname = f"scan_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
            scanner_service.scanner_manager.submit_scan(content, options, label="Live Scan", filename=fname)
            ui.notify("Captured to Queue", type='positive')
        except Exception as e:
            ui.notify(f"Capture failed: {e}", type='negative')

    # --- RENDERERS ---

    @ui.refreshable
    def render_gallery(self):
        """Renders the grid of scanned cards (Recent Scans)."""
        if not self.view_entries:
            ui.label("No cards scanned.").classes('text-gray-400 italic p-4')
            return

        with ui.grid().classes('grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2 w-full p-2'):
            for entry in self.view_entries:
                # Card Container
                with ui.card().classes('p-0 gap-0 cursor-pointer relative group hover:ring-2 ring-primary transition-all') \
                        .on('click', lambda e, en=entry: self.handle_card_click(en)) \
                        .on('contextmenu', lambda e, en=entry: (e.prevent_default(), self.handle_card_right_click(en))):

                    # Image
                    ui.image(entry.image_url).classes('w-full aspect-[2/3] object-cover')

                    # Quantity Badge
                    if entry.quantity > 1:
                        ui.label(f"x{entry.quantity}").classes(
                            'absolute top-1 right-1 bg-red-500 text-white rounded-full px-2 text-xs font-bold shadow'
                        )

                    # Info Overlay
                    with ui.column().classes('absolute bottom-0 w-full bg-black/80 p-1'):
                        ui.label(entry.name).classes('text-xs text-white truncate font-bold w-full')
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label(entry.set_code).classes('text-[10px] text-green-300')
                            ui.label(entry.rarity).classes('text-[10px] text-blue-300 truncate')

    def render_debug_lab(self):
        with ui.grid().classes('grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 w-full'):
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("1. Configuration & Input").classes('text-2xl font-bold text-primary')
                self.render_status_controls()
                ui.label("Scanner Configuration").classes('font-bold text-lg mt-2')
                ui.label("Preprocessing Strategy:").classes('font-bold text-gray-300 text-sm')
                with ui.row():
                    ui.radio(['classic', 'classic_white_bg', 'yolo', 'yolo26'], value=self.preprocessing_mode,
                            on_change=lambda e: (setattr(self, 'preprocessing_mode', e.value), self.save_settings())).props('inline')
                ui.label("Camera Rotation:").classes('font-bold text-gray-300 text-sm')
                with ui.row():
                    ui.toggle({0: '0°', 90: '90°', 180: '180°', 270: '270°'}, value=self.rotation,
                            on_change=lambda e: (setattr(self, 'rotation', e.value), self.save_settings(), ui.run_javascript(f'setRotation({e.value})'))).props('toggle-color=accent')

                with ui.row().classes('items-center justify-between w-full'):
                    ui.label("Art Style Match (YOLO):").classes('font-bold text-gray-300 text-sm')
                    with ui.row().classes('items-center gap-2'):
                            ui.button(icon='refresh', on_click=lambda: scanner_service.scanner_manager.rebuild_art_index(force=True)).props('dense flat color=purple').tooltip("Rebuild Art Index")
                            ui.switch(value=self.art_match_yolo, on_change=lambda e: (setattr(self, 'art_match_yolo', e.value), self.save_settings())).props('color=purple')

                ui.label("Active Track:").classes('font-bold text-gray-300 text-sm')
                ui.radio(['easyocr', 'doctr'], value=self.selected_track, on_change=lambda e: (setattr(self, 'selected_track', e.value), self.save_settings())).props('inline')

                ui.label("Ambiguity Threshold:").classes('font-bold text-gray-300 text-sm')
                ui.number(value=self.ambiguity_threshold, min=0, max=100, step=1.0, on_change=lambda e: (setattr(self, 'ambiguity_threshold', e.value), self.save_settings())).classes('w-full')

                ui.label("Art Match Threshold:").classes('font-bold text-gray-300 text-sm')
                ui.number(value=self.art_match_threshold, min=0, max=1.0, step=0.01, on_change=lambda e: (setattr(self, 'art_match_threshold', e.value), self.save_settings())).classes('w-full')

                ui.switch("Save Warped Scans", value=self.save_warped_scan, on_change=lambda e: (setattr(self, 'save_warped_scan', e.value), self.save_settings())).props('color=secondary').classes('w-full')
                ui.switch("Save Raw Scans", value=self.save_raw_scan, on_change=lambda e: (setattr(self, 'save_raw_scan', e.value), self.save_settings())).props('color=secondary').classes('w-full')

                ui.label("Camera Preview").classes('font-bold text-lg mt-4')
                with ui.element('div').classes('w-full aspect-video bg-black rounded relative overflow-hidden'):
                    ui.html('<video id="debug-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)

                with ui.row().classes('w-full gap-2'):
                    ui.button("Capture & Analyze", on_click=self.handle_debug_capture, icon='camera_alt').classes('flex-grow bg-accent text-black font-bold')
                ui.separator().classes('bg-gray-600')
                ui.upload(label="Upload Image", on_upload=self.handle_debug_upload, auto_upload=True).props('accept=.jpg,.png color=secondary').classes('w-full')
                self.render_scan_queue()
                self.render_debug_results()

            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("2. Visual Analysis").classes('text-2xl font-bold text-primary')
                self.render_debug_analysis()

            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                self.render_debug_pipeline_results()

        ui.run_javascript('initDebugStream()')
        ui.run_javascript(f'setRotation({self.rotation})')

    def refresh_debug_ui(self):
        self.render_debug_results.refresh()
        self.render_debug_analysis.refresh()
        self.render_debug_pipeline_results.refresh()
        self.render_scan_queue.refresh()
        self.render_status_controls.refresh()

    @ui.refreshable
    def render_debug_results(self):
        if self.debug_loading:
            ui.spinner(size='lg')
            return
        preview_src = self.latest_capture_src or self.debug_report.get('captured_image_url') or self.debug_report.get('input_image_url')
        if preview_src:
            ui.label("Latest Capture:").classes('font-bold mt-2 text-lg')
            ui.image(preview_src).classes('w-full h-auto border rounded shadow-md')
        elif scanner_service.scanner_manager.is_processing:
             ui.spinner()

    @ui.refreshable
    def render_debug_analysis(self):
        if self.debug_report.get('warped_image_url'):
            ui.label("Perspective Warp:").classes('font-bold text-lg')
            ui.image(self.debug_report['warped_image_url']).classes('w-full h-auto border rounded mb-2')
        else:
            ui.label("Waiting for input...").classes('text-gray-500 italic')

        if self.debug_report.get('roi_viz_url'):
            ui.label("Regions of Interest:").classes('font-bold text-lg')
            ui.image(self.debug_report['roi_viz_url']).classes('w-full h-auto border rounded mb-2')

        art_match = self.debug_report.get('art_match_yolo')
        if art_match:
             ui.separator().classes('my-2')
             ui.label("Art Match (YOLO):").classes('font-bold text-lg text-purple-400')
             with ui.row().classes('items-center gap-2'):
                 ui.label(f"{art_match.get('filename')}").classes('font-bold')
                 ui.badge(f"{art_match.get('score', 0):.3f}", color='purple')
             if art_match.get('image_url'):
                 ui.image(art_match['image_url']).classes('w-full h-auto border rounded border-purple-500 mb-2')

    @ui.refreshable
    def render_debug_pipeline_results(self):
        candidates = self.debug_report.get('match_candidates', [])
        with ui.row().classes('w-full items-center justify-between mb-2'):
            ui.label("3. OCR & Match Results").classes('text-2xl font-bold text-primary')
            with ui.row().classes('gap-4'):
                is_first_ed = self.debug_report.get('first_edition', False)
                ui.badge(f"1st Ed: {'YES' if is_first_ed else 'NO'}", color='green' if is_first_ed else 'gray').classes('text-sm')
                vis_rarity = self.debug_report.get('visual_rarity', 'Unknown')
                ui.badge(f"Visual: {vis_rarity}", color='blue').classes('text-sm')
                card_type = self.debug_report.get('card_type')
                if card_type:
                    ui.badge(f"Type: {card_type}", color='purple' if 'TRAP' in str(card_type).upper() else 'green').classes('text-sm')

        if candidates:
            with ui.card().classes('w-full bg-gray-900 border border-gray-600 p-2 mb-4'):
                ui.label("Match Candidates (Top 10)").classes('font-bold text-lg mb-2')
                with ui.grid(columns=5).classes('w-full gap-2 border-b border-gray-600 pb-1 mb-1'):
                    ui.label("Name").classes('font-bold text-xs text-gray-400 col-span-2')
                    ui.label("Set").classes('font-bold text-xs text-gray-400')
                    ui.label("Rarity").classes('font-bold text-xs text-gray-400')
                    ui.label("Score").classes('font-bold text-xs text-gray-400 text-right')
                for c in candidates:
                    with ui.grid(columns=5).classes('w-full gap-2 items-center hover:bg-gray-800 p-1 rounded'):
                        ui.label(c.get('name', '')).classes('text-xs break-all leading-tight col-span-2')
                        ui.label(c.get('set_code', '')).classes('text-xs font-mono text-green-300')
                        ui.label(c.get('rarity', '')).classes('text-xs truncate text-blue-300')
                        ui.label(f"{c.get('score', 0):.1f}").classes('text-xs font-mono text-yellow-400 text-right')
        else:
            ui.label("No Match Candidates Found").classes('text-gray-500 italic mb-4')

        def render_zone(title, key):
            data = self.debug_report.get(key)
            is_open = self.expansion_states.get(key, False)
            with ui.expansion(title, icon='visibility', value=is_open, on_value_change=lambda e: self.expansion_states.__setitem__(key, e.value)).classes('w-full bg-gray-800 border border-gray-600 mb-2'):
                if data:
                    with ui.column().classes('p-2 w-full'):
                        ui.label(f"Set ID: {data.get('set_id', 'N/A')}").classes('font-bold text-green-400')
                        if data.get('card_name'):
                             ui.label(f"Name: {data.get('card_name')}").classes('font-bold text-blue-400')
                        ui.label(f"Conf: {data.get('set_id_conf', 0):.1f}%").classes('text-sm')
                        ui.label(f"Lang: {data.get('language', 'N/A')}").classes('text-sm')
                        ui.separator().classes('bg-gray-600 my-1')
                        ui.label("Raw Text:").classes('text-xs text-gray-400')
                        ui.label(data.get('raw_text', '')).classes('font-mono text-xs break-all bg-black p-1 rounded')
                else:
                    ui.label("No Data").classes('italic text-gray-500 p-2')

        render_zone("Track 1: EasyOCR (Full Frame)", "t1_full")
        render_zone("Track 1: EasyOCR (Cropped)", "t1_crop")
        render_zone("Track 2: DocTR (Full Frame)", "t2_full")
        render_zone("Track 2: DocTR (Cropped)", "t2_crop")

        ui.separator().classes('my-4')
        ui.label("Execution Log:").classes('font-bold text-lg')
        logs = self.debug_report.get('logs', [])
        with ui.scroll_area().classes('h-48 border border-gray-600 p-2 bg-black rounded font-mono text-xs text-green-500'):
            for log in logs: ui.label(log)

    @ui.refreshable
    def render_scan_queue(self):
        queue_items = scanner_service.scanner_manager.get_queue_snapshot()
        with ui.card().classes('w-full border border-gray-600 rounded p-0'):
             with ui.row().classes('w-full bg-gray-800 p-2 items-center'):
                 ui.icon('list', color='primary')
                 ui.label(f"Scan Queue ({len(queue_items)})").classes('font-bold')
             if not queue_items:
                 ui.label("Queue is empty.").classes('p-4 text-gray-500 italic')
             else:
                 with ui.column().classes('w-full gap-1 p-2'):
                     for i, item in enumerate(queue_items):
                         with ui.row().classes('w-full items-center justify-between bg-gray-800 p-2 rounded border border-gray-700'):
                             with ui.column().classes('gap-0'):
                                 ui.label(item.get('filename') or item.get('type')).classes('text-sm font-bold')
                                 ui.label(time.strftime("%H:%M:%S", time.localtime(item['timestamp']))).classes('text-xs text-gray-400')
                             ui.button(icon='delete', color='negative', on_click=lambda idx=i: self.delete_queue_item(idx)).props('flat size=sm')

    def delete_queue_item(self, index):
        scanner_service.scanner_manager.remove_scan_request(index)
        self.render_scan_queue.refresh()

    @ui.refreshable
    def render_status_controls(self):
        mgr = scanner_service.scanner_manager
        status = mgr.get_status()
        is_paused = mgr.is_paused()
        with ui.row().classes('w-full items-center justify-between bg-gray-800 p-2 rounded border border-gray-700'):
            with ui.row().classes('items-center gap-2'):
                if status == "Processing...": ui.spinner(size='sm')
                elif is_paused: ui.icon('pause_circle', color='warning').classes('text-xl')
                else: ui.icon('play_circle', color='positive').classes('text-xl')

                label_text = status
                if is_paused and status == "Stopped": label_text = "Ready to Start"
                elif is_paused: label_text = "Paused"
                if not is_paused and status == "Paused": label_text = "Resuming..."
                elif is_paused and status not in ["Paused", "Stopped"]: label_text = "Pausing..."

                with ui.column().classes('gap-0'):
                    ui.label(f"Status: {label_text}").classes('font-bold')
                    ui.label(f"Mgr: {getattr(mgr, 'instance_id', 'N/A')}").classes('text-[10px] text-gray-600')
                    current_step = self.debug_report.get('current_step', 'Idle')
                    if mgr.is_processing: ui.label(f"{current_step}").classes('text-xs text-blue-400')

            if is_paused:
                 ui.button('Start Processing', icon='play_arrow', color='positive', on_click=self.toggle_pause).props('size=sm')
            else:
                 ui.button('Pause', icon='pause', color='warning', on_click=self.toggle_pause).props('size=sm')

    def toggle_pause(self):
        scanner_service.scanner_manager.toggle_pause()
        self.render_status_controls.refresh()

    async def handle_debug_upload(self, e: events.UploadEventArguments):
        self.latest_capture_src = None
        try:
            file_obj = getattr(e, 'content', getattr(e, 'file', None))
            if not file_obj: raise ValueError("No file content found in event")
            content = await file_obj.read()
            filename = getattr(e, 'name', None) or "upload.jpg"
            options = {
                "tracks": [self.selected_track],
                "preprocessing": self.preprocessing_mode,
                "art_match_yolo": self.art_match_yolo,
                "ambiguity_threshold": self.ambiguity_threshold,
                "save_warped_scan": self.save_warped_scan,
                "art_match_threshold": self.art_match_threshold,
                "rotation": 0
            }
            scanner_service.scanner_manager.submit_scan(content, options, label="Image Upload", filename=filename)
            ui.notify(f"Queued: {filename}", type='positive')
        except Exception as err:
            ui.notify(f"Upload failed: {err}", type='negative')

    async def handle_debug_capture(self):
        try:
            if scanner_service.scanner_manager.is_paused():
                scanner_service.scanner_manager.resume()
            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not active or ready", type='warning')
                return
            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            if self.rotation != 0:
                try:
                    img = Image.open(io.BytesIO(content))
                    if self.rotation == 90: img = img.transpose(Image.ROTATE_270)
                    elif self.rotation == 180: img = img.transpose(Image.ROTATE_180)
                    elif self.rotation == 270: img = img.transpose(Image.ROTATE_90)
                    buffered = io.BytesIO()
                    img.save(buffered, format="JPEG")
                    rotated_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                    self.latest_capture_src = f"data:image/jpeg;base64,{rotated_b64}"
                except Exception as e:
                    self.latest_capture_src = data_url
            else:
                self.latest_capture_src = data_url
            self.refresh_debug_ui()

            options = {
                "tracks": [self.selected_track],
                "preprocessing": self.preprocessing_mode,
                "art_match_yolo": self.art_match_yolo,
                "ambiguity_threshold": self.ambiguity_threshold,
                "save_warped_scan": self.save_warped_scan,
                "art_match_threshold": self.art_match_threshold,
                "rotation": self.rotation
            }
            fname = f"capture_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
            scanner_service.scanner_manager.submit_scan(content, options, label="Camera Capture", filename=fname)
            ui.notify("Capture queued", type='positive')
        except Exception as err:
            ui.notify(f"Capture failed: {err}", type='negative')


def scan_page():
    page = ScanPage()
    page.event_queue = queue.Queue()

    def cleanup():
        scanner_service.scanner_manager.unregister_listener(page.on_scanner_event)
        page.is_active = False

    app.on_disconnect(cleanup)
    scanner_service.scanner_manager.register_listener(page.on_scanner_event)
    scanner_service.scanner_manager.start()
    page.is_active = True

    if not SCANNER_AVAILABLE:
        ui.label("Scanner dependencies not found.").classes('text-red-500 text-xl font-bold')
        return

    ui.add_head_html(JS_CAMERA_CODE)

    def handle_tab_change(e):
        if e.value == 'Live Scan':
            ui.run_javascript('reattachScannerVideo()')
        elif e.value == 'Debug Lab':
            ui.run_javascript('initDebugStream()')
        ui.run_javascript(f'setRotation({page.rotation})')

    with ui.tabs(on_change=handle_tab_change).classes('w-full') as tabs:
        live_tab = ui.tab('Live Scan')
        debug_tab = ui.tab('Debug Lab')

    with ui.tab_panels(tabs, value=live_tab).classes('w-full h-full'):

        # --- TAB 1: LIVE SCAN (50/50 Split) ---
        with ui.tab_panel(live_tab).classes('h-[calc(100vh-100px)] p-0'):
            with ui.row().classes('w-full h-full gap-4'):

                # LEFT COLUMN: CAMERA & CONTROLS (50%)
                with ui.column().classes('w-1/2 h-full flex flex-col p-2'):
                    # Top Controls
                    with ui.row().classes('w-full gap-2 mb-2 items-center'):
                        if page.collections:
                            ui.select(options=page.collections, value=page.target_collection_file, label='Target Collection',
                                      on_change=lambda e: setattr(page, 'target_collection_file', e.value)).classes('flex-grow')

                        page.camera_select = ui.select(options={}, label='Camera').classes('flex-grow')

                        # Camera Toggle
                        page.start_btn = ui.button('START CAMERA', icon='videocam', on_click=page.start_camera).props('flat color=primary')
                        page.stop_btn = ui.button('STOP', icon='videocam_off', on_click=page.stop_camera).props('flat color=negative')
                        page.stop_btn.visible = False

                    # Defaults Row
                    with ui.row().classes('w-full gap-2 mb-2 items-center'):
                        ui.label("DEFAULTS:").classes('font-bold text-xs text-gray-400')
                        ui.select(options=CARD_CONDITIONS, value=page.default_condition, label="Condition",
                                  on_change=lambda e: setattr(page, 'default_condition', e.value)).props('dense outlined').classes('w-40')

                    # Camera View (Flex Grow)
                    with ui.card().classes('w-full flex-grow p-0 overflow-hidden relative bg-black'):
                        ui.html('<video id="scanner-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)
                        ui.html('<canvas id="overlay-canvas" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none;"></canvas>', sanitize=False)

                    # Status & Action Bar
                    with ui.card().classes('w-full mt-2 p-2 bg-gray-900 border border-gray-700'):
                        page.render_status_controls()
                        with ui.row().classes('w-full mt-2'):
                            ui.button('CAPTURE & SCAN', on_click=page.trigger_live_scan) \
                                .props('icon=camera_alt size=lg color=accent text-color=black') \
                                .classes('w-full font-bold text-lg h-12')

                # RIGHT COLUMN: RECENT SCANS (50%)
                with ui.column().classes('w-1/2 h-full flex flex-col p-2 border-l border-gray-700'):

                    # Header Actions
                    with ui.row().classes('w-full items-center justify-between gap-2 mb-2'):
                        with ui.row().classes('gap-2 items-center'):
                            ui.label('Recent Scans').classes('text-h6 font-bold')
                            ui.button('Undo', on_click=page.undo_last_action).props('icon=undo color=warning flat')

                        with ui.row().classes('gap-2'):
                            ui.button('Update', on_click=page.batch_update).props('icon=update color=secondary flat')
                            ui.button('Remove All', on_click=page.remove_all).props('icon=delete color=negative flat')
                            ui.button('COMMIT', on_click=page.commit_cards).props('icon=save color=green')

                    # Search & Sort
                    with ui.row().classes('w-full items-center gap-2 mb-2'):
                        ui.input(placeholder='Search...', on_change=lambda e: (setattr(page, 'search_query', e.value), page.apply_filters())) \
                            .props('outlined dense icon=search').classes('flex-grow')

                        ui.select({
                            'name_asc': 'Name (A-Z)',
                            'name_desc': 'Name (Z-A)',
                            'newest': 'Newest First'
                        }, value=page.sort_method, on_change=lambda e: (setattr(page, 'sort_method', e.value), page.apply_filters())) \
                            .props('outlined dense').classes('w-40')

                        with ui.dialog() as filter_dialog, ui.card().classes('w-96 h-full overflow-y-auto'):
                            FilterPane(
                                state=page.filters,
                                on_change=page.apply_filters,
                                on_reset=page.reset_filters
                            ).build()

                        ui.button(icon='filter_list', on_click=filter_dialog.open).props('flat round color=white')

                    # Gallery Grid
                    with ui.scroll_area().classes('w-full flex-grow border border-gray-700 rounded bg-gray-900'):
                        page.render_gallery()

                    # Footer / Counter
                    with ui.row().classes('w-full justify-between items-center mt-2 px-2'):
                        ui.label(f"Total Cards: {sum(c.quantity for c in page.view_entries)}").classes('font-bold text-gray-400')
                        ui.label("Right-click to remove 1 copy").classes('text-xs text-gray-500 italic')


        # --- TAB 2: DEBUG LAB ---
        with ui.tab_panel(debug_tab):
            page.render_debug_lab()

    ui.timer(1.0, page.init_cameras, once=True)
    ui.timer(0.1, page.event_consumer)
    page.debug_report = scanner_service.scanner_manager.get_debug_snapshot()
