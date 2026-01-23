from nicegui import ui, app, run, events
import logging
import os
import asyncio
import time
import uuid
import base64
import queue
from typing import List, Dict, Any, Optional
from fastapi import UploadFile

# Import the module, not the instance, to avoid stale references on reload
from src.services.scanner import manager as scanner_service
from src.services.scanner import SCANNER_AVAILABLE
from src.core.persistence import persistence
from src.core.models import CollectionCard, CollectionVariant, CollectionEntry
from src.services.ygo_api import ygo_service
from src.core.utils import transform_set_code, REGION_TO_LANGUAGE_MAP

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

        # Config
        self.ocr_tracks = ['doctr'] # Default to DocTR
        self.preprocessing_mode = 'classic' # 'classic', 'yolo', or 'yolo26'
        self.art_match_yolo = False
        self.default_condition = "Near Mint"

        # Debug Lab State (local cache of Pydantic model dump)
        self.debug_report = {}
        self.debug_loading = False
        self.latest_capture_src = None
        # self.was_processing is removed as we use event based updates now
        self.watchdog_counter = 0

        # UI State Persistence
        self.expansion_states = {}

        # Dialog reference
        self.ambiguity_dialog_ref = None

        # Last Found Card (for Debug Lab display)
        self.last_scan_result = None

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

    async def event_consumer(self):
        """Consumes events from the local queue and updates UI."""
        try:
            # 1. Process Queued Events (Fast path)
            while not self.event_queue.empty():
                try:
                    event = self.event_queue.get_nowait()

                    # Apply snapshot
                    if event.snapshot:
                        self.debug_report = event.snapshot.model_dump()

                    # Refresh logic based on event type
                    if event.type in ['status_update', 'scan_queued', 'scan_started', 'step_complete', 'scan_finished']:
                        self.refresh_debug_ui()

                    if event.type == 'scan_ambiguous':
                        # Open Ambiguity Dialog
                        self.open_ambiguity_dialog(event.data.get('result'))
                        # Also refresh debug to show logs
                        self.refresh_debug_ui()

                    if event.type == 'error':
                        ui.notify(event.data.get('message', 'Error'), type='negative')

                    if event.type == 'result_ready':
                         # Handled by result queue poll below, but good for trigger
                         pass

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
                # If ambiguous, it might have been emitted via event.
                # Check if it has ambiguity_data to avoid double handling if we handled event.
                # Actually, `scan_ambiguous` event sends data payload. `result_queue` sends ScanResult.
                # If it's ambiguous, the manager might NOT put it in result_queue or might flag it.
                # Current manager puts it in result_queue ALWAYS.

                # Check if we should auto-add or wait.
                # If ambiguous, we rely on the event handling.
                # But if we missed the event?

                if res.get('ambiguity_data'):
                     # Do not auto-add. Event handler should have caught it.
                     pass
                else:
                    self.scanned_cards.insert(0, res)
                    self.last_scan_result = res
                    self.render_live_list.refresh()
                    ui.notify(f"Scanned: {res.get('name')}", type='positive')
                    self.refresh_debug_ui() # Ensure final result is shown

        except Exception as e:
            logger.error(f"Error in event_consumer: {e}")

    def open_ambiguity_dialog(self, result_data):
        if not result_data: return
        ambiguity = result_data.get('ambiguity_data', {})
        candidates = ambiguity.get('candidates', [])

        # Default Selections
        selected_cand_idx = 0
        current_candidate = candidates[0] if candidates else {}

        # State containers
        state = {
            "card_idx": 0,
            "set_code": result_data.get('set_code'),
            "rarity": result_data.get('rarity'),
            "language": result_data.get('language', 'EN'),
            "first_edition": result_data.get('first_edition', False),
            "condition": self.default_condition
        }

        # Helper to get current card sets
        def get_current_sets():
            if not candidates: return []
            return candidates[state["card_idx"]]['sets']

        def update_set_options():
            sets = get_current_sets()
            # Filter based on language?
            # User requirement: "If DE is selected ... only show setcodes with DE country codes"
            # But we must allow "compatible" codes (e.g. legacy G for DE).

            opts = []
            target_lang = state['language']

            for s in sets:
                # Check region
                # Use util function to extract region
                # But here we have full set code.
                code = s['set_code']

                # Check compatibility
                # We can't strictly hide incompatible ones if that prevents the user from correcting a wrong language selection.
                # But we should prioritize/filter.
                # Let's show compatible ones first, or strictly if requested.
                # Prompt: "Constrain the Dropdowns to only possible options"

                # Implementation: Check if transformed code matches?
                # Or check region code.

                # Let's filter:
                # 1. Neutral (no region) -> Always show?
                # 2. Region matches Language (e.g. DE matches DE)
                # 3. Legacy Region matches Language (e.g. G matches DE)

                # Simple check:
                # Transform to target language. If the result equals the code, it's a perfect match for that language.
                transformed = transform_set_code(code, target_lang)
                if transformed == code:
                     opts.append(code)
                else:
                    # Also include if it has NO region?
                     if '-' in code and code.split('-')[1][0].isdigit():
                         opts.append(code)

            # If filtered list is empty, show all (fallback)
            if not opts:
                opts = [s['set_code'] for s in sets]

            # Deduplicate
            return sorted(list(set(opts)))

        def update_rarity_options():
            sets = get_current_sets()
            current_set_code = state['set_code']

            rarities = []
            for s in sets:
                if s['set_code'] == current_set_code:
                    rarities.append(s['set_rarity'])

            if not rarities:
                # Fallback: all rarities for this card
                 rarities = [s['set_rarity'] for s in sets]

            return sorted(list(set(rarities)))

        # Dialog
        with ui.dialog() as dialog, ui.card().classes('w-[600px]'):
            ui.label("Resolve Ambiguity").classes('text-xl font-bold text-warning')
            ui.label(ambiguity.get('reason', 'Unknown ambiguity')).classes('text-sm text-gray-400 mb-2')

            with ui.row().classes('w-full gap-4'):
                # Left: Image Preview
                with ui.column().classes('w-1/3'):
                    # Preview should update based on set selection
                    img = ui.image(result_data.get('image_path') or '/images/placeholder.png').classes('w-full rounded')

                    def update_image():
                        # Find image for current set/rarity
                        sets = get_current_sets()
                        found = False
                        for s in sets:
                            if s['set_code'] == state['set_code'] and s['set_rarity'] == state['rarity']:
                                # We need image path. API Set has image_id.
                                # Construct path: /images/{id}.jpg
                                if s.get('image_id'):
                                    img.source = f"/images/{s['image_id']}.jpg"
                                    found = True
                                break
                        if not found and candidates:
                             # Fallback to card default
                             cid = candidates[state['card_idx']]['card_id']
                             img.source = f"/images/{cid}.jpg"

                # Right: Controls
                with ui.column().classes('w-2/3'):
                    # 1. Card Selection
                    card_opts = {i: f"{c['name']} (Score: {int(c['score'])})" for i, c in enumerate(candidates)}
                    c_select = ui.select(card_opts, value=state['card_idx'], label="Card Identity").classes('w-full')

                    # 2. Language
                    l_select = ui.select(['EN', 'DE', 'FR', 'IT', 'PT', 'ES', 'JP', 'KR'], value=state['language'], label="Language").classes('w-full')

                    # 3. Set Code
                    s_select = ui.select(update_set_options(), value=state['set_code'], label="Set Code").classes('w-full')

                    # 4. Rarity
                    r_select = ui.select(update_rarity_options(), value=state['rarity'], label="Rarity").classes('w-full')

                    # 5. Condition & 1st Ed
                    with ui.row().classes('w-full'):
                        ui.select(['Mint', 'Near Mint', 'Excellent', 'Good', 'Light Played', 'Played', 'Poor'],
                                  value=state['condition'], label="Condition").classes('w-1/2').bind_value(state, 'condition')
                        ui.checkbox("1st Edition").bind_value(state, 'first_edition').classes('mt-4')

                    # Event Handlers
                    def on_card_change(e):
                        state['card_idx'] = e.value
                        s_select.options = update_set_options()
                        s_select.value = s_select.options[0] if s_select.options else ""
                        update_image()

                    def on_lang_change(e):
                        state['language'] = e.value
                        s_select.options = update_set_options()
                        # Try to keep current value if valid, else pick first
                        if s_select.value not in s_select.options and s_select.options:
                             s_select.value = s_select.options[0]
                        update_image()

                    def on_set_change(e):
                        state['set_code'] = e.value
                        r_select.options = update_rarity_options()
                        if r_select.value not in r_select.options and r_select.options:
                             r_select.value = r_select.options[0]
                        update_image()

                    def on_rarity_change(e):
                        state['rarity'] = e.value
                        update_image()

                    c_select.on_value_change(on_card_change)
                    l_select.on_value_change(on_lang_change)
                    s_select.on_value_change(on_set_change)
                    r_select.on_value_change(on_rarity_change)

            with ui.row().classes('w-full justify-end mt-4'):
                ui.button("Abort", on_click=dialog.close).props('flat color=negative')

                def confirm():
                    # Construct final result
                    sel_card = candidates[state['card_idx']]

                    final_res = result_data.copy()
                    final_res['name'] = sel_card['name']
                    final_res['card_id'] = sel_card['card_id']
                    final_res['set_code'] = state['set_code']
                    final_res['rarity'] = state['rarity']
                    final_res['language'] = state['language']
                    final_res['first_edition'] = state['first_edition']
                    final_res.pop('ambiguity_data', None) # Clear ambiguity

                    # Add to scanned list
                    self.scanned_cards.insert(0, final_res)
                    self.render_live_list.refresh()
                    ui.notify(f"Resolved: {sel_card['name']}", type='positive')
                    self.refresh_debug_ui()
                    dialog.close()

                ui.button("Confirm", on_click=confirm).props('color=primary')

        dialog.open()

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

                    if api_card:
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

                # Use default condition from UI
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
            self.render_live_list.refresh()

        except Exception as e:
            logger.error(f"Error saving collection: {e}")
            ui.notify(f"Error saving collection: {e}", type='negative')

    async def trigger_live_scan(self):
        """Triggers a scan from the Live Tab using current settings."""
        try:
            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not active or ready", type='warning')
                return

            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            options = {
                "tracks": self.ocr_tracks,
                "preprocessing": self.preprocessing_mode,
                "art_match_yolo": self.art_match_yolo
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
        self.render_found_card.refresh()

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
                "tracks": self.ocr_tracks,
                "preprocessing": self.preprocessing_mode,
                "art_match_yolo": self.art_match_yolo
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
            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not active or ready", type='warning')
                return

            self.latest_capture_src = data_url
            # We want to show the capture immediately?
            # Yes, locally.
            self.refresh_debug_ui()

            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            options = {
                "tracks": self.ocr_tracks,
                "preprocessing": self.preprocessing_mode,
                "art_match_yolo": self.art_match_yolo
            }
            fname = f"capture_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
            # Use dynamic import access
            scanner_service.scanner_manager.submit_scan(content, options, label="Camera Capture", filename=fname)
            ui.notify("Capture queued", type='positive')

        except Exception as err:
            ui.notify(f"Capture failed: {err}", type='negative')

    @ui.refreshable
    def render_live_list(self):
        if not self.scanned_cards:
            ui.label("No cards scanned.").classes('text-gray-400 italic')
            return

        for i, card in enumerate(self.scanned_cards):
            with ui.card().classes('w-full mb-2 p-2 flex flex-row items-center gap-4'):
                if card.get('image_path'):
                    ui.image(f"/images/{os.path.basename(card['image_path'])}").classes('w-12 h-16 object-contain')
                with ui.column().classes('flex-grow'):
                    ui.label(card.get('name', 'Unknown')).classes('font-bold')
                    with ui.row().classes('gap-2 text-xs text-gray-500'):
                        ui.label(f"{card.get('set_code')}")
                        ui.label(f"• {card.get('rarity')}")
                        if card.get('first_edition'):
                            ui.badge('1st', color='orange').props('size=xs')

                ui.button(icon='delete', color='negative', flat=True,
                          on_click=lambda idx=i: self.remove_card(idx))

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

        # Analysis Stats
        with ui.column().classes('gap-1'):
            vis_rarity = self.debug_report.get('visual_rarity', 'Unknown')
            first_ed = self.debug_report.get('first_edition', False)
            ui.label(f"Visual Rarity: {vis_rarity}").classes('text-sm')
            ui.label(f"1st Edition (Visual/OCR): {first_ed}").classes('text-sm')

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
        # 4 Collapsable Zones

        def render_zone(title, key):
            data = self.debug_report.get(key)
            # Use persistent state for expansion
            is_open = self.expansion_states.get(key, True)
            with ui.expansion(title, icon='visibility', value=is_open, on_value_change=lambda e: self.expansion_states.__setitem__(key, e.value)).classes('w-full bg-gray-800 border border-gray-600 mb-2'):
                if data:
                    with ui.column().classes('p-2 w-full'):
                        ui.label(f"Set ID: {data.get('set_id', 'N/A')}").classes('font-bold text-green-400')
                        if data.get('card_name'):
                             ui.label(f"Name: {data.get('card_name')}").classes('font-bold text-blue-400')

                        # New fields
                        with ui.row().classes('gap-4'):
                            if data.get('atk'): ui.label(f"ATK: {data.get('atk')}")
                            if data.get('def_val'): ui.label(f"DEF: {data.get('def_val')}")

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

            # Add Default Condition Dropdown here for accessibility in Debug Lab as well?
            # Or just Live Scan. The request said "Live Scan Header".
            # I'll add it here too if useful, but maybe space is tight.
            # I'll stick to Live Scan header (Tab 1) for the main dropdown.

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

                # Preprocessing Toggle
                ui.label("Preprocessing Strategy:").classes('font-bold text-gray-300')
                with ui.row():
                    ui.radio(['classic', 'classic_white_bg', 'yolo', 'yolo26'], value=self.preprocessing_mode, on_change=lambda e: setattr(self, 'preprocessing_mode', e.value)).props('inline')

                # Art Match
                with ui.row().classes('items-center justify-between w-full'):
                    ui.label("Art Style Match (YOLO):").classes('font-bold text-gray-300')
                    with ui.row().classes('items-center gap-2'):
                         ui.button('Index Images', icon='refresh', on_click=lambda: scanner_service.scanner_manager.rebuild_art_index(force=True)).props('dense color=purple').tooltip("Rebuild Art Index from data/images")
                         ui.switch(value=self.art_match_yolo, on_change=lambda e: setattr(self, 'art_match_yolo', e.value)).props('color=purple')

                # Tracks Selector
                ui.label("Active Tracks:").classes('font-bold text-gray-300')
                # Checkboxes
                with ui.row().classes('flex-wrap'):
                    ui.checkbox('EasyOCR', value='easyocr' in self.ocr_tracks, on_change=lambda e: self.toggle_track('easyocr', e.value))
                    ui.checkbox('DocTR', value='doctr' in self.ocr_tracks, on_change=lambda e: self.toggle_track('doctr', e.value))

                # Camera Preview
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

                ui.separator().classes('bg-gray-600')
                ui.label("Last Matched Card").classes('text-xl font-bold text-accent')
                self.render_found_card()

            # --- CARD 3: RESULTS ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("3. OCR Results").classes('text-2xl font-bold text-primary')
                self.render_debug_pipeline_results()

        ui.run_javascript('initDebugStream()')

    @ui.refreshable
    def render_found_card(self):
        if not self.last_scan_result:
            ui.label("No result yet.").classes('text-gray-500 italic')
            return

        res = self.last_scan_result
        with ui.row().classes('w-full gap-4 items-start'):
            if res.get('image_path'):
                 ui.image(f"/images/{os.path.basename(res['image_path'])}").classes('w-24 h-auto rounded border')

            with ui.column().classes('gap-1'):
                ui.label(res.get('name', 'Unknown')).classes('font-bold text-lg')
                ui.label(f"Set: {res.get('set_code')}").classes('font-mono text-green-400')
                ui.label(f"Rarity: {res.get('rarity')}").classes('text-sm')
                ui.label(f"1st Ed: {res.get('first_edition')}").classes('text-sm')
                ui.label(f"Lang: {res.get('language')}").classes('text-sm')
                ui.label(f"Match Score: {res.get('match_score')}").classes('text-xs text-gray-400')

    def toggle_track(self, track, enabled):
        if enabled:
            if track not in self.ocr_tracks: self.ocr_tracks.append(track)
        else:
            if track in self.ocr_tracks: self.ocr_tracks.remove(track)

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

    with ui.tabs(on_change=handle_tab_change).classes('w-full') as tabs:
        live_tab = ui.tab('Live Scan')
        debug_tab = ui.tab('Debug Lab')

    with ui.tab_panels(tabs, value=live_tab).classes('w-full h-full'):

        # --- TAB 1: LIVE SCAN ---
        with ui.tab_panel(live_tab):
            with ui.row().classes('w-full gap-4 items-center mb-4'):
                if page.collections:
                    ui.select(options=page.collections, value=page.target_collection_file, label='Collection',
                              on_change=lambda e: setattr(page, 'target_collection_file', e.value)).classes('w-48')

                page.camera_select = ui.select(options={}, label='Camera').classes('w-48')

                # Default Condition Dropdown
                ui.select(['Mint', 'Near Mint', 'Excellent', 'Good', 'Light Played', 'Played', 'Poor'],
                          value=page.default_condition, label='Default Condition',
                          on_change=lambda e: setattr(page, 'default_condition', e.value)).classes('w-40')

                page.start_btn = ui.button('Start', on_click=page.start_camera).props('icon=videocam')
                page.stop_btn = ui.button('Stop', on_click=page.stop_camera).props('icon=videocam_off color=negative')
                page.stop_btn.visible = False

                ui.separator().props('vertical')

                # --- NEW: Status Controls in Live Scan ---
                page.render_status_controls()

                # Replaced Auto Scan with Manual Scan Button
                ui.button('Capture & Scan', on_click=page.trigger_live_scan).props('icon=camera color=accent text-color=black')

                ui.space()
                ui.button('Add to Collection', on_click=page.commit_cards).props('color=primary icon=save')

            with ui.row().classes('w-full h-[calc(100vh-250px)] gap-4'):
                # Camera View
                with ui.card().classes('flex-1 h-full p-0 overflow-hidden relative bg-black'):
                    ui.html('<video id="scanner-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)
                    ui.html('<canvas id="overlay-canvas" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none;"></canvas>', sanitize=False)

                # List View
                with ui.column().classes('w-96 h-full'):
                    ui.label("Recent Scans").classes('text-xl font-bold')
                    with ui.scroll_area().classes('w-full flex-grow border rounded p-2'):
                        page.render_live_list()

        # --- TAB 2: DEBUG LAB ---
        with ui.tab_panel(debug_tab):
             page.render_debug_lab()

    ui.timer(1.0, page.init_cameras, once=True)

    # Use fast consumer loop instead of slow polling
    ui.timer(0.1, page.event_consumer)

    # Initialize from current state immediately
    page.debug_report = scanner_service.scanner_manager.get_debug_snapshot()
