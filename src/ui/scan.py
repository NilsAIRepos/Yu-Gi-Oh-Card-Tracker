from nicegui import ui, app, run, events
import logging
import os
import asyncio
import time
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

# Client-Side Camera Logic (High Quality)
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

        // Also attach to debug video if it exists
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
    // Retry finding the element a few times if it's not immediately available
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

            // Full Resolution Capture
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
            }, 'image/jpeg', 0.95); // High Quality

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
    // Try capturing from scanner video first, then debug video if needed
    let videoSource = window.scannerVideo;
    let usingDebug = false;

    // Check if scanner video is valid and active
    if (!videoSource || videoSource.readyState < 2) {
        if (window.debugVideo && window.debugVideo.readyState >= 2) {
             videoSource = window.debugVideo;
             usingDebug = true;
        } else {
             return null;
        }
    } else {
        // If scanner video is paused/ended, try debug video
        if (videoSource.paused || videoSource.ended) {
             if (window.debugVideo && window.debugVideo.readyState >= 2) {
                 videoSource = window.debugVideo;
                 usingDebug = true;
             }
        }
    }

    // Ensure the chosen source is playing
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
        self.auto_scan_switch = None
        self.is_active = False

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

    def on_tab_change(self, e):
        # e.value is the tab object or value.
        # Checking by label name is tricky if value is object.
        # But we can rely on the tab names we assigned in scan_page function if we had access.
        # Alternatively, we just check if it's the live scan tab.
        # However, e.value is the ui.tab instance.
        pass # Logic handled in scan_page closure

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

    def refresh_debug_ui(self):
        self.render_debug_results.refresh()
        self.render_debug_analysis.refresh()
        self.render_debug_pipeline_results.refresh()

    async def handle_debug_upload(self, e: events.UploadEventArguments):
        self.debug_loading = True
        self.latest_capture_src = None # Clear previous capture on upload
        self.refresh_debug_ui()
        try:
            content = await e.file.read()
            report = await scanner_manager.analyze_static_image(content)
            self.debug_report = report
        except Exception as err:
            ui.notify(f"Analysis failed: {err}", type='negative')
        self.debug_loading = False
        self.refresh_debug_ui()

    async def handle_debug_capture(self):
        self.debug_loading = True
        self.refresh_debug_ui()
        try:
            # Capture frame from client
            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not active or ready", type='warning')
                self.debug_loading = False
                self.refresh_debug_ui()
                return

            # Show immediate preview
            self.latest_capture_src = data_url
            self.refresh_debug_ui()

            # Convert Data URL to bytes
            import base64
            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            report = await scanner_manager.analyze_static_image(content)
            self.debug_report = report
        except Exception as err:
            ui.notify(f"Capture failed: {err}", type='negative')

        self.debug_loading = False
        self.refresh_debug_ui()

    async def update_loop(self):
        if not self.is_active: return

        # Live Overlay
        contour = scanner_manager.get_live_contour()
        if contour:
            await ui.run_javascript(f'drawOverlay({contour})')
        else:
            await ui.run_javascript('clearOverlay()')

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

                # Delete Button
                ui.button(icon='delete', color='negative', flat=True,
                          on_click=lambda idx=i: self.remove_card(idx))

    @ui.refreshable
    def render_debug_results(self):
        if self.debug_loading:
            ui.spinner(size='lg')
            return

        # Preview Section
        preview_src = self.latest_capture_src or self.debug_report.get('input_image_url')
        if preview_src:
            ui.label("Latest Input:").classes('font-bold mt-2 text-lg')
            ui.image(preview_src).classes('w-full h-auto border rounded shadow-md')
        elif self.debug_loading:
            ui.label("Processing...").classes('italic text-accent mt-2')

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

        crops = self.debug_report.get('crops', {})
        if crops:
            ui.label("Extracted Crops:").classes('font-bold text-lg')
            with ui.row().classes('gap-4'):
                if crops.get('set_id'):
                     with ui.column():
                        ui.label("Set ID").classes('text-xs')
                        ui.image(crops['set_id']).classes('h-12 border rounded')
                if crops.get('art'):
                     with ui.column():
                        ui.label("Artwork").classes('text-xs')
                        ui.image(crops['art']).classes('h-32 w-32 object-contain border rounded')

    @ui.refreshable
    def render_debug_pipeline_results(self):
        results = self.debug_report.get('results', {})
        if results:
            with ui.column().classes('w-full gap-2 bg-gray-800 p-4 rounded'):
                with ui.row().classes('w-full justify-between items-center'):
                     ui.label("Set ID:").classes('font-bold text-gray-300')
                     ui.label(f"{results.get('set_id', 'N/A')}").classes('font-mono text-xl text-white')

                with ui.row().classes('w-full justify-between items-center'):
                     ui.label("OCR Conf:").classes('font-bold text-gray-300')
                     conf = results.get('set_id_conf', 0)
                     color = 'text-green-400' if conf > 60 else 'text-red-400'
                     ui.label(f"{conf:.1f}%").classes(f'font-mono text-lg {color}')

                with ui.row().classes('w-full justify-between items-center'):
                     ui.label("Language:").classes('font-bold text-gray-300')
                     ui.label(f"{results.get('language', 'N/A')}").classes('text-lg text-white')

                with ui.row().classes('w-full justify-between items-center'):
                     ui.label("Art Match Score:").classes('font-bold text-gray-300')
                     ui.label(f"{results.get('match_score', 0)}").classes('text-lg text-white')

            ui.separator().classes('my-2 bg-gray-600')
            ui.label(f"{results.get('card_name', 'Unknown')}").classes('text-2xl font-bold text-accent text-center w-full')
        else:
            ui.label("No results yet.").classes('text-gray-500 italic')

        ui.label("Execution Log:").classes('font-bold text-lg mt-4')
        steps = self.debug_report.get('steps', [])
        with ui.scroll_area().classes('h-64 border border-gray-600 p-2 bg-black rounded'):
            for step in steps:
                icon = 'check_circle' if step['status'] == 'OK' else 'error'
                color = 'text-green-500' if step['status'] == 'OK' else 'text-red-500'
                with ui.row().classes('items-center gap-2 mb-1 flex-nowrap'):
                    ui.icon(icon).classes(color)
                    ui.label(f"{step['name']}: {step['details']}").classes('text-sm text-gray-300 font-mono')

    def render_debug_lab(self):
        # Changed to Grid with Card containers for "Larger Boxes" and Modern Look
        with ui.grid().classes('grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 w-full'):

            # --- CARD 1: INPUT SOURCE ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("1. Input Source").classes('text-2xl font-bold text-primary')

                # Camera Preview Area - Static!
                with ui.element('div').classes('w-full aspect-video bg-black rounded relative overflow-hidden'):
                    ui.html('<video id="debug-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)

                # Controls
                with ui.row().classes('w-full gap-2'):
                    ui.button("Capture Frame", on_click=self.handle_debug_capture, icon='camera_alt').classes('flex-grow bg-accent text-black font-bold')

                ui.separator().classes('bg-gray-600')
                ui.upload(label="Or Upload Image", on_upload=self.handle_debug_upload, auto_upload=True).props('accept=.jpg,.png color=secondary').classes('w-full')

                # Results
                self.render_debug_results()

            # --- CARD 2: VISUAL PIPELINE ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("2. Visual Analysis").classes('text-2xl font-bold text-primary')
                self.render_debug_analysis()

            # --- CARD 3: RESULTS & LOGS ---
            with ui.card().classes('w-full p-4 flex flex-col gap-4 shadow-lg bg-gray-900 border border-gray-700'):
                ui.label("3. Pipeline Results").classes('text-2xl font-bold text-primary')
                self.render_debug_pipeline_results()

        # Attach stream to debug video if available
        ui.run_javascript('initDebugStream()')

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
                page.auto_scan_switch = ui.switch('Auto Scan', on_change=lambda e: scanner_manager.set_auto_scan(e.value))
                page.auto_scan_switch.value = not scanner_manager.auto_scan_paused

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
    ui.timer(0.1, page.update_loop)
