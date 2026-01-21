from nicegui import ui, app, run, events
import logging
import os
import asyncio
import time
import base64
from typing import List, Dict, Any, Optional
from fastapi import UploadFile

from src.services.scanner.manager import scanner_manager, SCANNER_AVAILABLE
from src.core.persistence import persistence
from src.core.models import CollectionCard, CollectionVariant, CollectionEntry
from src.services.ygo_api import ygo_service

logger = logging.getLogger(__name__)

# API Endpoint for Frame Upload
@app.post("/api/scanner/upload_frame")
async def upload_frame(file: UploadFile):
    try:
        content = await file.read()
        scanner_manager.push_frame(content)
        return {"status": "received", "size": len(content)}
    except Exception as e:
        logger.error(f"Error receiving frame: {e}")
        return {"status": "error", "message": str(e)}

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

async function startCamera(deviceId, uploadUrl) {
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

        startStreamingLoop(uploadUrl);
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

function startStreamingLoop(uploadUrl) {
    if (window.streamInterval) clearInterval(window.streamInterval);
    window.isStreaming = true;
    const procCanvas = document.createElement('canvas');
    const procCtx = procCanvas.getContext('2d');

    window.streamInterval = setInterval(() => {
        try {
            if (!window.isStreaming || !window.scannerVideo || window.scannerVideo.readyState < 2) return;

            const w = window.scannerVideo.videoWidth;
            const h = window.scannerVideo.videoHeight;
            if (w === 0 || h === 0) return;

            procCanvas.width = w;
            procCanvas.height = h;
            procCtx.drawImage(window.scannerVideo, 0, 0, w, h);

            procCanvas.toBlob(blob => {
                if (blob) {
                     const formData = new FormData();
                     formData.append('file', blob);
                     fetch(uploadUrl, { method: 'POST', body: formData }).catch(e => console.error("Frame upload failed:", e));
                }
            }, 'image/jpeg', 0.95);

        } catch (err) {
            console.error("Client: captureFrame exception:", err);
        }
    }, 200);
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
        self.ocr_tracks = ['easyocr'] # ['easyocr', 'paddle']
        self.preprocessing_mode = 'classic' # 'classic' or 'yolo'

        # Debug Lab State
        self.debug_report = {}
        self.debug_loading = False
        self.latest_capture_src = None

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

    async def start_camera(self):
        device_id = self.camera_select.value if self.camera_select else None
        try:
            if await ui.run_javascript(f'startCamera("{device_id}", "/api/scanner/upload_frame")', timeout=20.0):
                scanner_manager.start()
                self.start_btn.visible = False
                self.stop_btn.visible = True
            else:
                 ui.notify("Failed to start camera (JS returned false)", type='negative')
        except Exception as e:
            logger.error(f"Error starting camera: {e}")
            ui.notify(f"Error starting camera: {e}", type='negative')

    async def stop_camera(self):
        await ui.run_javascript('stopCamera()')
        scanner_manager.stop()
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

                entry = CollectionEntry(
                    condition="Near Mint",
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
        options = {
            "tracks": self.ocr_tracks,
            "preprocessing": self.preprocessing_mode
        }
        scanner_manager.trigger_scan(options)
        ui.notify("Scanning...", type='info')

    def refresh_debug_ui(self):
        self.render_debug_results.refresh()
        self.render_debug_analysis.refresh()
        self.render_debug_pipeline_results.refresh()

    async def handle_debug_upload(self, e: events.UploadEventArguments):
        self.debug_loading = True
        self.latest_capture_src = None
        self.refresh_debug_ui()
        try:
            content = await e.file.read()
            # We use analyze_static_image for upload, which is async but wrapped in io_bound in manager
            options = {
                "tracks": self.ocr_tracks,
                "preprocessing": self.preprocessing_mode
            }
            report = await scanner_manager.analyze_static_image(content, options)
            # Update local debug state from the report
            self.debug_report = report
            # Also update manager debug state so it persists?
            # analyze_static_image in manager returns a report but doesn't necessarily update global state
            # unless we tell it to. In this case, we use the returned report.
        except Exception as err:
            ui.notify(f"Analysis failed: {err}", type='negative')
        self.debug_loading = False
        self.refresh_debug_ui()

    async def handle_debug_capture(self):
        self.debug_loading = True
        self.refresh_debug_ui()
        try:
            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not active or ready", type='warning')
                self.debug_loading = False
                self.refresh_debug_ui()
                return

            self.latest_capture_src = data_url
            self.refresh_debug_ui()

            # Trigger Scan via Manager (Worker)
            # We need to send bytes.
            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            # Since we have the content, we can manually trigger the queue via trigger_scan
            # but trigger_scan uses 'latest_frame'.
            # We should probably update latest_frame or just use analyze_static_image?
            # The prompt says: "Output ALL 4 OCR RAW TEXT RESULTS... IN THE DEBUG LAB".
            # The manager's trigger_scan flow updates the debug_state which the UI polls.
            # So calling trigger_scan (which uses latest_frame) is preferred if we trust latest_frame.
            # But here we captured a frame explicitly.
            # Let's push this frame then trigger.
            scanner_manager.push_frame(content)

            options = {
                "tracks": self.ocr_tracks,
                "preprocessing": self.preprocessing_mode
            }
            scanner_manager.trigger_scan(options)

        except Exception as err:
            ui.notify(f"Capture failed: {err}", type='negative')
            self.debug_loading = False
            self.refresh_debug_ui()

    async def update_loop(self):
        if not self.is_active: return

        # Poll Debug State
        self.debug_report = scanner_manager.get_debug_snapshot()
        if self.debug_report.get('logs') and self.debug_loading:
             # If we were loading, check if done?
             # Simple logic: just refresh UI periodically if visible
             pass

        # Only refresh if something changed?
        # For now, let's refresh periodically if loading, or if we have new data.
        # Ideally we'd have a dirty flag.
        # We can refresh the debug UI every loop? Might be too heavy.
        # Let's refresh only if status is "Processing..." or similar.
        if scanner_manager.is_processing:
             self.refresh_debug_ui()
        elif self.debug_loading: # Should turn off
             self.debug_loading = False
             self.refresh_debug_ui()

        # Process Pending Lookups (Async Resolver)
        # This is where we act as the consumer of lookup_queue
        await scanner_manager.process_pending_lookups()

        # Live Results
        result = scanner_manager.get_latest_result()
        if result:
            self.scanned_cards.insert(0, result)
            self.render_live_list.refresh()
            ui.notify(f"Scanned: {result.get('name')}", type='positive')

        # Notifications
        note = scanner_manager.get_latest_notification()
        if note:
             ui.notify(note[1], type=note[0])

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
                    ui.label(f"{card.get('set_code')}").classes('text-xs text-gray-500')

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
        elif scanner_manager.is_processing:
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

    @ui.refreshable
    def render_debug_pipeline_results(self):
        # 4 Collapsable Zones

        def render_zone(title, key):
            data = self.debug_report.get(key)
            with ui.expansion(title, icon='visibility').classes('w-full bg-gray-800 border border-gray-600 mb-2'):
                if data:
                    with ui.column().classes('p-2 w-full'):
                        ui.label(f"Set ID: {data.get('set_id', 'N/A')}").classes('font-bold text-green-400')
                        ui.label(f"Conf: {data.get('set_id_conf', 0):.1f}%").classes('text-sm')
                        ui.label(f"Lang: {data.get('language', 'N/A')}").classes('text-sm')
                        ui.separator().classes('bg-gray-600 my-1')
                        ui.label("Raw Text:").classes('text-xs text-gray-400')
                        ui.label(data.get('raw_text', '')).classes('font-mono text-xs break-all bg-black p-1 rounded')
                else:
                    ui.label("No Data").classes('italic text-gray-500 p-2')

        render_zone("Track 1: EasyOCR (Full Frame)", "t1_full")
        render_zone("Track 1: EasyOCR (Cropped)", "t1_crop")
        render_zone("Track 2: PaddleOCR (Full Frame)", "t2_full")
        render_zone("Track 2: PaddleOCR (Cropped)", "t2_crop")

        ui.separator().classes('my-4')

        ui.label("Execution Log:").classes('font-bold text-lg')
        logs = self.debug_report.get('logs', [])
        with ui.scroll_area().classes('h-48 border border-gray-600 p-2 bg-black rounded font-mono text-xs text-green-500'):
            for log in logs:
                ui.label(log)

    def render_debug_lab(self):
        with ui.grid().classes('grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 w-full'):

            # --- CARD 1: CONTROLS & INPUT ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("1. Configuration & Input").classes('text-2xl font-bold text-primary')

                # Preprocessing Toggle
                ui.label("Preprocessing Strategy:").classes('font-bold text-gray-300')
                with ui.row():
                    ui.radio(['classic', 'yolo'], value=self.preprocessing_mode, on_change=lambda e: setattr(self, 'preprocessing_mode', e.value)).props('inline')

                # Tracks Selector
                ui.label("Active Tracks:").classes('font-bold text-gray-300')
                # Checkboxes
                with ui.row():
                    ui.checkbox('EasyOCR', value='easyocr' in self.ocr_tracks, on_change=lambda e: self.toggle_track('easyocr', e.value))
                    ui.checkbox('PaddleOCR', value='paddle' in self.ocr_tracks, on_change=lambda e: self.toggle_track('paddle', e.value))

                # Camera Preview
                with ui.element('div').classes('w-full aspect-video bg-black rounded relative overflow-hidden'):
                    ui.html('<video id="debug-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)

                # Controls
                with ui.row().classes('w-full gap-2'):
                    ui.button("Capture & Analyze", on_click=self.handle_debug_capture, icon='camera_alt').classes('flex-grow bg-accent text-black font-bold')

                ui.separator().classes('bg-gray-600')
                ui.upload(label="Upload Image", on_upload=self.handle_debug_upload, auto_upload=True).props('accept=.jpg,.png color=secondary').classes('w-full')

                self.render_debug_results()

            # --- CARD 2: VISUAL ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("2. Visual Analysis").classes('text-2xl font-bold text-primary')
                self.render_debug_analysis()

            # --- CARD 3: RESULTS ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("3. OCR Results").classes('text-2xl font-bold text-primary')
                self.render_debug_pipeline_results()

        ui.run_javascript('initDebugStream()')

    def toggle_track(self, track, enabled):
        if enabled:
            if track not in self.ocr_tracks: self.ocr_tracks.append(track)
        else:
            if track in self.ocr_tracks: self.ocr_tracks.remove(track)

def scan_page():
    def cleanup():
        scanner_manager.stop()
        page.is_active = False

    app.on_disconnect(cleanup)
    page = ScanPage()
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
                page.start_btn = ui.button('Start', on_click=page.start_camera).props('icon=videocam')
                page.stop_btn = ui.button('Stop', on_click=page.stop_camera).props('icon=videocam_off color=negative')
                page.stop_btn.visible = False

                ui.separator().props('vertical')

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
    ui.timer(0.2, page.update_loop) # Slightly slower loop
