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

# Import the module, not the instance, to avoid stale references on reload
from src.services.scanner import manager as scanner_service
from src.services.scanner import SCANNER_AVAILABLE
from src.core.persistence import persistence
from src.core.models import CollectionCard, CollectionVariant, CollectionEntry
from src.services.ygo_api import ygo_service
from src.ui.components.ambiguity_dialog import AmbiguityDialog
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from src.ui.viewmodels import BulkCollectionEntry
from src.core.changelog_manager import changelog_manager
from src.core import config_manager
from src.services.collection_editor import CollectionEditor
from src.core.utils import generate_variant_id

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
}
</script>
"""

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
        self.config = config_manager.load_config()

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

        # Debug Lab State (local cache of Pydantic model dump)
        self.debug_report = {}
        self.debug_loading = False
        self.latest_capture_src = None
        # self.was_processing is removed as we use event based updates now
        self.watchdog_counter = 0

        # UI State Persistence
        self.expansion_states = {}

        # Gallery / List State
        self.scan_entries: List[BulkCollectionEntry] = []
        self.scan_filtered: List[BulkCollectionEntry] = []
        self.api_card_map = {} # Cache for API cards

        self.scan_state = {
            'collection_cards': [], # Synced with self.scan_entries
            'search_text': '',
            'sort_by': 'Newest',
            'sort_desc': True,
            'collection_page': 1,
            'collection_page_size': 50,
            'collection_total_pages': 1,

            # Filters
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

            # Metadata (Populated later)
            'available_sets': [],
            'available_monster_races': [],
            'available_st_races': [],
            'available_archetypes': [],
            'available_card_types': ['Monster', 'Spell', 'Trap', 'Skill'],
        }

        self.single_card_view = SingleCardView()
        self.filter_pane = None
        self.scan_filter_dialog = None
        self.warning_dialog = None

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

        # Sync list used by logic
        self.ocr_tracks = [self.selected_track]

        config_manager.save_config(self.config)

    def _scan_to_entry(self, scan_result: Dict) -> Optional[BulkCollectionEntry]:
        raw_id = scan_result.get('card_id')
        if not raw_id:
            return None

        try:
            card_id = int(raw_id)
        except ValueError:
            logger.error(f"Invalid card_id format: {raw_id}")
            return None

        # Cache API Card
        if card_id not in self.api_card_map:
             card = ygo_service.get_card(card_id)
             if card:
                 self.api_card_map[card_id] = card

        api_card = self.api_card_map.get(card_id)
        if not api_card:
            logger.warning(f"Card ID {card_id} not found in database.")
            return None

        # Determine Image
        img_id = scan_result.get('image_id')
        if not img_id and api_card.card_images:
            img_id = api_card.card_images[0].id

        # URL logic similar to BulkAdd
        img_url = None
        if api_card.card_images:
             for img in api_card.card_images:
                 if img.id == img_id:
                     img_url = img.image_url_small
                     break

        set_name = "Unknown Set"
        if api_card.card_sets:
            for s in api_card.card_sets:
                if s.set_code == scan_result.get('set_code'):
                    set_name = s.set_name
                    break

        return BulkCollectionEntry(
            id=scan_result.get('uuid'),
            api_card=api_card,
            quantity=1,
            set_code=scan_result.get('set_code'),
            set_name=set_name,
            rarity=scan_result.get('rarity'),
            language=scan_result.get('language', 'EN'),
            condition=scan_result.get('condition', self.default_condition),
            first_edition=scan_result.get('first_edition', False),
            image_url=img_url,
            image_id=img_id,
            variant_id=scan_result.get('variant_id'),
            price=0.0
        )

    async def load_recent_scans(self):
        """Loads scans from temp file."""
        # Ensure DB loaded
        await ygo_service.load_card_database()

        temp_path = "data/scans/scans_temp.json"
        self.scanned_cards = []
        if os.path.exists(temp_path):
            try:
                with open(temp_path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.scanned_cards = data
            except Exception as e:
                logger.error(f"Failed to load recent scans: {e}")

        # Ensure UUIDs
        updated = False
        for c in self.scanned_cards:
            if 'uuid' not in c:
                c['uuid'] = str(uuid.uuid4())
                updated = True

        if updated:
            self.save_recent_scans()

        # Build View Models
        self.scan_entries = []
        for c in self.scanned_cards:
            entry = self._scan_to_entry(c)
            if entry:
                self.scan_entries.append(entry)

        self.scan_state['collection_cards'] = self.scan_entries

        # Populate Metadata for Filters (Lazy approach: based on loaded cards + global DB if needed,
        # but BulkAdd uses global. For Scan, maybe just use what we have or global?
        # BulkAdd loads global. We can rely on ygo_service for global metadata if we wanted strict filtering.
        # But FilterPane relies on `scan_state` keys.
        # Let's populate minimal metadata based on scanned cards for now or reuse bulk add logic?
        # To reuse FilterPane effectively, we need valid metadata.
        # Let's try to load minimal metadata from the scanned cards themselves for dropdowns.

        sets = set()
        m_races = set()
        st_races = set()
        archetypes = set()

        for entry in self.scan_entries:
            sets.add(f"{entry.set_name} | {entry.set_code.split('-')[0]}")
            c = entry.api_card
            if c:
                if c.archetype: archetypes.add(c.archetype)
                if "Monster" in c.type and c.race: m_races.add(c.race)
                elif ("Spell" in c.type or "Trap" in c.type) and c.race: st_races.add(c.race)

        self.scan_state['available_sets'][:] = sorted(list(sets))
        self.scan_state['available_monster_races'][:] = sorted(list(m_races))
        self.scan_state['available_st_races'][:] = sorted(list(st_races))
        self.scan_state['available_archetypes'][:] = sorted(list(archetypes))

        await self.apply_scan_filters()

    def save_recent_scans(self):
        """Saves current scanned cards to temp file."""
        temp_path = "data/scans/scans_temp.json"
        try:
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            with open(temp_path, 'w') as f:
                json.dump(self.scanned_cards, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save recent scans: {e}")

    async def apply_scan_filters(self):
        source = self.scan_state['collection_cards']
        res = list(source)
        s = self.scan_state

        txt = s['search_text'].lower()
        if txt:
            def matches(e: BulkCollectionEntry):
                return (txt in e.api_card.name.lower() or
                        txt in e.set_code.lower() or
                        txt in e.api_card.desc.lower())
            res = [e for e in res if matches(e)]

        if s['filter_card_type']: res = [e for e in res if any(t in e.api_card.type for t in s['filter_card_type'])]
        if s['filter_attr']: res = [e for e in res if e.api_card.attribute == s['filter_attr']]
        if s['filter_monster_race']: res = [e for e in res if "Monster" in e.api_card.type and e.api_card.race == s['filter_monster_race']]
        if s['filter_st_race']: res = [e for e in res if ("Spell" in e.api_card.type or "Trap" in e.api_card.type) and e.api_card.race == s['filter_st_race']]
        if s['filter_archetype']: res = [e for e in res if e.api_card.archetype == s['filter_archetype']]
        if s['filter_set']:
             target = s['filter_set'].split('|')[0].strip().lower()
             res = [e for e in res if target in e.set_name.lower() or target in e.set_code.lower()]
        if s['filter_rarity']:
             target = s['filter_rarity'].lower()
             res = [e for e in res if e.rarity.lower() == target]
        if s['filter_monster_category']:
             cats = s['filter_monster_category']
             res = [e for e in res if any(e.api_card.matches_category(cat) for cat in cats)]
        if s['filter_owned_lang']:
             res = [e for e in res if e.language == s['filter_owned_lang']]
        if s['filter_condition']:
             res = [e for e in res if e.condition in s['filter_condition']]

        key = s['sort_by']
        reverse = s['sort_desc']
        if key == 'Name': res.sort(key=lambda x: x.api_card.name, reverse=reverse)
        elif key == 'ATK': res.sort(key=lambda x: (x.api_card.atk or -1), reverse=reverse)
        elif key == 'DEF': res.sort(key=lambda x: (getattr(x.api_card, 'def_', None) or -1), reverse=reverse)
        elif key == 'Level': res.sort(key=lambda x: (x.api_card.level or -1), reverse=reverse)
        elif key == 'Set Code': res.sort(key=lambda x: x.set_code, reverse=reverse)
        elif key == 'Quantity': res.sort(key=lambda x: x.quantity, reverse=reverse)
        elif key == 'Newest':
             # Default order (source) is Newest First.
             # If reverse is False (Ascending), we want Oldest First.
             if not reverse:
                 res.reverse()

        self.scan_filtered = res
        self.scan_state['collection_page'] = 1
        self.update_scan_pagination()
        if hasattr(self, 'render_live_list'):
            self.render_live_list.refresh()

    def update_scan_pagination(self):
        count = len(self.scan_filtered)
        self.scan_state['collection_total_pages'] = max(1, (count + self.scan_state['collection_page_size'] - 1) // self.scan_state['collection_page_size'])

    async def undo_last_scan_action(self):
        last_change = changelog_manager.undo_last_change('scan_temp')
        if not last_change:
            ui.notify("Nothing to undo.", type='warning')
            return

        action = last_change.get('action')
        data = last_change.get('card_data')

        if action == 'ADD':
            uuid_target = data.get('uuid')
            self.scanned_cards = [c for c in self.scanned_cards if c.get('uuid') != uuid_target]

        elif action == 'REMOVE':
             # data should be the full object for REMOVE
             self.scanned_cards.insert(0, data)

        elif action == 'UPDATE':
             uuid_target = data.get('uuid')
             old_data = last_change.get('old_data')
             if old_data:
                 for c in self.scanned_cards:
                     if c.get('uuid') == uuid_target:
                         c.update(old_data)
                         break

        self.save_recent_scans()
        await self.load_recent_scans()
        ui.notify(f"Undid {action}", type='positive')
        if hasattr(self, 'render_scan_header'):
            self.render_scan_header.refresh()

    async def update_scan_entry(self, entry_id: str, updates: Dict[str, Any]):
         # Find matching scan result
         target = None
         for c in self.scanned_cards:
             if c.get('uuid') == entry_id:
                 target = c
                 break

         if target:
             old_data = {k: target.get(k) for k in updates.keys() if k in target}

             # Log UPDATE
             changelog_manager._write_entry('scan_temp', {
                "action": "UPDATE",
                "quantity": 1,
                "card_data": {'uuid': target.get('uuid')},
                "old_data": old_data,
                "type": "single"
             })

             target.update(updates)
             self.save_recent_scans()
             await self.load_recent_scans()
             # ui.notify("Updated entry", type='positive')

    async def remove_scan_entry(self, entry: BulkCollectionEntry):
        target = None
        for i, c in enumerate(self.scanned_cards):
            if c.get('uuid') == entry.id:
                target = c
                break

        if target:
            # Log REMOVE
            # Store full target object to allow restore
            changelog_manager.log_change('scan_temp', 'REMOVE', target, 1)

            self.scanned_cards.remove(target)
            self.save_recent_scans()
            await self.load_recent_scans()
            ui.notify("Removed entry", type='info')
            if hasattr(self, 'render_scan_header'):
                self.render_scan_header.refresh()

    async def reset_scan_filters(self):
        s = self.scan_state
        s['search_text'] = ''
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

        if self.filter_pane:
            self.filter_pane.reset_ui_elements()

        await self.apply_scan_filters()

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

    async def on_card_confirmed(self, result_dict: Dict[str, Any]):
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

        # Ensure UUID
        if 'uuid' not in result_dict:
            result_dict['uuid'] = str(uuid.uuid4())

        self.scanned_cards.insert(0, result_dict)
        self.save_recent_scans()

        # Log ADD
        changelog_manager.log_change('scan_temp', 'ADD', result_dict, 1)

        await self.load_recent_scans()
        ui.notify(f"Added: {result_dict.get('name')}", type='positive')
        if hasattr(self, 'render_scan_header'):
            self.render_scan_header.refresh()

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
                    await self.on_card_confirmed(res)

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

    def remove_card(self, index):
        if 0 <= index < len(self.scanned_cards):
            self.scanned_cards.pop(index)
            self.save_recent_scans()
            self.render_live_list.refresh()

    async def commit_cards(self):
        if not self.target_collection_file:
            ui.notify("Please select a target collection.", type='warning')
            return

        if not self.scanned_cards:
            ui.notify("No cards to add.", type='warning')
            return

        try:
            collection = persistence.load_collection(self.target_collection_file)

            count = 0
            for item in self.scanned_cards:
                # item is dict from ScanResult.model_dump()
                if not item.get('card_id'):
                    continue

                target_card = next((c for c in collection.cards if c.card_id == item['card_id']), None)
                if not target_card:
                    target_card = CollectionCard(card_id=item['card_id'], name=item['name'])
                    collection.cards.append(target_card)

                target_variant = next((v for v in target_card.variants
                                       if v.set_code == item['set_code'] and v.rarity == item['rarity']), None)

                if not target_variant:
                    api_card = ygo_service.get_card(item['card_id'])
                    variant_id = str(item['card_id'])
                    image_id = None

                    # If we have variant info from matching
                    if item.get('variant_id'):
                         variant_id = item['variant_id']
                         image_id = item.get('image_id')
                    elif api_card:
                         # Fallback search
                        for s in api_card.card_sets:
                            if s.set_code == item['set_code'] and s.set_rarity == item['rarity']:
                                variant_id = s.variant_id
                                image_id = s.image_id
                                break

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
                    quantity=1
                )
                target_variant.entries.append(entry)
                count += 1

            persistence.save_collection(collection, self.target_collection_file)

            ui.notify(f"Added {count} cards to {collection.name}", type='positive')
            self.scanned_cards.clear()
            self.save_recent_scans() # Will save empty list

            # Clear temp file as requested
            try:
                os.remove("data/scans/scans_temp.json")
            except:
                pass

            self.render_live_list.refresh()

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

            # Show overlay
            if hasattr(self, 'capture_overlay'):
                self.capture_overlay.source = data_url
                # Apply rotation
                transform = f'rotate({self.rotation}deg)'
                self.capture_overlay.style(f'display: block; transform: {transform};')

                def hide_overlay():
                    self.capture_overlay.style('display: none;')

                ui.timer(1.0, hide_overlay, once=True)

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

    def _setup_card_tooltip(self, card, specific_image_id=None):
        if not card: return
        target_img = card.card_images[0] if card.card_images else None
        if specific_image_id and card.card_images:
            for img in card.card_images:
                if img.id == specific_image_id:
                    target_img = img
                    break
        if not target_img: return

        img_id = target_img.id
        high_res_url = target_img.image_url
        low_res_url = target_img.image_url_small
        is_local = image_manager.image_exists(img_id, high_res=True)
        initial_src = f"/images/{img_id}_high.jpg" if is_local else (high_res_url or low_res_url)

        with ui.tooltip().classes('bg-transparent shadow-none border-none p-0 overflow-visible z-[9999] max-w-none').props('style="max-width: none" delay=5000') as tooltip:
            if initial_src:
                ui.image(initial_src).classes('w-auto h-[65vh] min-w-[1000px] object-contain rounded-lg shadow-2xl').props('fit=contain')
            if not is_local and high_res_url:
                async def ensure_high():
                    if not image_manager.image_exists(img_id, high_res=True):
                         await image_manager.ensure_image(img_id, high_res_url, high_res=True)
                tooltip.on('show', ensure_high)

    @ui.refreshable
    def render_scan_header(self):
        with ui.row().classes('w-full p-2 bg-gray-900 border-b border-gray-800 items-center justify-between gap-2 flex-nowrap overflow-x-auto'):
            ui.label('Recent Scans').classes('text-h6 font-bold')
            with ui.row().classes('items-center gap-1 flex-nowrap'):
                # Update & Remove
                # Note: Bulk Update logic could be added here similar to BulkAddPage

                has_history = changelog_manager.get_last_change('scan_temp') is not None
                btn = ui.button(icon='undo', on_click=self.undo_last_scan_action).props('flat dense color=white size=sm')
                if not has_history:
                    btn.disable()
                    btn.classes('opacity-50')
                else:
                    with btn: ui.tooltip('Undo last action')

                ui.button("Remove All", on_click=self.on_remove_all_click).props('flat dense color=negative size=sm')

                ui.separator().props('vertical')

                ui.input(placeholder='Search...',
                         on_change=lambda e: self.apply_scan_filters()) \
                    .bind_value(self.scan_state, 'search_text') \
                    .props('dense borderless dark debounce=300') \
                    .classes('w-52 text-sm')

                ui.separator().props('vertical')

                # Pagination
                async def change_page(delta):
                     new_p = max(1, min(self.scan_state['collection_total_pages'], self.scan_state['collection_page'] + delta))
                     if new_p != self.scan_state['collection_page']:
                         self.scan_state['collection_page'] = new_p
                         self.render_live_list.refresh()

                ui.button(icon='chevron_left', on_click=lambda: change_page(-1)).props('flat dense color=white size=sm')
                ui.label().bind_text_from(self.scan_state, 'collection_page', lambda p: f"{p}/{self.scan_state['collection_total_pages']}").classes('text-xs font-mono')
                ui.button(icon='chevron_right', on_click=lambda: change_page(1)).props('flat dense color=white size=sm')

                ui.separator().props('vertical')

                # Sort
                sort_opts = ['Name', 'ATK', 'DEF', 'Level', 'Set Code', 'Newest']
                async def on_sort(e):
                    self.scan_state['sort_by'] = e.value
                    await self.apply_scan_filters()
                ui.select(sort_opts, value=self.scan_state['sort_by'], on_change=on_sort).props('dense options-dense borderless').classes('w-20 text-xs')

                async def toggle_sort():
                    self.scan_state['sort_desc'] = not self.scan_state['sort_desc']
                    await self.apply_scan_filters()
                ui.button(on_click=toggle_sort).props('flat dense color=white size=sm').bind_icon_from(self.scan_state, 'sort_desc', lambda d: 'arrow_downward' if d else 'arrow_upward')

                ui.button(icon='filter_list', on_click=self.open_filter_dialog).props('flat dense color=white size=sm')

    def open_filter_dialog(self):
        if not self.scan_filter_dialog:
             self.scan_filter_dialog = ui.dialog().props('position=right')
             with self.scan_filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
                 with ui.scroll_area().classes('flex-grow w-full'):
                     self.filter_pane = FilterPane(self.scan_state, self.apply_scan_filters, self.reset_scan_filters)
                     self.filter_pane.build()
        self.scan_filter_dialog.open()

    async def open_scan_single_view(self, entry: BulkCollectionEntry):
        async def on_save(card, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode):
             updates = {
                 'set_code': set_code,
                 'rarity': rarity,
                 'language': language,
                 'condition': condition,
                 'first_edition': first_edition,
                 'image_id': image_id,
                 'variant_id': variant_id
             }
             await self.update_scan_entry(entry.id, updates)
             ui.notify('Scan updated.', type='positive')

        await self.single_card_view.open_collectors(
            card=entry.api_card,
            owned_count=entry.quantity,
            set_code=entry.set_code,
            rarity=entry.rarity,
            set_name=entry.set_name,
            language=entry.language,
            condition=entry.condition,
            first_edition=entry.first_edition,
            image_url=entry.image_url,
            image_id=entry.image_id,
            set_price=entry.price,
            current_collection=None,
            save_callback=on_save,
            variant_id=entry.variant_id,
            hide_header_stats=False,
            primary_button_label='UPDATE',
            primary_button_mode='UPDATE'
        )

    async def on_remove_all_click(self):
        changes = []
        for c in self.scanned_cards:
            changes.append({
                'action': 'REMOVE',
                'quantity': 1,
                'card_data': c
            })

        changelog_manager.log_batch_change('scan_temp', 'Remove All Scans', changes)

        self.scanned_cards.clear()
        self.save_recent_scans()
        await self.load_recent_scans()
        ui.notify("Removed all scans", type='positive')
        if hasattr(self, 'render_scan_header'):
            self.render_scan_header.refresh()

    @ui.refreshable
    def render_live_list(self):
        start = (self.scan_state['collection_page'] - 1) * self.scan_state['collection_page_size']
        end = min(start + self.scan_state['collection_page_size'], len(self.scan_filtered))
        items = self.scan_filtered[start:end]

        if not items:
            ui.label('No scans found.').classes('text-gray-500 italic w-full text-center mt-10')
            return

        with ui.grid(columns='repeat(auto-fill, minmax(110px, 1fr))').classes('w-full gap-2 p-2').props('id="scan-list"'):
            for item in items:
                img_src = item.image_url or (f"/images/{item.image_id}.jpg" if image_manager.image_exists(item.image_id) else None)

                with ui.card().classes('p-0 cursor-pointer hover:scale-105 transition-transform border border-accent w-full aspect-[2/3] select-none') \
                        .on('click', lambda i=item: self.open_scan_single_view(i)) \
                        .on('contextmenu.prevent', lambda i=item: self.remove_scan_entry(i)):

                    with ui.element('div').classes('relative w-full h-full'):
                         if img_src:
                             ui.image(img_src).classes('w-full h-full object-cover')

                         ui.label(item.language).classes('absolute top-[1px] left-[1px] text-xs font-bold shadow-black drop-shadow-md bg-black/30 rounded px-1')

                         with ui.column().classes('absolute bottom-0 left-0 bg-black/80 text-white text-[9px] px-1 gap-0 w-full'):
                             ui.label(item.api_card.name).classes('text-[9px] font-bold text-white leading-none truncate w-full')
                             with ui.row().classes('w-full justify-between items-center'):
                                 ui.label(item.set_code).classes('font-mono')
                                 if item.first_edition:
                                     ui.label('1st').classes('font-bold text-orange-400')
                             ui.label(item.rarity).classes('text-[8px] text-gray-300 w-full truncate')

                    self._setup_card_tooltip(item.api_card, specific_image_id=item.image_id)

        # Initialize sortable for visual effect (or reordering if we implement it)
        ui.run_javascript('if (window.initSortable) initSortable("scan-list", "scan_group", false, false)')

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
    def render_status_controls(self):
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

    def toggle_pause(self):
        scanner_service.scanner_manager.toggle_pause()
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

    ui.add_head_html('<script src="https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js"></script>')
    ui.add_head_html('<style>.sortable-ghost-custom { opacity: 0.5; }</style>')
    ui.add_body_html('''
        <script>
        window.initSortable = function(elementId, groupName, pullMode, putMode) {
            var el = document.getElementById(elementId);
            if (!el) return;
            if (el._sortable) el._sortable.destroy();

            el._sortable = new Sortable(el, {
                group: { name: groupName, pull: pullMode, put: putMode },
                animation: 150,
                sort: true,
                ghostClass: 'sortable-ghost-custom',
                forceFallback: true,
                fallbackTolerance: 3
            });
        }
        </script>
    ''')

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

    with ui.tab_panels(tabs, value=live_tab).classes('w-full h-full'):

        # --- TAB 1: LIVE SCAN ---
        with ui.tab_panel(live_tab):
            # Top Controls Row
            with ui.row().classes('w-full gap-4 items-center mb-4'):
                if page.collections:
                    ui.select(options=page.collections, value=page.target_collection_file, label='Collection',
                              on_change=lambda e: setattr(page, 'target_collection_file', e.value)).classes('w-48')

                page.camera_select = ui.select(options={}, label='Camera').classes('w-48')
                page.start_btn = ui.button('Start', on_click=page.start_camera).props('icon=videocam')
                page.stop_btn = ui.button('Stop', on_click=page.stop_camera).props('icon=videocam_off color=negative')
                page.stop_btn.visible = False

                ui.space()

                ui.select(options=["Mint", "Near Mint", "Excellent", "Good", "Light Played", "Played", "Poor", "Damaged"],
                          value=page.default_condition, label="Default Condition",
                          on_change=lambda e: setattr(page, 'default_condition', e.value)).classes('w-32')

                ui.button('Add All', on_click=page.commit_cards).props('color=primary icon=save')

            # Main Content
            with ui.row().classes('w-full h-[calc(100vh-250px)] gap-4'):
                # Left Column: Status, Camera, Capture
                with ui.column().classes('flex-1 h-full gap-2'):
                    # Status Section (Above Camera)
                    page.render_status_controls()

                    # Camera View
                    with ui.card().classes('w-full flex-grow p-0 overflow-hidden relative bg-black'):
                        ui.html('<video id="scanner-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)
                        ui.html('<canvas id="overlay-canvas" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none;"></canvas>', sanitize=False)
                        page.capture_overlay = ui.image().classes('absolute top-0 left-0 w-full h-full object-contain pointer-events-none z-10').style('display: none;')

                    # Capture Button (Below Camera)
                    ui.button('Capture & Scan', on_click=page.trigger_live_scan).classes('w-full font-bold text-lg').props('icon=camera color=accent text-color=black')

                # Right Column: Gallery List
                with ui.column().classes('flex-1 h-full bg-dark border border-gray-800 rounded flex flex-col overflow-hidden'):
                    page.render_scan_header()
                    with ui.column().classes('w-full flex-grow relative bg-black/20 overflow-hidden'):
                        with ui.scroll_area().classes('w-full h-full'):
                            page.render_live_list()

        # --- TAB 2: DEBUG LAB ---
        with ui.tab_panel(debug_tab):
             page.render_debug_lab()

    ui.timer(1.0, page.init_cameras, once=True)
    ui.timer(0.1, page.load_recent_scans, once=True)

    # Use fast consumer loop instead of slow polling
    ui.timer(0.1, page.event_consumer)

    # Initialize from current state immediately
    page.debug_report = scanner_service.scanner_manager.get_debug_snapshot()
