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
from typing import List, Dict, Any, Optional
from fastapi import UploadFile
from PIL import Image
import io
from dataclasses import dataclass

# Import the module, not the instance, to avoid stale references on reload
from src.services.scanner import manager as scanner_service
from src.services.scanner import SCANNER_AVAILABLE
from src.core.persistence import persistence
from src.core.models import CollectionCard, CollectionVariant, CollectionEntry
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.services.collection_editor import CollectionEditor
from src.core.changelog_manager import changelog_manager
from src.ui.components.ambiguity_dialog import AmbiguityDialog
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from src.core import config_manager as scanner_config_manager
from src.core.config import config_manager as app_config_manager
from src.core.utils import extract_language_code, LANGUAGE_COUNTRY_MAP, generate_variant_id
from src.core.constants import CARD_CONDITIONS, CONDITION_ABBREVIATIONS

logger = logging.getLogger(__name__)

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

function drawOverlay(points) {
    if (!window.overlayCanvas || !window.overlayCtx || !window.scannerVideo) return;
    const w = window.overlayCanvas.width;
    const h = window.overlayCanvas.height;
    window.overlayCtx.clearRect(0, 0, w, h);

    if (!points || points.length < 4) return;
    window.overlayCtx.beginPath();
    window.overlayCtx.moveTo(points[0][0] * w, points[0][1] * h);
    for (let i = 1; i < points.length; i++) {
        window.overlayCtx.lineTo(points[i][0] * w, points[i][1] * h);
    }
    window.overlayCtx.closePath();
    window.overlayCtx.strokeStyle = '#00FF00';
    window.overlayCtx.lineWidth = 4;
    window.overlayCtx.stroke();
}

function clearOverlay() {
    if (window.overlayCtx && window.overlayCanvas) {
        window.overlayCtx.clearRect(0, 0, window.overlayCanvas.width, window.overlayCanvas.height);
    }
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
    let usingDebug = false;

    if (!videoSource || videoSource.readyState < 2) {
        if (window.debugVideo && window.debugVideo.readyState >= 2) {
             videoSource = window.debugVideo;
             usingDebug = true;
        } else {
             return null;
        }
    } else {
        if (videoSource.paused || videoSource.ended) {
             if (window.debugVideo && window.debugVideo.readyState >= 2) {
                 videoSource = window.debugVideo;
                 usingDebug = true;
             }
        }
    }

    if (videoSource.paused) {
        try {
            await videoSource.play();
        } catch (e) {
            console.error("Failed to resume video for capture:", e);
        }
    }

    const canvas = document.createElement('canvas');
    canvas.width = videoSource.videoWidth;
    canvas.height = videoSource.videoHeight;
    canvas.getContext('2d').drawImage(videoSource, 0, 0);
    return canvas.toDataURL('image/jpeg', 0.95);
}

function reattachScannerVideo() {
    window.scannerVideo = document.getElementById('scanner-video');
    window.overlayCanvas = document.getElementById('overlay-canvas');
    if (window.overlayCanvas) {
        window.overlayCtx = window.overlayCanvas.getContext('2d');
        if (window.scannerVideo) {
             window.overlayCanvas.width = window.scannerVideo.videoWidth;
             window.overlayCanvas.height = window.scannerVideo.videoHeight;
        }
    }

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

    const overlay = document.getElementById('capture-overlay');
    if (overlay) overlay.style.transform = transform;
}

function showCaptureOverlay(imageData, rotation, duration) {
    const overlay = document.getElementById('capture-overlay');
    if (!overlay) return;

    overlay.style.backgroundImage = 'url("' + imageData + '")';
    overlay.style.transform = 'rotate(' + rotation + 'deg)';
    overlay.style.opacity = '1';

    setTimeout(() => {
        overlay.style.opacity = '0';
        setTimeout(() => {
             if (overlay.style.opacity === '0') {
                 overlay.style.backgroundImage = '';
             }
        }, 500);
    }, duration);
}
</script>
"""

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
    price: float = 0.0
    scan_image_path: Optional[str] = None # For Live Scan UI

class ScanPage:
    def __init__(self):
        self.scanned_cards: List[Dict[str, Any]] = []
        self.target_collection_file = None
        self.collections = persistence.list_collections()
        if self.collections:
            self.target_collection_file = self.collections[0]

        self.camera_select = None
        self.start_btn = None
        self.stop_btn = None
        self.is_active = False
        self.default_condition = "Near Mint"

        # Load Configuration
        self.config = scanner_config_manager.load_config()

        # Initialize UI state from config
        self.ocr_tracks = self.config.get('ocr_tracks', ['doctr'])
        # Ensure ocr_tracks is valid (only one active track supported for now, but keeping list structure for compat)
        if not self.ocr_tracks:
            self.ocr_tracks = ['doctr']
        self.selected_track = self.ocr_tracks[0] # For UI Radio Button

        self.preprocessing_mode = self.config.get('preprocessing_mode', 'classic')
        self.art_match_yolo = self.config.get('art_match_yolo', True) # Default to True per request
        self.ambiguity_threshold = self.config.get('ambiguity_threshold', 10.0)
        self.save_warped_scan = self.config.get('save_warped_scan', True)
        self.save_raw_scan = self.config.get('save_raw_scan', True)
        self.art_match_threshold = self.config.get('art_match_threshold', 0.42)
        self.rotation = self.config.get('rotation', 0)
        self.overlay_duration = self.config.get('overlay_duration', 1000)

        # Data & Filters
        self.api_card_map: Dict[int, ApiCard] = {}
        self.filtered_scanned_cards: List[BulkCollectionEntry] = []
        self.single_card_view = SingleCardView()

        # Load Recent Scans
        self.load_recent_scans()
        self.filter_pane = None

        self.filter_state = {
            'available_sets': [],
            'available_monster_races': [],
            'available_st_races': [],
            'available_archetypes': [],
            'available_card_types': ['Monster', 'Spell', 'Trap', 'Skill'],

            # Filter Values
            'search_text': '',
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': ['Monster', 'Spell', 'Trap'],
            'filter_monster_race': '',
            'filter_st_race': '',
            'filter_archetype': '',
            'filter_monster_category': [],
            'filter_level': None,
            'filter_atk_min': 0, 'filter_atk_max': 5000,
            'filter_def_min': 0, 'filter_def_max': 5000,
            'filter_price_min': 0.0, 'filter_price_max': 1000.0,
            'filter_ownership_min': 0, 'filter_ownership_max': 100,
            'filter_condition': [], 'filter_owned_lang': '',

            'sort_by': 'Newest',
            'sort_desc': True,
        }

        # Undo/Redo Stacks
        self.scan_undo_stack = [] # List of (action, data) tuples
        self.scan_redo_stack = []

        # Defaults
        self.default_language = "EN"

        # Batch Update State
        self.update_apply_lang = False
        self.update_apply_cond = False

        # Debug Lab State (local cache of Pydantic model dump)
        self.debug_report = {}
        self.debug_loading = False
        self.latest_capture_src = None
        # self.was_processing is removed as we use event based updates now
        self.watchdog_counter = 0

        # UI State Persistence
        self.expansion_states = {}

    def push_scan_undo(self, action_type: str, data: Any):
        self.scan_undo_stack.append({'type': action_type, 'data': data})
        # Clear redo stack on new action
        self.scan_redo_stack.clear()

    async def undo_scan_action(self):
        if not self.scan_undo_stack:
            ui.notify("Nothing to undo.", type='warning')
            return

        action = self.scan_undo_stack.pop()
        type_ = action['type']
        data = action['data']

        if type_ == 'ADD':
            if self.scanned_cards:
                 # ADD adds to 0
                 self.scanned_cards.pop(0)

        elif type_ == 'REMOVE':
            idx = data['index']
            item = data['item']
            self.scanned_cards.insert(idx, item)

        elif type_ == 'UPDATE':
            idx = data['index']
            old_item = data['old']
            if 0 <= idx < len(self.scanned_cards):
                self.scanned_cards[idx] = old_item

        elif type_ == 'COMMIT':
             self.scanned_cards = list(data) # Restore list
             if self.target_collection_file:
                 changelog_manager.undo_last_change(self.target_collection_file)

        elif type_ == 'COMMIT_SINGLE':
             idx = data.get('index', 0)
             self.scanned_cards.insert(idx, data['scan_item'])
             if data.get('col_file'):
                 changelog_manager.undo_last_change(data['col_file'])

        self.save_recent_scans()
        await self.apply_filters()
        ui.notify("Undid last action.", type='positive')

    def save_settings(self):
        """Saves current settings to config."""
        self.config['ocr_tracks'] = [self.selected_track]
        self.config['preprocessing_mode'] = self.preprocessing_mode
        self.config['art_match_yolo'] = self.art_match_yolo
        self.config['ambiguity_threshold'] = self.ambiguity_threshold
        self.config['save_warped_scan'] = self.save_warped_scan
        self.config['save_raw_scan'] = self.save_raw_scan
        self.config['art_match_threshold'] = self.art_match_threshold
        self.config['rotation'] = self.rotation
        self.config['overlay_duration'] = self.overlay_duration

        # Sync list used by logic
        self.ocr_tracks = [self.selected_track]

        scanner_config_manager.save_config(self.config)

    def load_recent_scans(self):
        """Loads scans from temp file."""
        temp_path = "data/scans/scans_temp.json"
        if os.path.exists(temp_path):
            try:
                with open(temp_path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.scanned_cards = data
                        # Filtered list will be populated by apply_filters asynchronously
                        self.filtered_scanned_cards = []
            except Exception as e:
                logger.error(f"Failed to load recent scans: {e}")

    def save_recent_scans(self):
        """Saves current scanned cards to temp file."""
        temp_path = "data/scans/scans_temp.json"
        try:
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            with open(temp_path, 'w') as f:
                json.dump(self.scanned_cards, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save recent scans: {e}")

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
        """Callback for scanner events."""
        if not self.is_active: return

        # Push state immediately
        if event.snapshot:
            self.debug_report = event.snapshot.model_dump()
        self.event_queue.put(event)

    def on_card_confirmed(self, result_dict: Dict[str, Any]):
        """Callback from Ambiguity Dialog or direct addition."""

        # Save Warped Image logic
        if self.save_warped_scan and result_dict.get('scan_image_path') and result_dict.get('card_id'):
            try:
                src_path = result_dict['scan_image_path']
                if os.path.exists(src_path):
                    target_dir = "data/scans/card_images"
                    os.makedirs(target_dir, exist_ok=True)

                    card_id = result_dict['card_id']
                    base_name = str(card_id)
                    ext = ".jpg"
                    target_path = os.path.join(target_dir, f"{base_name}{ext}")

                    # Handle collisions
                    counter = 1
                    while os.path.exists(target_path):
                        target_path = os.path.join(target_dir, f"{base_name}({counter}){ext}")
                        counter += 1

                    shutil.copy2(src_path, target_path)
                    logger.info(f"Saved scan image to {target_path}")
            except Exception as e:
                logger.error(f"Failed to save warped scan image: {e}")

        # Save Raw Image logic
        if self.save_raw_scan and result_dict.get('raw_image_path') and result_dict.get('card_id'):
             try:
                src_path = result_dict['raw_image_path']
                if os.path.exists(src_path):
                    target_dir = "data/scans/raw_images"
                    os.makedirs(target_dir, exist_ok=True)

                    card_id = result_dict['card_id']
                    base_name = str(card_id)
                    ext = ".jpg"
                    target_path = os.path.join(target_dir, f"{base_name}{ext}")

                    # Handle collisions
                    counter = 1
                    while os.path.exists(target_path):
                        target_path = os.path.join(target_dir, f"{base_name}({counter}){ext}")
                        counter += 1

                    shutil.copy2(src_path, target_path)
                    logger.info(f"Saved raw scan image to {target_path}")
             except Exception as e:
                logger.error(f"Failed to save raw scan image: {e}")

        # Cleanup temp raw file
        if result_dict.get('raw_image_path') and os.path.exists(result_dict['raw_image_path']):
             try:
                 os.remove(result_dict['raw_image_path'])
             except: pass

        self.scanned_cards.insert(0, result_dict)
        self.push_scan_undo('ADD', result_dict)
        self.save_recent_scans()
        asyncio.create_task(self.apply_filters())
        ui.notify(f"Added: {result_dict.get('name')}", type='positive')

    async def event_consumer(self):
        """Consumes events from the local queue and updates UI."""
        try:
            # Drive the matching process
            await scanner_service.scanner_manager.process_pending_lookups()

            # 1. Process Queued Events (Fast path)
            while not self.event_queue.empty():
                try:
                    event = self.event_queue.get_nowait()

                    # Apply snapshot
                    if event.snapshot:
                        self.debug_report = event.snapshot.model_dump()

                    # Refresh logic based on event type
                    if event.type in ['status_update', 'scan_queued', 'scan_started', 'step_complete', 'scan_finished']:

                        # Clear local capture preview once the backend has initialized the new scan (showing the real rotated image)
                        if event.type == 'step_complete' and event.data.get('step') == 'init':
                            self.latest_capture_src = None

                        self.refresh_debug_ui()

                        # Handle specific finished notifications
                        if event.type == 'scan_finished':
                            if not event.data.get('success'):
                                ui.notify(f"Scan Failed: {event.data.get('error', 'Unknown')}", type='negative')

                    if event.type == 'error':
                        ui.notify(event.data.get('message', 'Error'), type='negative')

                except queue.Empty:
                    break

            # 2. Watchdog / Polling fallback (Robustness)
            # Every 10 ticks (approx 1 sec), force fetch the state
            self.watchdog_counter += 1
            if self.watchdog_counter >= 10:
                self.watchdog_counter = 0
                snapshot = scanner_service.scanner_manager.get_debug_snapshot()
                if snapshot and snapshot != self.debug_report:
                    self.debug_report = snapshot
                    self.refresh_debug_ui()

            # 3. Check result queue
            res = scanner_service.scanner_manager.get_latest_result()
            if res:
                logger.info(f"UI Received Result: {res.get('set_code')}, Ambiguous: {res.get('ambiguity_flag')}")

                # Check for empty candidates (No Match)
                if not res.get('candidates'):
                    ui.notify("No match found", type='negative')

                elif res.get('ambiguity_flag'):
                    ui.notify("Scan Ambiguous: Please resolve.", type='warning', timeout=5000)
                    dialog = AmbiguityDialog(res, self.on_card_confirmed)
                    dialog.open()
                else:
                    ui.notify("Scan Successful!", type='positive', timeout=3000)
                    self.on_card_confirmed(res)

                self.refresh_debug_ui() # Ensure final result is shown

        except Exception as e:
            logger.error(f"Error in event_consumer: {e}")

    async def start_camera(self):
        device_id = self.camera_select.value if self.camera_select else None
        try:
            if await ui.run_javascript(f'startCamera("{device_id}")', timeout=20.0):
                # We don't need to start/stop the manager here anymore, it runs daemon
                # Use dynamic import access
                scanner_service.scanner_manager.start()
                self.start_btn.visible = False
                self.stop_btn.visible = True
            else:
                 ui.notify("Failed to start camera (JS returned false)", type='negative')
        except Exception as e:
            logger.error(f"Error starting camera: {e}")
            ui.notify(f"Error starting camera: {e}", type='negative')

    async def stop_camera(self):
        await ui.run_javascript('stopCamera()')
        # We no longer stop the manager when camera stops. It runs in background.
        # scanner_service.scanner_manager.stop()
        self.start_btn.visible = True
        self.stop_btn.visible = False

    def remove_card(self, item: BulkCollectionEntry):
        try:
            # We need to find the item in scanned_cards (list of dicts) using ID or similar
            # Since BulkCollectionEntry is derived from scanned_cards, index might match if filters align?
            # Safer to find by object reference or ID logic.
            # But filtered_scanned_cards contains NEW objects created in apply_filters.
            # So simple list.remove(obj) won't work if obj is from filtered list.
            # We need to map back to original dict.

            # Use 'scan_id' or simply index?
            # In apply_filters, we iterate source = scanned_cards.
            # We can't rely on index if filtered.
            # Let's assume unique combination of properties?
            # Actually, we can attach the original dict to the entry or just use index logic carefully.

            # Let's match by memory address if possible? No, recreated.
            # Let's add a temporary '_source_ref' to the entry? No, cleanest is index.
            # But we sort.

            # Let's search by properties.
            # Or better, just store a UUID in scanned_cards when added?

            # Current quick fix: search by properties.
            target_idx = -1
            for i, raw in enumerate(self.scanned_cards):
                 # Compare critical fields
                 if (raw.get('card_id') == item.api_card.id and
                     raw.get('set_code') == item.set_code and
                     raw.get('rarity') == item.rarity and
                     raw.get('image_id') == item.image_id and
                     raw.get('quantity', 1) == item.quantity):
                     target_idx = i
                     break

            if target_idx != -1:
                removed = self.scanned_cards.pop(target_idx)
                self.push_scan_undo('REMOVE', {'index': target_idx, 'item': removed})
                self.save_recent_scans()
                asyncio.create_task(self.apply_filters())
                ui.notify("Removed card", type='info')
        except Exception as e:
            logger.error(f"Error removing card: {e}")

    def reduce_card_qty(self, item: BulkCollectionEntry):
        try:
            target_idx = -1
            for i, raw in enumerate(self.scanned_cards):
                 if (raw.get('card_id') == item.api_card.id and
                     raw.get('set_code') == item.set_code and
                     raw.get('rarity') == item.rarity and
                     raw.get('image_id') == item.image_id and
                     raw.get('quantity', 1) == item.quantity):
                     target_idx = i
                     break

            if target_idx != -1:
                raw = self.scanned_cards[target_idx]
                old_raw = raw.copy()
                qty = raw.get('quantity', 1)

                if qty > 1:
                    raw['quantity'] = qty - 1
                    self.push_scan_undo('UPDATE', {'index': target_idx, 'old': old_raw, 'new': raw.copy()})
                    self.save_recent_scans()
                    asyncio.create_task(self.apply_filters())
                    ui.notify(f"Reduced quantity to {raw['quantity']}", type='info')
                else:
                    self.remove_card(item)
        except ValueError:
            pass

    async def commit_cards(self):
        if not self.target_collection_file:
            ui.notify("Please select a target collection.", type='warning')
            return

        if not self.scanned_cards:
            ui.notify("No cards to add.", type='warning')
            return

        try:
            collection = persistence.load_collection(self.target_collection_file)

            # Prepare changelog batch
            changes = []
            count = 0

            for item in self.scanned_cards:
                if not item.get('card_id'): continue

                # Logic to add to collection (replicated from before but cleaner)
                target_card = next((c for c in collection.cards if c.card_id == item['card_id']), None)
                if not target_card:
                    target_card = CollectionCard(card_id=item['card_id'], name=item['name'])
                    collection.cards.append(target_card)

                # Determine Variant ID
                variant_id = item.get('variant_id')
                image_id = item.get('image_id')

                if not variant_id:
                    # Generate or Find
                    api_card = ygo_service.get_card(item['card_id'])
                    if api_card:
                        # Try to find in sets
                        found = False
                        for s in api_card.card_sets:
                            if s.set_code == item['set_code'] and s.set_rarity == item['rarity']:
                                variant_id = s.variant_id
                                image_id = s.image_id
                                found = True
                                break

                    if not variant_id:
                         variant_id = generate_variant_id(item['card_id'], item['set_code'], item['rarity'], image_id)

                target_variant = next((v for v in target_card.variants if v.variant_id == variant_id), None)
                if not target_variant:
                     # Fallback check by properties if ID didn't match (legacy)
                     target_variant = next((v for v in target_card.variants
                                          if v.set_code == item['set_code'] and v.rarity == item['rarity']), None)

                if not target_variant:
                    target_variant = CollectionVariant(
                        variant_id=variant_id,
                        set_code=item['set_code'],
                        rarity=item['rarity'],
                        image_id=image_id
                    )
                    target_card.variants.append(target_variant)

                entry = CollectionEntry(
                    condition=self.default_condition,
                    language=item['language'],
                    first_edition=item['first_edition'],
                    quantity=item.get('quantity', 1)
                )
                target_variant.entries.append(entry)
                count += 1

                # Log Change
                changes.append({
                    'action': 'ADD',
                    'quantity': item.get('quantity', 1),
                    'card_data': {
                        'card_id': item['card_id'],
                        'name': item['name'],
                        'set_code': item['set_code'],
                        'rarity': item['rarity'],
                        'image_id': image_id,
                        'language': item['language'],
                        'condition': self.default_condition,
                        'first_edition': item['first_edition'],
                        'variant_id': variant_id
                    }
                })

            persistence.save_collection(collection, self.target_collection_file)

            # Log Batch
            if changes:
                changelog_manager.log_batch_change(
                    self.target_collection_file,
                    f"Added {len(changes)} scanned cards",
                    changes
                )

            ui.notify(f"Added {count} cards to {collection.name}", type='positive')

            # Undo Logic
            self.push_scan_undo('COMMIT', list(self.scanned_cards))

            self.scanned_cards.clear()
            self.save_recent_scans()

            try:
                os.remove("data/scans/scans_temp.json")
            except:
                pass

            asyncio.create_task(self.apply_filters())

        except Exception as e:
            logger.error(f"Error saving collection: {e}")
            ui.notify(f"Error saving collection: {e}", type='negative')

    async def trigger_live_scan(self):
        """Triggers a scan from the Live Tab using current settings."""
        try:
            # Ensure scanner is running (unpause if needed)
            if scanner_service.scanner_manager.is_paused():
                scanner_service.scanner_manager.resume()

            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not active or ready", type='warning')
                return

            ui.run_javascript(f'showCaptureOverlay("{data_url}", {self.rotation}, {self.overlay_duration})')

            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            options = {
                "tracks": [self.selected_track], # Use the single selected track
                "preprocessing": self.preprocessing_mode,
                "art_match_yolo": self.art_match_yolo,
                "ambiguity_threshold": self.ambiguity_threshold,
                "save_warped_scan": self.save_warped_scan,
                "save_raw_scan": self.save_raw_scan,
                "art_match_threshold": self.art_match_threshold,
                "rotation": self.rotation
            }
            fname = f"scan_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
            # Use dynamic import access
            scanner_service.scanner_manager.submit_scan(content, options, label="Live Scan", filename=fname)
            ui.notify("Captured to Queue", type='positive')
        except Exception as e:
            ui.notify(f"Capture failed: {e}", type='negative')

    def refresh_debug_ui(self):
        self.render_debug_results.refresh()
        self.render_debug_analysis.refresh()
        self.render_debug_pipeline_results.refresh()
        self.render_scan_queue.refresh()
        self.render_status_controls.refresh()

    async def handle_debug_upload(self, e: events.UploadEventArguments):
        self.latest_capture_src = None
        try:
            # Handle NiceGUI version differences
            file_obj = getattr(e, 'content', getattr(e, 'file', None))
            if not file_obj:
                raise ValueError("No file content found in event")

            content = await file_obj.read()

            # Extract filename safely
            filename = getattr(e, 'name', None)
            if not filename and hasattr(file_obj, 'name'):
                filename = file_obj.name
            if not filename:
                filename = "upload.jpg"

            options = {
                "tracks": [self.selected_track],
                "preprocessing": self.preprocessing_mode,
                "art_match_yolo": self.art_match_yolo,
                "ambiguity_threshold": self.ambiguity_threshold,
                "save_warped_scan": self.save_warped_scan,
                "art_match_threshold": self.art_match_threshold,
                "rotation": 0 # Explicitly 0 for uploads
            }
            # Use dynamic import access
            scanner_service.scanner_manager.submit_scan(content, options, label="Image Upload", filename=filename)
            ui.notify(f"Queued: {filename}", type='positive')
        except Exception as err:
            ui.notify(f"Upload failed: {err}", type='negative')
        # refresh triggered by event

    async def handle_debug_capture(self):
        # refresh triggered by event
        try:
            # Ensure scanner is running (unpause if needed)
            if scanner_service.scanner_manager.is_paused():
                scanner_service.scanner_manager.resume()

            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not active or ready", type='warning')
                return

            # Decode raw capture
            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            # Create rotated preview locally if needed
            if self.rotation != 0:
                try:
                    img = Image.open(io.BytesIO(content))
                    # Map User Rotation (CW) to PIL Transpose
                    if self.rotation == 90:
                        img = img.transpose(Image.ROTATE_270)
                    elif self.rotation == 180:
                        img = img.transpose(Image.ROTATE_180)
                    elif self.rotation == 270:
                        img = img.transpose(Image.ROTATE_90)

                    buffered = io.BytesIO()
                    img.save(buffered, format="JPEG")
                    rotated_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                    self.latest_capture_src = f"data:image/jpeg;base64,{rotated_b64}"
                except Exception as e:
                    logger.error(f"Failed to rotate preview: {e}")
                    self.latest_capture_src = data_url
            else:
                self.latest_capture_src = data_url

            # We want to show the capture immediately?
            # Yes, locally.
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
            # Use dynamic import access
            scanner_service.scanner_manager.submit_scan(content, options, label="Camera Capture", filename=fname)
            ui.notify("Capture queued", type='positive')

        except Exception as err:
            ui.notify(f"Capture failed: {err}", type='negative')

    async def load_library_data(self):
        try:
            lang_code = app_config_manager.get_language().lower()
            api_cards = await ygo_service.load_card_database(lang_code)
            self.api_card_map = {c.id: c for c in api_cards}

            # Populate Filter Options
            sets = set()
            m_races = set()
            st_races = set()
            archetypes = set()

            for c in api_cards:
                if c.card_sets:
                    for s in c.card_sets:
                         sets.add(f"{s.set_name} | {s.set_code.split('-')[0] if '-' in s.set_code else s.set_code}")
                if c.archetype: archetypes.add(c.archetype)
                if "Monster" in c.type: m_races.add(c.race)
                elif "Spell" in c.type or "Trap" in c.type:
                    if c.race: st_races.add(c.race)

            self.filter_state['available_sets'][:] = sorted(list(sets))
            self.filter_state['available_monster_races'][:] = sorted(list(m_races))
            self.filter_state['available_st_races'][:] = sorted(list(st_races))
            self.filter_state['available_archetypes'][:] = sorted(list(archetypes))

            if self.filter_pane:
                self.filter_pane.update_options()

            await self.apply_filters()

        except Exception as e:
            logger.error(f"Error loading library data: {e}")
            ui.notify(f"Error loading card database: {e}", type='negative')

    def _scan_to_entry(self, item: Dict[str, Any]) -> Optional[BulkCollectionEntry]:
        """Converts a raw scan dict to a View Model Entry."""
        api_card = self.api_card_map.get(item.get('card_id'))
        if not api_card: return None

        set_code = item.get('set_code')
        rarity = item.get('rarity')

        # Determine Set Name and Image ID
        set_name = "Unknown Set"
        if api_card.card_sets:
            for s in api_card.card_sets:
                if s.set_code == set_code:
                    set_name = s.set_name
                    break

        image_id = item.get('image_id')
        if not image_id and api_card.card_images:
            image_id = api_card.card_images[0].id

        img_url = None
        if api_card.card_images:
            for img in api_card.card_images:
                if img.id == image_id:
                    img_url = img.image_url_small
                    break
            if not img_url: img_url = api_card.card_images[0].image_url_small

        # Check for local scan path
        scan_path = None
        if item.get('scan_image_path') and os.path.exists(item['scan_image_path']):
            scan_path = item['scan_image_path']
        elif item.get('image_path') and os.path.exists(item['image_path']):
            scan_path = item['image_path']

        unique_id = f"{item.get('card_id')}_{set_code}_{rarity}_{uuid.uuid4().hex[:4]}" # Unique for UI key

        return BulkCollectionEntry(
            id=unique_id,
            api_card=api_card,
            quantity=item.get('quantity', 1),
            set_code=set_code,
            set_name=set_name,
            rarity=rarity,
            language=item.get('language', 'EN'),
            condition=item.get('condition', self.default_condition),
            first_edition=item.get('first_edition', False),
            image_url=img_url,
            image_id=image_id,
            variant_id=item.get('variant_id', ''),
            price=0.0,
            scan_image_path=scan_path
        )

    async def apply_filters(self):
        source = self.scanned_cards
        s = self.filter_state
        res = []

        txt = s.get('search_text', '').lower()

        for item in source:
            entry = self._scan_to_entry(item)
            if not entry: continue

            api_card = entry.api_card

            # Text Filter
            if txt:
                name = api_card.name.lower()
                code = entry.set_code.lower()
                desc = api_card.desc.lower()
                if not (txt in name or txt in code or txt in desc):
                    continue

            if s['filter_card_type'] and not any(t in api_card.type for t in s['filter_card_type']): continue
            if s['filter_attr'] and api_card.attribute != s['filter_attr']: continue
            if s['filter_monster_race'] and "Monster" in api_card.type and api_card.race != s['filter_monster_race']: continue
            if s['filter_st_race'] and ("Spell" in api_card.type or "Trap" in api_card.type) and api_card.race != s['filter_st_race']: continue
            if s['filter_archetype'] and api_card.archetype != s['filter_archetype']: continue
            if s['filter_monster_category'] and not any(api_card.matches_category(cat) for cat in s['filter_monster_category']): continue
            if s['filter_level'] is not None and api_card.level != int(s['filter_level']): continue

            # Ranges
            if s['filter_atk_min'] > 0 or s['filter_atk_max'] < 5000:
                    if api_card.atk is None or not (s['filter_atk_min'] <= int(api_card.atk) <= s['filter_atk_max']): continue
            if s['filter_def_min'] > 0 or s['filter_def_max'] < 5000:
                    if api_card.def_ is None or not (s['filter_def_min'] <= int(api_card.def_) <= s['filter_def_max']): continue

            # Price (Simplified for Scan: usually 0 unless we fetch)
            if s['filter_price_min'] > 0.0 or s['filter_price_max'] < 1000.0:
                 pass # Skip price filter for scans for now as we don't fetch prices live

            # Ownership (Quantity)
            qty = entry.quantity
            if s['filter_ownership_min'] > 0 or s['filter_ownership_max'] < 100:
                if not (s['filter_ownership_min'] <= qty <= s['filter_ownership_max']): continue

            # Owned Language
            if s['filter_owned_lang']:
                if entry.language != s['filter_owned_lang']: continue

            # Item Properties Filters
            if s['filter_set']:
                 target = s['filter_set'].split('|')[0].strip().lower()
                 if not (target in entry.set_code.lower() or target in entry.set_name.lower()): continue

            if s['filter_rarity'] and entry.rarity.lower() != s['filter_rarity'].lower(): continue

            if s['filter_condition']:
                 if entry.condition not in s['filter_condition']: continue

            res.append(entry)

        # Sort
        key = s['sort_by']
        desc = s['sort_desc']

        def sort_key(e: BulkCollectionEntry):
            if key == 'Name': return e.api_card.name
            if key == 'Set Code': return e.set_code
            if key == 'Rarity': return e.rarity
            if key == 'Newest': return -1 # Original order preserved via stable sort if needed, but we re-create list.
                                          # Wait, scanned_cards is strictly ordered (Newest First).
                                          # So index 0 is newest.
                                          # To sort by Newest, we just keep original order.
            if key == 'ATK': return e.api_card.atk or -1
            if key == 'DEF': return getattr(e.api_card, 'def_', -1)
            if key == 'Level': return e.api_card.level or -1
            return 0

        if key != 'Newest':
            res.sort(key=sort_key, reverse=desc)
        elif not desc:
            res.reverse() # Oldest first

        self.filtered_scanned_cards = res
        self.render_live_list.refresh()

    async def reset_filters(self):
        s = self.filter_state
        s['filter_set'] = ''
        s['filter_rarity'] = ''
        s['filter_attr'] = ''
        s['filter_card_type'] = ['Monster', 'Spell', 'Trap']
        s['filter_monster_race'] = ''
        s['filter_st_race'] = ''
        s['filter_archetype'] = ''
        s['filter_monster_category'] = []
        s['filter_level'] = None
        s['filter_atk_min'] = 0
        s['filter_atk_max'] = 5000
        s['filter_def_min'] = 0
        s['filter_def_max'] = 5000
        s['filter_price_min'] = 0.0
        s['filter_price_max'] = 1000.0
        s['filter_ownership_min'] = 0
        s['filter_ownership_max'] = 100
        s['filter_condition'] = []
        s['filter_owned_lang'] = ''
        s['search_text'] = ''

        if self.filter_pane:
            self.filter_pane.reset_ui_elements()

        await self.apply_filters()

    async def open_single_view(self, entry: BulkCollectionEntry):
        async def on_update(payload):
            try:
                # Find the original raw item
                target_idx = -1
                for i, raw in enumerate(self.scanned_cards):
                     if (raw.get('card_id') == entry.api_card.id and
                         raw.get('set_code') == entry.set_code and
                         raw.get('rarity') == entry.rarity and
                         raw.get('image_id') == entry.image_id and
                         raw.get('quantity', 1) == entry.quantity):
                         target_idx = i
                         break

                if target_idx == -1: return

                raw = self.scanned_cards[target_idx]
                old_raw = raw.copy()

                for k, v in payload.items():
                    if k in ['set_code', 'rarity', 'language', 'first_edition', 'condition', 'quantity']:
                        raw[k] = v
                    if k == 'image_id': raw['image_id'] = v
                    if k == 'variant_id': raw['variant_id'] = v

                self.push_scan_undo('UPDATE', {'index': target_idx, 'old': old_raw, 'new': raw.copy()})
                self.save_recent_scans()
                await self.apply_filters()
                ui.notify("Scan updated.", type='positive')
            except Exception as e:
                ui.notify(f"Update failed: {e}", type='warning')

        async def on_save(card, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode, **kwargs):
            # This logic mimics on_update essentially, or commits to collection.
            # The SingleCardView 'Add to Collection' button calls this with mode='ADD'.
            # The 'Update' button (custom added) calls 'on_update_scan_callback'.

            if mode == 'ADD':
                if not self.target_collection_file:
                    ui.notify("No collection selected.", type='warning')
                    return

                # Find and remove from scan list
                target_idx = -1
                for i, raw in enumerate(self.scanned_cards):
                     if (raw.get('card_id') == entry.api_card.id and
                         raw.get('set_code') == entry.set_code and
                         raw.get('rarity') == entry.rarity and
                         raw.get('quantity', 1) == entry.quantity):
                         target_idx = i
                         break

                if target_idx == -1: return # Should not happen

                col = persistence.load_collection(self.target_collection_file)

                CollectionEditor.apply_change(col, card, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode='ADD')
                persistence.save_collection(col, self.target_collection_file)

                card_data = {
                    'card_id': card.id, 'name': card.name, 'set_code': set_code, 'rarity': rarity,
                    'image_id': image_id, 'language': language, 'condition': condition,
                    'first_edition': first_edition, 'variant_id': variant_id
                }
                changelog_manager.log_change(self.target_collection_file, 'ADD', card_data, quantity)

                removed = self.scanned_cards.pop(target_idx)
                self.push_scan_undo('COMMIT_SINGLE', {'index': target_idx, 'scan_item': removed, 'col_file': self.target_collection_file})
                self.save_recent_scans()
                await self.apply_filters()
                ui.notify(f"Added {quantity}x to collection.", type='positive')

        await self.single_card_view.open_collectors(
            card=entry.api_card,
            owned_count=0,
            set_code=entry.set_code,
            rarity=entry.rarity,
            set_name=entry.set_name,
            language=entry.language,
            condition=entry.condition,
            first_edition=entry.first_edition,
            image_id=entry.image_id,
            on_update_scan_callback=on_update,
            save_callback=on_save,
            variant_id=entry.variant_id
        )

    async def on_remove_all_click(self):
        if not self.scanned_cards: return
        with ui.dialog() as d, ui.card():
             ui.label("Clear all scanned cards?").classes('text-lg font-bold')
             with ui.row().classes('justify-end'):
                 ui.button("Cancel", on_click=d.close).props('flat')
                 async def confirm():
                     d.close()
                     self.push_scan_undo('COMMIT', list(self.scanned_cards))
                     self.scanned_cards.clear()
                     self.save_recent_scans()
                     await self.apply_filters()
                     ui.notify("All scans removed.", type='positive')
                 ui.button("Clear All", on_click=confirm).props('color=negative')
        d.open()

    async def on_update_all_click(self):
        if not self.scanned_cards: return
        if not (self.update_apply_lang or self.update_apply_cond):
            ui.notify("Select at least one property (Lang, Cond) to update.", type='warning')
            return

        with ui.dialog() as d, ui.card():
             ui.label("Batch Update Scans").classes('text-lg font-bold')
             ui.label(f"Update {len(self.scanned_cards)} cards?").classes('text-sm')

             updates = []
             if self.update_apply_lang: updates.append(f"Language -> {self.default_language}")
             if self.update_apply_cond: updates.append(f"Condition -> {self.default_condition}")

             msg = "Applying: " + ", ".join(updates)
             ui.label(msg).classes('text-xs text-accent')

             with ui.row().classes('justify-end'):
                 ui.button("Cancel", on_click=d.close).props('flat')
                 async def confirm():
                     d.close()
                     snapshot = [c.copy() for c in self.scanned_cards]
                     self.push_scan_undo('COMMIT', snapshot)

                     count = 0
                     for item in self.scanned_cards:
                         if self.update_apply_cond: item['condition'] = self.default_condition
                         if self.update_apply_lang: item['language'] = self.default_language
                         count += 1

                     self.save_recent_scans()
                     await self.apply_filters()
                     ui.notify(f"Updated {count} cards.", type='positive')
                 ui.button("Update", on_click=confirm).props('color=warning')
        d.open()

    def render_top_header(self):
        with ui.row().classes('w-full p-2 bg-black border-b border-gray-800 items-center justify-between gap-4'):
             # Group 1: Hardware & Source
             with ui.row().classes('items-center gap-2'):
                 if self.collections:
                      ui.select(options=self.collections, value=self.target_collection_file, label='Target Collection',
                                on_change=lambda e: setattr(self, 'target_collection_file', e.value)).props('dense outlined options-dense').classes('w-64')

             # Group 2: Defaults & Actions
             with ui.row().classes('items-center gap-4'):
                 ui.label("Defaults:").classes('text-xs font-bold text-gray-500 uppercase')
                 with ui.row().classes('gap-2'):
                      ui.select(['EN', 'DE', 'FR', 'IT', 'PT'], value=self.default_language, label="Lang",
                                on_change=lambda e: setattr(self, 'default_language', e.value)).props('dense outlined options-dense').classes('w-20')
                      ui.select(CARD_CONDITIONS, value=self.default_condition, label="Cond",
                                on_change=lambda e: setattr(self, 'default_condition', e.value)).props('dense outlined options-dense').classes('w-32')

                 ui.separator().props('vertical')

                 ui.button('Commit', on_click=self.commit_cards).props('color=positive icon=save').classes('font-bold')

    @ui.refreshable
    def render_scan_header(self):
        with ui.row().classes('w-full p-2 bg-gray-900 border-b border-gray-800 items-center justify-between gap-2 flex-nowrap overflow-x-auto'):
            ui.label('Recent Scans').classes('text-h6 font-bold')

            with ui.row().classes('items-center gap-1 flex-nowrap'):
                ui.button('Undo', icon='undo', on_click=self.undo_scan_action).props('flat dense color=white size=sm').tooltip('Undo last action')
                ui.separator().props('vertical')

                with ui.row().classes('gap-1 items-center bg-gray-800 rounded px-1 border border-gray-700'):
                    ui.button("Update", on_click=self.on_update_all_click).props('flat dense color=warning size=sm')
                    ui.checkbox('Lang', value=self.update_apply_lang, on_change=lambda e: setattr(self, 'update_apply_lang', e.value)).props('dense size=xs').classes('text-[10px]')
                    ui.checkbox('Cond', value=self.update_apply_cond, on_change=lambda e: setattr(self, 'update_apply_cond', e.value)).props('dense size=xs').classes('text-[10px]')

                ui.button("Remove All", on_click=self.on_remove_all_click).props('flat dense color=negative size=sm')
                ui.separator().props('vertical')

                ui.input(placeholder='Search...', on_change=lambda e: [self.filter_state.update({'search_text': e.value}), asyncio.create_task(self.apply_filters())]).props('dense borderless dark debounce=300').classes('w-32 text-sm')
                ui.separator().props('vertical')

                sort_opts = ['Newest', 'Name', 'Set Code', 'Rarity', 'ATK', 'DEF', 'Level']
                async def on_sort(e):
                    self.filter_state['sort_by'] = e.value
                    await self.apply_filters()
                ui.select(sort_opts, value=self.filter_state['sort_by'], on_change=on_sort).props('dense options-dense borderless').classes('w-20 text-xs')

                async def toggle_sort():
                    self.filter_state['sort_desc'] = not self.filter_state['sort_desc']
                    await self.apply_filters()
                ui.button(on_click=toggle_sort).props('flat dense color=white size=sm').bind_icon_from(self.filter_state, 'sort_desc', lambda d: 'arrow_downward' if d else 'arrow_upward')

                ui.button(icon='filter_list', on_click=self.filter_dialog.open).props('flat dense color=white size=sm')

    @ui.refreshable
    def render_live_list(self):
        items = self.filtered_scanned_cards
        if not items and self.scanned_cards:
            ui.label("No matches for filter.").classes('text-gray-400 italic')
        elif not items:
            ui.label("No cards scanned.").classes('text-gray-400 italic')
            return

        with ui.grid(columns='repeat(auto-fill, minmax(110px, 1fr))').classes('w-full gap-2 p-2'):
            for item in items:
                img_src = None
                # Prioritize Scan Image if available
                if item.scan_image_path:
                    if item.scan_image_path.startswith('data/scans/'):
                         img_src = item.scan_image_path.replace('data/scans/', '/scans/')
                    else:
                         img_src = f"/images/{os.path.basename(item.scan_image_path)}"

                # Fallback to DB image
                if not img_src:
                     if item.image_id and image_manager.image_exists(item.image_id):
                         img_src = f"/images/{item.image_id}.jpg"
                     elif item.image_url:
                         img_src = item.image_url

                cond_short = CONDITION_ABBREVIATIONS.get(item.condition, item.condition[:2].upper())

                with ui.card().classes('p-0 cursor-pointer hover:scale-105 transition-transform border border-accent w-full aspect-[2/3] select-none') \
                        .on('click', lambda x=item: self.open_single_view(x)) \
                        .on('contextmenu.prevent', lambda x=item: self.reduce_card_qty(x)):

                    with ui.element('div').classes('relative w-full h-full'):
                         if img_src:
                             ui.image(img_src).classes('w-full h-full object-cover')
                         else:
                             ui.label("?").classes('w-full h-full flex items-center justify-center bg-gray-800 text-white')

                         lang = item.language.upper()
                         country_code = LANGUAGE_COUNTRY_MAP.get(lang)
                         if country_code:
                             ui.element('img').props(f'src="https://flagcdn.com/h24/{country_code}.png" alt="{lang}"').classes('absolute top-[1px] left-[1px] h-4 w-6 shadow-black drop-shadow-md rounded bg-black/30')
                         else:
                             ui.label(lang).classes('absolute top-[1px] left-[1px] text-[10px] font-bold shadow-black drop-shadow-md bg-black/50 rounded px-1 text-white')

                         qty = item.quantity
                         if qty > 1:
                              ui.label(f"{qty}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs shadow-md')

                         with ui.column().classes('absolute bottom-0 left-0 bg-black/80 text-white text-[9px] px-1 gap-0 w-full'):
                             ui.label(item.api_card.name).classes('text-[9px] font-bold text-white leading-none truncate w-full')

                             with ui.row().classes('w-full justify-between items-center'):
                                 with ui.row().classes('gap-1'):
                                     ui.label(cond_short).classes('font-bold text-yellow-500')
                                     if item.first_edition:
                                         ui.label('1st').classes('font-bold text-orange-400')
                                 ui.label(item.set_code).classes('font-mono')

                             ui.label(item.rarity).classes('text-[8px] text-gray-300 w-full truncate')

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
        # Match Candidates (Moved to Top)
        candidates = self.debug_report.get('match_candidates', [])

        # Header Stats
        with ui.row().classes('w-full items-center justify-between mb-2'):
            ui.label("3. OCR & Match Results").classes('text-2xl font-bold text-primary')

            # Show Detected Features prominently
            with ui.row().classes('gap-4'):
                # 1st Edition Status
                is_first_ed = self.debug_report.get('first_edition', False)
                color = 'green' if is_first_ed else 'gray'
                ui.badge(f"1st Ed: {'YES' if is_first_ed else 'NO'}", color=color).classes('text-sm')

                # Visual Rarity
                vis_rarity = self.debug_report.get('visual_rarity', 'Unknown')
                ui.badge(f"Visual: {vis_rarity}", color='blue').classes('text-sm')

                # Card Type
                card_type = self.debug_report.get('card_type')
                if card_type:
                    color = 'purple' if 'TRAP' in card_type.upper() else 'green'
                    ui.badge(f"Type: {card_type}", color=color).classes('text-sm')

        if candidates:
            with ui.card().classes('w-full bg-gray-900 border border-gray-600 p-2 mb-4'):
                ui.label("Match Candidates (Top 10)").classes('font-bold text-lg mb-2')

                # Header
                with ui.grid(columns=5).classes('w-full gap-2 border-b border-gray-600 pb-1 mb-1'):
                    ui.label("Name").classes('font-bold text-xs text-gray-400 col-span-2')
                    ui.label("Set").classes('font-bold text-xs text-gray-400')
                    ui.label("Rarity").classes('font-bold text-xs text-gray-400')
                    ui.label("Score").classes('font-bold text-xs text-gray-400 text-right')

                # Rows
                for c in candidates:
                    with ui.grid(columns=5).classes('w-full gap-2 items-center hover:bg-gray-800 p-1 rounded'):
                        ui.label(c.get('name', '')).classes('text-xs break-all leading-tight col-span-2')
                        ui.label(c.get('set_code', '')).classes('text-xs font-mono text-green-300')
                        ui.label(c.get('rarity', '')).classes('text-xs truncate text-blue-300')
                        ui.label(f"{c.get('score', 0):.1f}").classes('text-xs font-mono text-yellow-400 text-right')
        else:
            ui.label("No Match Candidates Found").classes('text-gray-500 italic mb-4')

        # 4 Collapsable Zones
        def render_zone(title, key):
            data = self.debug_report.get(key)
            # Use persistent state for expansion
            is_open = self.expansion_states.get(key, False) # Default closed to reduce clutter
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
            for log in logs:
                ui.label(log)

    @ui.refreshable
    def render_scan_queue(self):
        # Use dynamic import access
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
                             ui.button(icon='delete', color='negative',
                                       on_click=lambda idx=i: self.delete_queue_item(idx)).props('flat size=sm')

    def delete_queue_item(self, index):
        scanner_service.scanner_manager.remove_scan_request(index)
        self.render_scan_queue.refresh()

    @ui.refreshable
    def render_control_bar(self):
        with ui.column().classes('w-full gap-2 mb-2'):
            # Row 1: Camera Controls
            with ui.row().classes('w-full items-center justify-between bg-gray-900 p-2 rounded border border-gray-800'):
                 self.camera_select = ui.select(options={}, label='Camera').props('dense outlined options-dense').classes('flex-grow')

                 with ui.row().classes('gap-2'):
                     self.start_btn = ui.button('Start Camera', icon='videocam', on_click=self.start_camera).props('flat dense color=positive')
                     self.stop_btn = ui.button('Stop Camera', icon='videocam_off', on_click=self.stop_camera).props('flat dense color=negative')
                     self.stop_btn.visible = False

            # Row 2: Status & Process Controls (Reverted Design)
            # Use dynamic import access
            mgr = scanner_service.scanner_manager
            status = mgr.get_status()
            is_paused = mgr.is_paused()

            with ui.row().classes('w-full items-center justify-between bg-gray-800 p-2 rounded border border-gray-700'):
                with ui.row().classes('items-center gap-2'):
                    if status == "Processing...":
                        ui.spinner(size='sm')
                    elif is_paused:
                        ui.icon('pause_circle', color='warning').classes('text-xl')
                    else:
                        ui.icon('play_circle', color='positive').classes('text-xl')

                    label_text = status
                    if is_paused and status == "Stopped":
                         label_text = "Ready to Start"
                    elif is_paused:
                         label_text = "Paused"

                    # Transient states
                    if not is_paused and status == "Paused":
                        label_text = "Resuming..."
                    elif is_paused and status not in ["Paused", "Stopped"]:
                        label_text = "Pausing..."

                    with ui.column().classes('gap-0'):
                        ui.label(f"Status: {label_text}").classes('font-bold')
                        ui.label(f"Mgr: {getattr(mgr, 'instance_id', 'N/A')}").classes('text-[10px] text-gray-600')
                        # Access current_step safely from debug_report (it's a dict now in UI context)
                        current_step = self.debug_report.get('current_step', 'Idle')
                        if mgr.is_processing:
                            ui.label(f"{current_step}").classes('text-xs text-blue-400')

                # Controls
                if is_paused:
                     ui.button('Start Processing', icon='play_arrow', color='positive', on_click=self.toggle_pause).props('size=sm')
                else:
                     ui.button('Pause', icon='pause', color='warning', on_click=self.toggle_pause).props('size=sm')

    @ui.refreshable
    def render_status_controls(self):
        """Legacy status controls for Debug Lab"""
        # Use dynamic import access
        mgr = scanner_service.scanner_manager
        status = mgr.get_status()
        is_paused = mgr.is_paused()

        with ui.row().classes('w-full items-center justify-between bg-gray-800 p-2 rounded border border-gray-700'):
            with ui.row().classes('items-center gap-2'):
                if status == "Processing...":
                    ui.spinner(size='sm')
                elif is_paused:
                    ui.icon('pause_circle', color='warning').classes('text-xl')
                else:
                    ui.icon('play_circle', color='positive').classes('text-xl')

                label_text = status
                if is_paused and status == "Stopped":
                     label_text = "Ready to Start"
                elif is_paused:
                     label_text = "Paused"

                with ui.column().classes('gap-0'):
                    ui.label(f"Status: {label_text}").classes('font-bold')
                    current_step = self.debug_report.get('current_step', 'Idle')
                    if mgr.is_processing:
                        ui.label(f"{current_step}").classes('text-xs text-blue-400')

            if is_paused:
                 ui.button('Start Processing', icon='play_arrow', color='positive', on_click=self.toggle_pause).props('size=sm')
            else:
                 ui.button('Pause', icon='pause', color='warning', on_click=self.toggle_pause).props('size=sm')

    def toggle_pause(self):
        scanner_service.scanner_manager.toggle_pause()
        self.render_control_bar.refresh()
        self.render_status_controls.refresh()

    def render_debug_lab(self):
        with ui.grid().classes('grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 w-full'):

            # --- CARD 1: CONTROLS & INPUT ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("1. Configuration & Input").classes('text-2xl font-bold text-primary')
                self.render_status_controls()

                # Configuration Section
                ui.label("Scanner Configuration").classes('font-bold text-lg mt-2')

                # Preprocessing Toggle
                ui.label("Preprocessing Strategy:").classes('font-bold text-gray-300 text-sm')
                with ui.row():
                    ui.radio(['classic', 'classic_white_bg', 'yolo', 'yolo26'], value=self.preprocessing_mode,
                            on_change=lambda e: (setattr(self, 'preprocessing_mode', e.value), self.save_settings())).props('inline')

                # Rotation
                ui.label("Camera Rotation:").classes('font-bold text-gray-300 text-sm')
                with ui.row():
                    ui.toggle({0: '0°', 90: '90°', 180: '180°', 270: '270°'}, value=self.rotation,
                            on_change=lambda e: (setattr(self, 'rotation', e.value), self.save_settings(), ui.run_javascript(f'setRotation({e.value})'))).props('toggle-color=accent')

                # Art Match
                with ui.row().classes('items-center justify-between w-full'):
                    ui.label("Art Style Match (YOLO):").classes('font-bold text-gray-300 text-sm')
                    with ui.row().classes('items-center gap-2'):
                         ui.button('Index Images', icon='refresh', on_click=lambda: scanner_service.scanner_manager.rebuild_art_index(force=True)).props('dense color=purple').tooltip("Rebuild Art Index from data/images")
                         ui.switch(value=self.art_match_yolo,
                                  on_change=lambda e: (setattr(self, 'art_match_yolo', e.value), self.save_settings())).props('color=purple')

                # Tracks Selector (Radio)
                ui.label("Active Track:").classes('font-bold text-gray-300 text-sm')
                ui.radio(['easyocr', 'doctr'], value=self.selected_track,
                        on_change=lambda e: (setattr(self, 'selected_track', e.value), self.save_settings())).props('inline')

                # Ambiguity Threshold
                ui.label("Ambiguity Threshold:").classes('font-bold text-gray-300 text-sm')
                ui.number(value=self.ambiguity_threshold, min=0, max=100, step=1.0,
                         on_change=lambda e: (setattr(self, 'ambiguity_threshold', e.value), self.save_settings())).classes('w-full')

                # Art Match Threshold
                ui.label("Art Match Threshold:").classes('font-bold text-gray-300 text-sm')
                ui.number(value=self.art_match_threshold, min=0, max=1.0, step=0.01,
                         on_change=lambda e: (setattr(self, 'art_match_threshold', e.value), self.save_settings())).classes('w-full')

                # Save Warped Scan
                ui.switch("Save Warped Scans", value=self.save_warped_scan,
                          on_change=lambda e: (setattr(self, 'save_warped_scan', e.value), self.save_settings())).props('color=secondary').classes('w-full')

                # Save Raw Scan
                ui.switch("Save Raw Scans", value=self.save_raw_scan,
                          on_change=lambda e: (setattr(self, 'save_raw_scan', e.value), self.save_settings())).props('color=secondary').classes('w-full')

                # Overlay Duration
                ui.label("Overlay Duration (ms):").classes('font-bold text-gray-300 text-sm')
                ui.number(value=self.overlay_duration, min=0, max=5000, step=100,
                          on_change=lambda e: (setattr(self, 'overlay_duration', e.value), self.save_settings())).classes('w-full')

                # Camera Preview
                ui.label("Camera Preview").classes('font-bold text-lg mt-4')
                with ui.element('div').classes('w-full aspect-video bg-black rounded relative overflow-hidden'):
                    ui.html('<video id="debug-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)

                # Controls
                with ui.row().classes('w-full gap-2'):
                    ui.button("Capture & Analyze", on_click=self.handle_debug_capture, icon='camera_alt').classes('flex-grow bg-accent text-black font-bold')

                ui.separator().classes('bg-gray-600')
                ui.upload(label="Upload Image", on_upload=self.handle_debug_upload, auto_upload=True).props('accept=.jpg,.png color=secondary').classes('w-full')

                self.render_scan_queue()
                self.render_debug_results()

            # --- CARD 2: VISUAL ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("2. Visual Analysis").classes('text-2xl font-bold text-primary')
                self.render_debug_analysis()

            # --- CARD 3: RESULTS ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                self.render_debug_pipeline_results()

        ui.run_javascript('initDebugStream()')
        ui.run_javascript(f'setRotation({self.rotation})')

    def toggle_track(self, track, enabled):
        # Deprecated logic in favor of single selection radio
        pass

def scan_page():
    page = ScanPage()

    # Initialize event queue for this page instance
    page.event_queue = queue.Queue()

    def cleanup():
        # Unregister listener
        scanner_service.scanner_manager.unregister_listener(page.on_scanner_event)
        page.is_active = False

    app.on_disconnect(cleanup)

    # Register listener
    scanner_service.scanner_manager.register_listener(page.on_scanner_event)

    # Ensure scanner service is running (idempotent)
    # Use dynamic import access
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

        # Re-apply rotation on tab change to ensure video element has correct style
        ui.run_javascript(f'setRotation({page.rotation})')

    with ui.tabs(on_change=handle_tab_change).classes('w-full') as tabs:
        live_tab = ui.tab('Live Scan')
        debug_tab = ui.tab('Debug Lab')

    # Initialize Filter Dialog
    page.filter_dialog = ui.dialog().props('position=right')
    with page.filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
         with ui.scroll_area().classes('flex-grow w-full'):
             page.filter_pane = FilterPane(page.filter_state, page.apply_filters, page.reset_filters, show_set_selector=True)
             page.filter_pane.build()

    with ui.tab_panels(tabs, value=live_tab).classes('w-full h-full'):

        # --- TAB 1: LIVE SCAN ---
        with ui.tab_panel(live_tab).classes('p-0 h-full flex flex-col'):

            # Global Header
            page.render_top_header()

            with ui.row().classes('w-full flex-grow flex-nowrap gap-0'):

                 # --- LEFT PANEL (Camera & Controls) ---
                 with ui.column().classes('w-1/2 h-full p-4 flex flex-col gap-2 border-r border-gray-800 bg-black'):

                      # Controls & Status
                      page.render_control_bar()

                      # Camera View
                      with ui.card().classes('w-full aspect-video p-0 overflow-hidden relative bg-black border border-gray-700 shadow-lg'):
                           ui.html('<video id="scanner-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)
                           ui.html('<canvas id="overlay-canvas" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none;"></canvas>', sanitize=False)
                           ui.html('<div id="capture-overlay" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; opacity: 0; transition: opacity 0.5s; background-size: contain; background-repeat: no-repeat; background-position: center;"></div>', sanitize=False)

                      # Big Capture Button
                      ui.button('Capture & Scan', on_click=page.trigger_live_scan).props('icon=camera color=accent text-color=black size=lg').classes('w-full font-bold h-16 text-xl mt-4')

                 # --- RIGHT PANEL (Gallery) ---
                 with ui.column().classes('w-1/2 h-full flex flex-col bg-gray-900 overflow-hidden gap-0'):
                      page.render_scan_header()

                      # Ensure flex-grow is applied. min-h-0 is crucial for nested flex scrolling.
                      with ui.column().classes('w-full flex-grow min-h-0 bg-black/20 overflow-hidden relative'):
                           # Ensure scroll area takes full height of THIS flex-child
                           with ui.scroll_area().classes('w-full h-full'):
                                page.render_live_list()

        # --- TAB 2: DEBUG LAB ---
        with ui.tab_panel(debug_tab):
             page.render_debug_lab()

    ui.timer(1.0, page.init_cameras, once=True)
    ui.timer(0.1, page.load_library_data, once=True)

    # Use fast consumer loop instead of slow polling
    ui.timer(0.1, page.event_consumer)

    # Initialize from current state immediately
    page.debug_report = scanner_service.scanner_manager.get_debug_snapshot()
