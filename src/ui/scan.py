from nicegui import ui, app, run, events
import logging
import os
import asyncio
import time
import base64
from typing import List, Dict, Any, Optional
from fastapi import UploadFile

from src.services.scanner.manager import scanner_manager, SCANNER_AVAILABLE
from src.services.log_stream import log_stream
from src.core.persistence import persistence
from src.core.models import CollectionCard, CollectionVariant, CollectionEntry
from src.services.ygo_api import ygo_service

logger = logging.getLogger(__name__)

# --- JS CAMERA CODE ---
# (Kept identical to previous version as it works well for client-side capture)
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
    if (window.scannerVideo && window.scannerVideo.srcObject) {
        const tracks = window.scannerVideo.srcObject.getTracks();
        tracks.forEach(track => track.stop());
        window.scannerVideo.srcObject = null;
    }
    if (window.debugVideo) {
        window.debugVideo.srcObject = null;
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

    if (!videoSource || videoSource.readyState < 2) {
        if (window.debugVideo && window.debugVideo.readyState >= 2) {
             videoSource = window.debugVideo;
        } else {
             return null;
        }
    }

    // Ensure playing
    if (videoSource.paused) {
        try { await videoSource.play(); } catch(e){}
    }

    const canvas = document.createElement('canvas');
    canvas.width = videoSource.videoWidth;
    canvas.height = videoSource.videoHeight;
    canvas.getContext('2d').drawImage(videoSource, 0, 0);
    return canvas.toDataURL('image/jpeg', 0.95);
}

function reattachScannerVideo() {
    window.scannerVideo = document.getElementById('scanner-video');
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

        # UI Refs
        self.camera_select = None
        self.log_view = None
        self.debug_results_container = None
        self.live_list_container = None

        # Config
        self.ocr_tracks = ['easyocr']
        self.preprocessing_mode = 'classic'

        # State
        self.is_scanning = False
        self.latest_report = None

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
            success = await ui.run_javascript(f'startCamera("{device_id}")', timeout=10.0)
            if success:
                ui.notify("Camera started", type='positive')
            else:
                ui.notify("Camera failed to start", type='negative')
        except Exception as e:
            ui.notify(f"Error: {e}", type='negative')

    async def stop_camera(self):
        await ui.run_javascript('stopCamera()')
        ui.notify("Camera stopped", type='info')

    def on_log(self, msg: str):
        """Callback for log stream."""
        if self.log_view:
            self.log_view.push(msg)

    async def run_scan_task(self, image_bytes: bytes, filename: str):
        if self.is_scanning:
            ui.notify("Scan in progress...", type='warning')
            return

        self.is_scanning = True
        ui.notify("Scanning...", type='info')

        try:
            options = {
                "tracks": self.ocr_tracks,
                "preprocessing": self.preprocessing_mode
            }

            # Execute Scan Directly (Async/Threaded)
            # We await the async process_scan, which internally uses run.io_bound for heavy lifting
            report = await scanner_manager.process_scan(image_bytes, options, filename)

            self.latest_report = report

            # Handle Result
            if report.get("scan_result"):
                self.scanned_cards.insert(0, report["scan_result"])
                ui.notify(f"Found: {report['scan_result'].get('name')}", type='positive')
                self.live_list_container.refresh()
            elif report.get("error"):
                 ui.notify(f"Error: {report['error']}", type='negative')
            else:
                ui.notify("No card found.", type='warning')

            self.debug_results_container.refresh()

        except Exception as e:
            logger.error(f"Scan Task Exception: {e}", exc_info=True)
            ui.notify(f"System Error: {str(e)}", type='negative')
        finally:
            self.is_scanning = False

    async def capture_and_scan(self):
        try:
            data_url = await ui.run_javascript('captureSingleFrame()')
            if not data_url:
                ui.notify("Camera not ready", type='warning')
                return

            header, encoded = data_url.split(",", 1)
            content = base64.b64decode(encoded)

            fname = f"capture_{int(time.time())}.jpg"
            await self.run_scan_task(content, fname)

        except Exception as e:
            ui.notify(f"Capture failed: {e}", type='negative')

    async def handle_upload(self, e: events.UploadEventArguments):
        try:
            # Robust file object retrieval - use getattr to avoid AttributeError if 'content' is missing
            file_obj = getattr(e, 'content', None)
            if not file_obj:
                 # Fallback for different NiceGUI versions or event structures
                 file_obj = getattr(e, 'file', None)

            if not file_obj:
                 raise ValueError("No file content found in upload event")

            content = file_obj.read()
            fname = getattr(e, 'name', None) or getattr(file_obj, 'name', None) or "upload.jpg"
            await self.run_scan_task(content, fname)
        except Exception as err:
            ui.notify(f"Upload failed: {err}", type='negative')

    def remove_card(self, index):
        if 0 <= index < len(self.scanned_cards):
            self.scanned_cards.pop(index)
            self.live_list_container.refresh()

    async def commit_cards(self):
        if not self.target_collection_file:
            ui.notify("Select a collection first.", type='warning')
            return

        if not self.scanned_cards:
            return

        try:
            collection = persistence.load_collection(self.target_collection_file)
            count = 0
            for item in self.scanned_cards:
                if not item.get('card_id'): continue

                # Simple add logic
                target_card = next((c for c in collection.cards if c.card_id == item['card_id']), None)
                if not target_card:
                    target_card = CollectionCard(card_id=item['card_id'], name=item['name'])
                    collection.cards.append(target_card)

                target_variant = next((v for v in target_card.variants
                                       if v.set_code == item['set_code'] and v.rarity == item['rarity']), None)
                if not target_variant:
                    # Basic variant creation (simplified for brevity, can rely on existing logic if needed)
                    target_variant = CollectionVariant(
                        variant_id=str(item['card_id']), # simplified
                        set_code=item['set_code'],
                        rarity=item['rarity']
                    )
                    target_card.variants.append(target_variant)

                target_variant.entries.append(CollectionEntry(condition="Near Mint", quantity=1))
                count += 1

            persistence.save_collection(collection, self.target_collection_file)
            ui.notify(f"Added {count} cards.", type='positive')
            self.scanned_cards.clear()
            self.live_list_container.refresh()

        except Exception as e:
            ui.notify(f"Save failed: {e}", type='negative')

    # --- UI RENDERERS ---

    @ui.refreshable
    def render_live_list(self):
        if not self.scanned_cards:
            ui.label("No scans yet.").classes('text-gray-500 italic')
            return

        for i, card in enumerate(self.scanned_cards):
            with ui.card().classes('w-full mb-2 p-2 flex flex-row items-center gap-4 bg-gray-800 border-gray-700'):
                if card.get('image_path'):
                    ui.image(f"/images/{os.path.basename(card['image_path'])}").classes('w-12 h-16 object-contain')
                with ui.column().classes('flex-grow gap-0'):
                    ui.label(card.get('name', 'Unknown')).classes('font-bold text-white')
                    ui.label(f"{card.get('set_code')} | {card.get('rarity')}").classes('text-xs text-gray-400')
                ui.button(icon='delete', color='negative', on_click=lambda idx=i: self.remove_card(idx)).props('flat size=sm')

    @ui.refreshable
    def render_debug_results(self):
        if not self.latest_report:
            ui.label("No data available.").classes('text-gray-500')
            return

        report = self.latest_report

        with ui.grid().classes('grid-cols-1 lg:grid-cols-2 gap-4 w-full'):
            # Images
            with ui.column().classes('w-full'):
                ui.label("Input / Warp").classes('font-bold text-primary')
                if report.get('captured_image_url'):
                    ui.image(report['captured_image_url']).classes('w-full rounded border border-gray-600')
                if report.get('warped_image_url'):
                    ui.image(report['warped_image_url']).classes('w-full rounded border border-gray-600 mt-2')

            # Data
            with ui.column().classes('w-full'):
                 ui.label("OCR Data").classes('font-bold text-primary')

                 for track in ['t1_full', 't1_crop', 't2_full', 't2_crop']:
                     data = report.get(track)
                     with ui.expansion(track.replace('_', ' ').upper(), icon='visibility').classes('w-full bg-gray-800 border-gray-600 mb-1'):
                         if data:
                             ui.label(f"Set ID: {data.get('set_id')} ({data.get('set_id_conf', 0):.1f}%)")
                             ui.label(f"Text: {data.get('raw_text')}").classes('text-xs font-mono break-all')
                         else:
                             ui.label("No Result")

def scan_page():
    page = ScanPage()

    # Disconnect handler to clean up log listener
    def cleanup():
        log_stream.unregister(page.on_log)
    app.on_disconnect(cleanup)

    # Register log listener
    log_stream.register(page.on_log)

    ui.add_head_html(JS_CAMERA_CODE)

    if not SCANNER_AVAILABLE:
        ui.label("Scanner dependencies missing.").classes('text-red-500 text-xl')
        return

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
            with ui.row().classes('w-full items-center gap-4 mb-2'):
                ui.select(options=page.collections, value=page.target_collection_file, label='Collection',
                          on_change=lambda e: setattr(page, 'target_collection_file', e.value)).classes('w-48')

                page.camera_select = ui.select(options={}, label='Camera').classes('w-48')
                ui.button('Start Camera', on_click=page.start_camera).props('icon=videocam')
                ui.button('Stop', on_click=page.stop_camera).props('icon=videocam_off color=negative')

                ui.space()
                ui.button('Capture & Scan', on_click=page.capture_and_scan).props('icon=camera color=accent text-color=black size=lg')
                ui.button('Commit', on_click=page.commit_cards).props('icon=save color=primary')

            with ui.row().classes('w-full h-[calc(100vh-200px)] gap-4'):
                # Camera
                with ui.card().classes('flex-1 h-full p-0 bg-black overflow-hidden'):
                     ui.html('<video id="scanner-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)

                # List
                with ui.column().classes('w-96 h-full'):
                    ui.label("Session Scans").classes('text-xl font-bold')
                    with ui.scroll_area().classes('w-full flex-grow border border-gray-700 rounded p-2'):
                        page.live_list_container = page.render_live_list()

        # --- TAB 2: DEBUG LAB ---
        with ui.tab_panel(debug_tab):
            with ui.row().classes('w-full gap-4'):
                # Left Col: Controls & Input
                with ui.column().classes('w-1/3'):
                    with ui.card().classes('w-full bg-gray-900 border border-gray-700'):
                        ui.label("Configuration").classes('text-lg font-bold text-primary')
                        ui.radio(['classic', 'yolo'], value=page.preprocessing_mode,
                                 on_change=lambda e: setattr(page, 'preprocessing_mode', e.value)).props('inline label="Preprocessing"')

                        with ui.row():
                             ui.checkbox('EasyOCR', value='easyocr' in page.ocr_tracks,
                                         on_change=lambda e: page.ocr_tracks.append('easyocr') if e.value else page.ocr_tracks.remove('easyocr'))
                             ui.checkbox('PaddleOCR', value='paddle' in page.ocr_tracks,
                                         on_change=lambda e: page.ocr_tracks.append('paddle') if e.value else page.ocr_tracks.remove('paddle'))

                        ui.separator().classes('bg-gray-600')
                        ui.label("Input").classes('text-lg font-bold text-primary')

                        # Debug Video
                        with ui.element('div').classes('w-full aspect-video bg-black rounded relative overflow-hidden'):
                             ui.html('<video id="debug-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)

                        with ui.row().classes('w-full'):
                            ui.button('Capture', on_click=page.capture_and_scan).classes('flex-grow bg-accent text-black')

                        ui.upload(label="Upload Image", on_upload=page.handle_upload, auto_upload=True).classes('w-full')

                # Middle/Right Col: Logs & Results
                with ui.column().classes('w-2/3'):
                    # Console
                    ui.label("System Log (Persistent)").classes('text-lg font-bold')
                    page.log_view = ui.log(max_lines=500).classes('w-full h-64 bg-black text-green-400 font-mono text-xs border border-gray-600 rounded p-2')

                    # Results
                    ui.separator().classes('my-4')
                    ui.label("Detailed Results").classes('text-lg font-bold')
                    page.debug_results_container = page.render_debug_results()

    ui.timer(1.0, page.init_cameras, once=True)
