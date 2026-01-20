# 🃏 OpenYuGi

> **The Ultimate Local-First Yugioh Collection Manager**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![NiceGUI](https://img.shields.io/badge/Built_with-NiceGUI-red.svg)](https://nicegui.io/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**OpenYuGi** is a professional-grade, privacy-focused application designed for serious collectors and players. Unlike cloud-based alternatives, OpenYuGi runs entirely on your local machine, giving you absolute ownership of your data while providing advanced features like AI-powered card scanning, comprehensive deck building, and precise rarity tracking.

---

## ✨ Key Features

### 📸 AI-Powered Webcam Scanner
Forget manual data entry. OpenYuGi utilizes **OpenCV** and **Tesseract OCR** to detect cards in real-time via your webcam.
- **Auto-Detection**: Instantly identifies card borders and extracts set codes (e.g., `LOB-EN001`).
- **Live Preview**: Visual feedback with green/red contours to ensure perfect scans.
- **Smart Matching**: Automatically resolves ambiguous scans by checking against your local database.

### 🛠 Professional Deck Builder
Construct your winning strategy with a robust deck editor.
- **Format Support**: Fully compatible with standard `.ydk` files (used by EDOPro, YGOOmega).
- **Banlist Integration**: Real-time validation against the latest banlists.
- **Side Decking**: Dedicated support for Main, Extra, and Side decks.
- **Analysis**: Visualize deck composition and stats.

### 📦 Smart Collection Management
Manage thousands of cards with ease.
- **Bulk Operations**: Add or remove cards in bulk efficiently.
- **Rarity Tracking**: Distinguish between a *Common* reprint and a *Secret Rare* original.
- **Data Import**: Import seamlessly from CSV, JSON, or Cardmarket exports.
- **Filter & Search**: Powerful filtering by set, rarity, quantity, and more.

### 🔒 Local-First Architecture
- **You Own Your Data**: All collections, decks, and settings are stored in human-readable JSON/YAML files in the `data/` directory.
- **Offline Capable**: Once images are downloaded, the app works entirely offline.
- **No Accounts**: No login, no tracking, no cloud dependencies.

---

## 🚀 Getting Started

### Prerequisites

1.  **Python 3.10 or higher**: [Download Python](https://www.python.org/downloads/)
2.  **Tesseract OCR** (Required for the Scanner):
    *   **Windows**: [Download the installer](https://github.com/UB-Mannheim/tesseract/wiki) and add the installation path (e.g., `C:\Program Files\Tesseract-OCR`) to your System PATH variable.
    *   **macOS**: `brew install tesseract`
    *   **Linux**: `sudo apt-get install tesseract-ocr`

### Installation

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/yourusername/openyugi.git
    cd openyugi
    ```

2.  **Set up a Virtual Environment (Recommended)**
    ```bash
    python -m venv venv
    # Windows
    .\venv\Scripts\activate
    # Linux/macOS
    source venv/bin/activate
    ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

### Running the Application

Launch the application server:
```bash
python main.py
```
Open your browser and navigate to `http://localhost:8080`.

---

## 🏗 Architecture & Technical Guide

OpenYuGi follows a clean, modular architecture separating business logic from the UI.

### Directory Structure

*   **`src/core`**: The brain of the application. Contains Pydantic models (`models.py`) and file persistence logic (`persistence.py`). This layer has **no** dependencies on the UI.
*   **`src/services`**: Integration layer. Handles external APIs (YGOPRODeck), the Scanner logic (`scanner/`), and Image management.
*   **`src/ui`**: The frontend layer built with **NiceGUI**. Each page (e.g., `deck_builder.py`, `scan.py`) is a self-contained module.
*   **`data/`**: Your data storage.
    *   `collections/`: JSON files for your card inventory.
    *   `decks/`: `.ydk` files for your decks.
    *   `images/`: Cached card artwork.
    *   `db/`: Local copy of the Card Database.

### Tech Stack

*   **NiceGUI**: A Python-based UI framework that wraps Vue.js and Quasar. It allows us to write reactive, modern web UIs entirely in Python.
*   **OpenCV & Tesseract**: The powerhouse behind the scanner. OpenCV handles image processing (contour detection, cropping), while Tesseract performs Optical Character Recognition to read card codes.
*   **Pydantic**: Ensures rigorous data validation. Every card, deck, and collection entry is a typed object, preventing data corruption.

---

## ⚠️ Troubleshooting & "Watch Out For"

### 1. "The Scanner button is grayed out"
**Cause:** The application failed to load `opencv` or `pytesseract`.
**Fix:**
*   Ensure Tesseract is installed on your OS.
*   On Windows, double-check that Tesseract is in your System PATH.
*   Restart the application after installing Tesseract.
*   Check the console logs for "Scanner dependencies missing".

### 2. "Images aren't loading"
**Behavior:** OpenYuGi uses a **Lazy Loading** strategy. It only downloads images when you first view them to save bandwidth and disk space.
**Fix:**
*   Ensure you have an active internet connection for the first load.
*   Go to **Settings > Download All Images** if you prefer to cache everything at once (Warning: Requires ~2GB+ disk space).

### 3. "My changes aren't saving"
**Note:** The application disables "Hot Reload" (`reload=False` in `main.py`) because it writes to the `data/` directory. If you are developing and editing code, you must manually restart the server to see code changes.

---

## 🗺 Roadmap

*   [ ] **Price Trending**: Historical price graphs using Cardmarket/TCGPlayer data.
*   [ ] **Cloud Sync**: Optional integration with Google Drive/Dropbox for backup.
*   [ ] **Mobile Optimization**: improved touch controls for the Deck Builder.
*   [ ] **Advanced Scanner**: GPU acceleration for faster detection.

---

## ❓ FAQ

**Q: Where is my data stored?**
A: Everything is in the `data/` folder in the project root. You can back up this folder to save your entire state.

**Q: Can I import my collection from other apps?**
A: Yes! Use the **Import** page to upload CSV or JSON files. We support standard formats exported by most collection managers.

**Q: Is this legal?**
A: OpenYuGi is a fan-made project. Card images and data are provided by the YGOPRODeck API. This tool is for personal collection management only.

---

*Built with ❤️ by the OpenYuGi Community.*
