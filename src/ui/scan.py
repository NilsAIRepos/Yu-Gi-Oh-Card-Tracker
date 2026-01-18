from nicegui import ui, app, run
import logging
import os
import asyncio
from typing import List, Dict, Any

from src.services.scanner.manager import scanner_manager, SCANNER_AVAILABLE
from src.core.persistence import persistence
from src.core.models import CollectionCard, CollectionVariant, CollectionEntry
from src.services.ygo_api import ygo_service

logger = logging.getLogger(__name__)

# Client-Side Camera Logic
JS_CAMERA_CODE = """
<script>
var video = null;
var stream = null;

function initScanner() {
    video = document.getElementById('scanner-video');
}

async function startCamera(deviceId) {
    if (!video) initScanner();
    if (stream) {
        stopCamera();
    }
    try {
        const constraints = {
            video: {
                deviceId: deviceId ? { exact: deviceId } : undefined,
                width: { ideal: 1280 },
                height: { ideal: 720 }
            }
        };
        stream = await navigator.mediaDevices.getUserMedia(constraints);
        if (video) {
            video.srcObject = stream;
            await video.play();
        }
        return true;
    } catch (err) {
        console.error("Error accessing camera:", err);
        return false;
    }
}

function stopCamera() {
    if (stream) {
        stream.getTracks().forEach(track => track.stop());
        stream = null;
    }
    if (video) {
        video.srcObject = null;
    }
}

function captureFrame() {
    if (!video || !stream) return null;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    if (canvas.width === 0 || canvas.height === 0) return null;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0);
    return canvas.toDataURL('image/jpeg', 0.8);
}

async function getVideoDevices() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
        return [];
    }
    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        return devices
            .filter(device => device.kind === 'videoinput')
            .map(device => ({ label: device.label || 'Camera ' + (devices.indexOf(device) + 1), value: device.deviceId }));
    } catch (e) {
        console.error(e);
        return [];
    }
}
</script>
"""

class ScanPage:
    def __init__(self):
        self.scanned_cards: List[Dict[str, Any]] = []
        self.target_collection_file = None
        self.list_container = None
        self.start_btn = None
        self.stop_btn = None
        self.status_label = None
        self.camera_select = None
        self.is_active = False

        # Load available collections
        self.collections = persistence.list_collections()
        if self.collections:
            self.target_collection_file = self.collections[0]

    async def init_cameras(self):
        """Fetches video devices from client."""
        try:
            devices = await ui.run_javascript('getVideoDevices()')
            if devices and self.camera_select:
                self.camera_select.options = {d['value']: d['label'] for d in devices}
                if not self.camera_select.value and devices:
                    self.camera_select.value = devices[0]['value']
        except Exception as e:
            logger.error(f"Error fetching cameras: {e}")

    async def start_camera(self):
        device_id = self.camera_select.value if self.camera_select else None
        success = await ui.run_javascript(f'startCamera("{device_id}")')
        if success:
            scanner_manager.start()
            self.start_btn.visible = False
            self.stop_btn.visible = True
        else:
            ui.notify("Failed to access camera. Check permissions.", type='negative')

    async def stop_camera(self):
        await ui.run_javascript('stopCamera()')
        scanner_manager.stop()
        self.start_btn.visible = True
        self.stop_btn.visible = False

    async def update_loop(self):
        if not self.is_active:
            return

        # 1. Sync State (Backend)
        is_running = scanner_manager.running
        # Note: We rely on manual start/stop button clicks to toggle visibility,
        # but we can force sync here if needed.
        # But if JS fails, backend might run while frontend stopped.

        # 2. Update Status
        if self.status_label:
            self.status_label.text = f"Status: {scanner_manager.get_status()}"

        # 3. Process Logic
        await scanner_manager.process_pending_lookups()

        # 4. Capture Frame (Client -> Server)
        if is_running:
            try:
                b64 = await ui.run_javascript('captureFrame()')
                if b64:
                    scanner_manager.push_frame(b64)
            except Exception:
                pass

        # 5. Check for new results
        result = scanner_manager.get_latest_result()
        if result:
            self.add_scanned_card(result)

    def add_scanned_card(self, data: Dict[str, Any]):
        self.scanned_cards.insert(0, data)
        self.render_list.refresh()
        ui.notify(f"Scanned: {data.get('name', 'Unknown')}", type='positive')

    @ui.refreshable
    def render_list(self):
        if not self.list_container:
            return

        with self.list_container:
            self.list_container.clear()

            if not self.scanned_cards:
                ui.label("No cards scanned yet.").classes('text-gray-500 italic')
                return

            for i, card in enumerate(self.scanned_cards):
                with ui.card().classes('w-full mb-2 p-2 flex flex-row items-center gap-4'):
                    # Image
                    img_path = card.get('image_path')
                    if img_path:
                         filename = os.path.basename(img_path)
                         ui.image(f'/images/{filename}').classes('w-16 h-24 object-contain')
                    else:
                        ui.icon('image', size='lg').classes('text-gray-400 w-16 h-24')

                    with ui.column().classes('flex-grow'):
                        ui.label(card.get('name', 'Unknown')).classes('font-bold')
                        ui.label(f"{card.get('set_code')} • {card.get('rarity')} • {card.get('language')}").classes('text-sm text-gray-400')
                        if card.get('first_edition'):
                            ui.badge('1st Ed', color='amber')

                    with ui.row():
                        ui.button(icon='delete', color='negative', flat=True,
                                  on_click=lambda idx=i: self.remove_card(idx))

    def remove_card(self, index):
        if 0 <= index < len(self.scanned_cards):
            self.scanned_cards.pop(index)
            self.render_list.refresh()

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
            self.render_list.refresh()

        except Exception as e:
            logger.error(f"Error saving collection: {e}")
            ui.notify(f"Error saving collection: {e}", type='negative')


def scan_page():
    # Helper to clean up on exit
    def cleanup():
        scanner_manager.stop()
        page.is_active = False

    app.on_disconnect(cleanup)

    page = ScanPage()
    page.is_active = True

    if not SCANNER_AVAILABLE:
        ui.label("Scanner dependencies not found.").classes('text-red-500 text-xl font-bold')
        ui.label("Please install opencv-python, pytesseract, and langdetect.").classes('text-gray-400')
        return

    # Inject Client-Side JS
    ui.add_body_html(JS_CAMERA_CODE)

    with ui.row().classes('w-full gap-4 items-center mb-4'):
        ui.label('Card Scanner').classes('text-2xl font-bold')

        # Collection Select
        if not page.collections:
            ui.label("No collections found. Please create one first.").classes('text-red-400')
        else:
            ui.select(options=page.collections, value=page.target_collection_file, label='Target Collection',
                      on_change=lambda e: setattr(page, 'target_collection_file', e.value)).classes('w-64')

        # Camera Select
        page.camera_select = ui.select(options={}, label='Camera').classes('w-48')

        page.start_btn = ui.button('Start Camera', on_click=page.start_camera).props('icon=videocam')
        page.stop_btn = ui.button('Stop Camera', on_click=page.stop_camera).props('icon=videocam_off flat color=negative')
        page.stop_btn.visible = False # Initial state

        ui.space()
        ui.button('Add Scanned Cards', on_click=page.commit_cards).props('color=primary icon=save')

    with ui.row().classes('w-full h-[calc(100vh-150px)] gap-4'):
        # Left: Camera
        with ui.card().classes('flex-1 min-w-0 h-full p-0 overflow-hidden relative bg-black'):
            # Video Element
            ui.html('<video id="scanner-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>', sanitize=False)

            # Overlay Status
            with ui.column().classes('absolute bottom-4 left-4 p-2 bg-black/50 rounded'):
                page.status_label = ui.label("Status: Idle").classes('text-white text-sm')
                ui.label("Place card in center.").classes('text-white text-sm')

        # Right: List
        with ui.column().classes('flex-1 min-w-0 h-full'):
            ui.label('Session Scanned Cards').classes('text-xl font-bold mb-2')

            with ui.scroll_area().classes('w-full flex-grow border rounded p-2'):
                 page.list_container = ui.column().classes('w-full')
                 page.render_list()

    # Init Cameras after delay
    ui.timer(1.0, page.init_cameras, once=True)

    # Start update timer
    ui.timer(0.3, page.update_loop)
