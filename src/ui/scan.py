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
    if (!window.scannerVideo || window.scannerVideo.readyState < 2) return null;
    const canvas = document.createElement('canvas');
    canvas.width = window.scannerVideo.videoWidth;
    canvas.height = window.scannerVideo.videoHeight;
    canvas.getContext('2d').drawImage(window.scannerVideo, 0, 0);
    return canvas.toDataURL('image/jpeg', 0.95);
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

        # Debug Lab State
        self.debug_report = {}
        self.debug_loading = False

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
        if await ui.run_javascript(f'startCamera("{device_id}", "/api/scanner/upload_frame")'):
            scanner_manager.start()
            self.start_btn.visible = False
            self.stop_btn.visible = True

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

    async def handle_debug_upload(self, e: events.UploadEvent):
        self.debug_loading = True
        self.render_debug_lab.refresh()
        try:
            content = e.content.read()
            report = await scanner_manager.analyze_static_image(content)
            self.debug_report = report
        except Exception as err:
            ui.notify(f"Analysis failed: {err}", type='negative')
        self.debug_loading = False
        self.render_debug_lab.refresh()

    async def handle_debug_capture(self):
        self.debug_loading = True
        self.render_debug_lab.refresh()
        try:
            # Capture frame from client
            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not active or ready", type='warning')
                self.debug_loading = False
                self.render_debug_lab.refresh()
                return

            # Convert Data URL to bytes
            import base64
            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            report = await scanner_manager.analyze_static_image(content)
            self.debug_report = report
        except Exception as err:
            ui.notify(f"Capture failed: {err}", type='negative')

        self.debug_loading = False
        self.render_debug_lab.refresh()

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
    def render_debug_lab(self):
        if self.debug_loading:
            ui.spinner(size='lg')
            return

        with ui.grid(columns=3).classes('w-full gap-4'):
            # Column 1: Input & Controls
            with ui.column().classes('p-4 border rounded'):
                ui.label("1. Input Source").classes('text-xl font-bold mb-4')
                ui.upload(label="Upload Image", on_upload=self.handle_debug_upload, auto_upload=True).props('accept=.jpg,.png').classes('w-full')
                ui.separator()
                ui.button("Capture from Camera", on_click=self.handle_debug_capture, icon='camera_alt').classes('w-full')

                if self.debug_report.get('input_image_url'):
                    ui.label("Input Image:").classes('font-bold mt-4')
                    ui.image(self.debug_report['input_image_url']).classes('w-full h-auto border')

            # Column 2: Visual Pipeline
            with ui.column().classes('p-4 border rounded'):
                ui.label("2. Visual Analysis").classes('text-xl font-bold mb-4')

                if self.debug_report.get('warped_image_url'):
                    ui.label("Warped & Pre-processed:").classes('font-bold')
                    ui.image(self.debug_report['warped_image_url']).classes('w-full h-auto border mb-2')

                if self.debug_report.get('roi_viz_url'):
                    ui.label("ROI Visualization:").classes('font-bold')
                    ui.image(self.debug_report['roi_viz_url']).classes('w-full h-auto border mb-2')

                crops = self.debug_report.get('crops', {})
                if crops.get('set_id'):
                    ui.label("Set ID Crop:").classes('font-bold')
                    ui.image(crops['set_id']).classes('h-16 border mb-2')

                if crops.get('art'):
                    ui.label("Art Crop:").classes('font-bold')
                    ui.image(crops['art']).classes('w-32 h-32 object-contain border')

            # Column 3: Logic & Logs
            with ui.column().classes('p-4 border rounded'):
                ui.label("3. Pipeline Results").classes('text-xl font-bold mb-4')

                results = self.debug_report.get('results', {})
                if results:
                    with ui.row().classes('w-full justify-between'):
                         ui.label("Set ID:").classes('font-bold')
                         ui.label(f"{results.get('set_id', 'N/A')}").classes('font-mono')

                    with ui.row().classes('w-full justify-between'):
                         ui.label("OCR Conf:").classes('font-bold')
                         conf = results.get('set_id_conf', 0)
                         color = 'text-green-600' if conf > 60 else 'text-red-600'
                         ui.label(f"{conf:.1f}%").classes(f'font-mono {color}')

                    with ui.row().classes('w-full justify-between'):
                         ui.label("Language:").classes('font-bold')
                         ui.label(f"{results.get('language', 'N/A')}")

                    with ui.row().classes('w-full justify-between'):
                         ui.label("Art Match Score:").classes('font-bold')
                         ui.label(f"{results.get('match_score', 0)}")

                    ui.separator().classes('my-2')
                    ui.label(f"Final Card: {results.get('card_name', 'Unknown')}").classes('text-lg font-bold text-primary')

                ui.label("Pipeline Steps:").classes('font-bold mt-4')
                steps = self.debug_report.get('steps', [])
                with ui.scroll_area().classes('h-64 border p-2 bg-gray-50'):
                    for step in steps:
                        icon = 'check_circle' if step['status'] == 'OK' else 'error'
                        color = 'green' if step['status'] == 'OK' else 'red'
                        with ui.row().classes('items-center gap-2 mb-1'):
                            ui.icon(icon, color=color)
                            ui.label(f"{step['name']}: {step['details']}").classes('text-sm')

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

    with ui.tabs().classes('w-full') as tabs:
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
