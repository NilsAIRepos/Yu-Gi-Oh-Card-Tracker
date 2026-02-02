# OpenYuGi - Architectural Guidelines

## Core Philosophy
**"Local-First Data, Professional Grade Management."**
*   **Source of Truth:** Local JSON/YAML files in the `data/` directory. The application state is hydrated from these files, and changes are persisted back atomically or via robust managers.
*   **Privacy & Ownership:** No external accounts, no cloud sync enforcement. User data remains on the local drive.
*   **UI Framework:** NiceGUI (Python-based).
*   **Style:** Modern, Dark Mode, Responsive.

## Directory Structure
*   `src/core/`: Business logic, Pydantic models (`models.py`), persistence layers (`persistence.py`), and configuration. **No UI code here.**
*   `src/ui/`: NiceGUI components (`components/`) and page logic. Each major feature (e.g., `scan.py`, `bulk_add.py`) generally has its own module.
*   `src/services/`: Integration layers.
    *   `ygo_api.py`: Yugipedia & YGOPRODeck integration.
    *   `scanner/`: Event-driven Computer Vision pipeline (`manager.py`, `pipeline.py`).
    *   `image_manager.py`: Handling local asset caching and downloading.
*   `data/`: User data storage (Collections, Decks, Images, DB Cache).

## Architectural Patterns

### 1. Data Mutation & Integrity
*   **CollectionEditor:** All modifications to card collections (Add, Remove, Move, Edit) MUST go through `src/services/collection_editor.py`.
    *   *Why?* It handles complex logic like variant matching, stack merging (incrementing quantity instead of duplicate rows), and storage location consistency.
*   **Persistence:** Use `src/core/persistence.py` for loading/saving.
*   **Async I/O:** Heavy operations (file saves, API calls) should use `nicegui.run.io_bound` to prevent blocking the main UI thread.

### 2. Event-Driven Scanner
The Scanner (`src/ui/scan.py` + `src/services/scanner/`) operates on a decoupled event-driven model:
*   **ScannerManager:** Runs in the background (Daemon). It accepts "Scan Requests" into a queue.
*   **Pipeline:** Processes images (OCR -> Feature Extraction -> Match) and emits events (`scan_started`, `step_complete`, `scan_finished`).
*   **UI Consumer:** The Frontend (`ScanPage`) registers a listener to update the UI in real-time without polling the database.

### 3. UI State Management
*   **Persistence:** UI state (e.g., last selected collection, active filters) is persisted via `persistence.save_ui_state` and `load_ui_state`.
*   **Component Isolation:** Complex UI elements (like `FilterPane`, `SingleCardView`) are encapsulated in `src/ui/components/` to promote reusability across pages (e.g., shared between `bulk_add` and `collection`).

### 4. Image Handling
*   **Lazy Loading:** Images are downloaded on-demand.
*   **ImageManager:** `src/services/image_manager.py` handles checking existence, downloading, and serving local paths. It differentiates between High-Res and Low-Res assets.

## Coding Standards
*   **Typing:** Use Python type hints everywhere.
*   **Models:** Use Pydantic for all data structures (`ApiCard`, `CollectionCard`, `StorageDefinition`).
*   **Async/Await:** Use `async def` for UI event handlers to ensure responsiveness.
*   **Error Handling:** Use `ui.notify` for user-facing errors and `logging` for internal diagnostics.
