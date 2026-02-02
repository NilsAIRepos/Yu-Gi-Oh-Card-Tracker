# 🃏 OpenYuGi

> **The Ultimate Local-First Yugioh Collection Manager**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![NiceGUI](https://img.shields.io/badge/Built_with-NiceGUI-red.svg)](https://nicegui.io/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active_Development-green)](https://github.com/yourusername/openyugi)

---

## 📖 Introduction

**OpenYuGi** is not just another collection manager—it is a professional-grade, privacy-focused environment designed for serious collectors, tournament players, and data hoarders.

In an era where every app demands an account, a subscription, and an internet connection, OpenYuGi takes a radical stance: **Your Data, Your Drive.**

### Why OpenYuGi?

*   **🔒 Privacy First**: No login. No telemetry. No cloud sync. Your collection lives in plain text JSON files on your hard drive. You are the sole owner of your inventory.
*   **🛠 Hacker Friendly**: The entire application is built on Python and readable text files. Want to write a script to analyze your card values? You can parse your own data in seconds.
*   **♾️ Forever Free**: As an open-source project, OpenYuGi will never charge you to manage your own cards.

---

## 📚 Documentation

For detailed guides, tutorials, and help, please visit our **[Wiki](docs/Home.md)**.

---

## ✨ Key Features at a Glance

*   **Smart Inventory**: Track infinite cards with granular details (Set Code, Rarity, Condition, Language, Edition).
*   **Storage Management**: Organize your collection into Binders and Boxes with visual feedback.
*   **Dual-Mode Views**: Switch between "Player Mode" (consolidated copies) and "Collector Mode" (specific printings).
*   **Pro Deck Builder**: Full `.ydk` support, integrated banlist validation, and collection cross-referencing.
*   **Market Integration**: Automatic price fetching from Cardmarket and TCGPlayer.
*   **Bulk Operations**: Add, move, or edit hundreds of cards at once via Drag-and-Drop.
*   **AI Scanner**: Webcam-based card recognition using OCR and Art Matching.

---

## 🚀 Getting Started

Follow this comprehensive guide to set up your environment.

### 1. Installation & Setup

**Prerequisites**
*   **Python 3.10 or newer**: [Download Here](https://www.python.org/downloads/). Verify with `python --version`.
*   **(Optional) Scanner Dependencies**:
    *   **Tesseract OCR**: Required for text recognition.
        *   **Windows**: [Download Installer](https://github.com/UB-Mannheim/tesseract/wiki). **Important**: Check "Add to PATH" during installation.
        *   **Linux**: `sudo apt-get install tesseract-ocr`
        *   **macOS**: `brew install tesseract`
    *   **Python Libraries**: The scanner requires `opencv-python`, `torch`, and `easyocr`/`doctr`. These are included in `requirements.txt` but may require system-level dependencies on Linux.

**Step-by-Step Installation**

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/yourusername/openyugi.git
    cd openyugi
    ```

2.  **Create a Virtual Environment** (Highly Recommended)
    *   *Windows*:
        ```bash
        python -m venv venv
        .\venv\Scripts\activate
        ```
    *   *Linux / macOS*:
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Launch the Application**
    ```bash
    python main.py
    ```
    The server will start, and your default web browser should automatically open `http://localhost:8080`.

### 2. Creating Your First Collection
OpenYuGi supports multiple separate collection files (e.g., "Main Binder", "Trade Binder", "Bulk Box").

1.  Navigate to the **Collection** page (default view).
2.  Locate the **Collection Dropdown** in the top-left header.
3.  Select the last option: **+ New Collection**.
4.  Enter a descriptive name (e.g., `My_Goat_Format_Collection`) and click **Create**.
5.  Your new JSON file is created in `data/collections/`.

### 3. Adding Cards
Populate your database using one of these methods:

*   **Method A: Manual Search (Best for single cards)**
    1.  Use the **Search Bar** in the header of the **Collection** tab. Type a card name (e.g., "Blue-Eyes").
    2.  Click the card to open the **Detail View**.
    3.  Select your specific Set Code (e.g., `LOB-EN001`), Rarity, language and condition.
    4.  Click **Add**.

*   **Method B: Bulk Add (Best for lists)**
    1.  Navigate to **Bulk Add**.
    2.  Use the **Library** pane (left) to search for cards.
    3.  **Drag and Drop** cards into your **Collection** pane (right).
    4.  Use the "Update" controls to batch-apply Language, Condition, or Storage Location to selected cards.

*   **Method C: Import (Best for migration)**
    1.  Go to **Import Tools**.
    2.  Upload a supported file (Cardmarket Stock File or Backup JSON).

---

## 📖 In-Depth Feature Guide

### 📦 Smart Collection Management

The **Collection View** is the heart of OpenYuGi. It is designed to handle thousands of entries without lag.

#### View Modes
*   **Consolidated View (Default)**:
    *   *Purpose*: Deck building and gameplay.
    *   *Behavior*: Aggregates all copies of "Mystical Space Typhoon" into a single entry.
    *   *Info*: Shows "Total Owned: 15" regardless of whether they are Commons, Secrets, or Starfoils.
*   **Collectors View**:
    *   *Purpose*: Valuation and trading.
    *   *Behavior*: Displays a separate row for every distinct printing.
    *   *Info*: "MRD-047 (Ultra) - 1x", "MRL-047 (Common) - 3x".

#### Filters & Sorting
The filter pane (accessible via the Filter icon) offers granular control:
*   **Set Code**: Supports loose matching (`LOB`) or strict matching (`| LOB`).
*   **Rarity**: Filter by specific rarities (e.g., "Quarter Century Secret Rare").
*   **Stats**: Filter by ATK/DEF ranges, Level, or Scale.
*   **Ownership**: Toggle "Owned Only" to hide database cards you don't have.

### 🗃️ Storage Management

Organize your physical inventory to match your digital one.

*   **Binders & Boxes**: Create named storage locations (e.g., "Binder 1", "Bulk Box A").
*   **Visual Gallery**: View all your storage containers with card counts.
*   **Assignment**:
    *   Assign cards to storage during **Scan** or **Bulk Add**.
    *   Right-click cards in the **Storage** page to move them in or out of containers.
    *   Filter your Collection view by specific Storage Locations.

### 🛠 Professional Deck Builder

The Deck Builder is fully compatible with the wider Yugioh ecosystem.

*   **Format**: Decks are saved as `.ydk` files in `data/decks/`. You can copy these files directly to your **EDOPro** or **YGOOmega** folder.
*   **Banlist Integration**:
    *   The app automatically fetches **TCG**, **OCG**, and **Goat** lists.
    *   Illegal cards are visually highlighted with red borders.
*   **Collection Sync**: The builder shows you exactly how many copies of a card you own while you build. No more proxying cards you thought you had!

### 🔄 Import / Export Tools

OpenYuGi removes the friction of moving data.

#### Supported Import Formats
1.  **Cardmarket Stock File (`.txt`, `.pdf`)**:
    *   Export your stock from Cardmarket and upload it here.
2.  **OpenYuGi JSON Backup**:
    *   Restores a full collection snapshot.

#### Export Options
*   **CSV Export**: Generates a spreadsheet-compatible file containing `Card Name, Set Code, Rarity, Quantity, Price`.
*   **JSON Backup**: A complete dump of the internal data structure.

### 🗄️ Database Editor

Sometimes the official API is wrong, or you have a custom proxy.

*   **Edit Cards**: Modify ATK, DEF, Level, or Name locally.
*   **Custom Sets**: Create your own Set Codes (e.g., `CUST-001`) and assign them to cards.
*   **Fix Rarities**: If a new reprint isn't in the database yet, you can manually add the Rarity entry to the card.

---

## 📸 AI-Powered Webcam Scanner (Beta)
![Status](https://img.shields.io/badge/Status-Beta-yellow)

The Scanner allows you to digitize your physical cards rapidly.

**Features**:
*   **Live Scan**: Point your webcam at a card. The app detects the card boundary, corrects perspective, and performs OCR.
*   **Dual-Track Recognition**: Uses **EasyOCR** or **DocTR** for text reading, combined with **YOLO** for Art Style matching.
*   **Ambiguity Resolution**: If multiple prints exist (e.g., same set code but different rarity), the app prompts you to select the correct one.
*   **Batch Commit**: Scanned cards are added to a temporary "Recent Scans" list. Review them, apply bulk edits (Condition/Language), and commit them to your collection in one click.
*   **Debug Lab**: Advanced users can visualize the pipeline steps (Edge Detection, Warp, ROI Extraction) to tune parameters.

---

## 🏗 Architecture & Data Model

For developers and power users who want to touch the metal.

### Data Sources & APIs
OpenYuGi relies on powerful external APIs to provide accurate data without maintaining a massive centralized server.

*   **YGOPRODeck API**: Primary source for Card Data and Images.
*   **Yugipedia**: Source for Structure Deck import data.

### Directory Structure
```
openyugi/
├── data/                  # USER DATA (Backup this folder!)
│   ├── collections/       # .json inventory files
│   ├── decks/             # .ydk deck files
│   ├── images/            # Downloaded .jpg assets
│   └── db/                # Local card database cache
├── src/
│   ├── core/              # Models, Config, Persistence
│   ├── services/          # API, Scanner, Image Manager
│   └── ui/                # NiceGUI Frontend Components
├── main.py                # Entry Point
└── requirements.txt       # Dependencies
```

### Data Schema (JSON)
Your collection is stored as a list of **CollectionCard** objects. Here is what a single entry looks like in your `.json` file:

```json
{
  "card_id": 46986414,
  "name": "Blue-Eyes White Dragon",
  "variants": [
    {
      "set_code": "LOB-001",
      "rarity": "Ultra Rare",
      "entries": [
        {
          "quantity": 1,
          "condition": "Near Mint",
          "language": "EN",
          "first_edition": true,
          "purchase_price": 50.00,
          "storage_location": "Binder 1"
        }
      ]
    }
  ]
}
```

---

## ⚠️ Troubleshooting

### "ModuleNotFoundError: No module named 'src'"
*   **Cause**: You ran `python src/main.py` instead of `python main.py`.
*   **Fix**: Always run the application from the root directory using `python main.py`.

### "The Scanner is disabled"
*   **Cause**: You did not install Tesseract or `pytesseract` could not find the executable.
*   **Fix**: Install Tesseract (see Installation section) and restart the app.

### "Images are missing"
*   **Cause**: OpenYuGi uses **Lazy Loading**. Images are downloaded on-demand.
*   **Fix**: Ensure you have an internet connection. If images still fail, check the `data/images` folder permissions.

---

## 🗺 Roadmap

The journey doesn't end here. We have big plans for OpenYuGi:

*   [ ] **Price History Graphs**: Visualize the value of your collection over time.
*   [ ] **Cloud Sync Plugins**: Optional support for syncing `data/` to Google Drive or Dropbox.
*   [ ] **Mobile Interface**: Optimization for touch screens and mobile browsers.
*   [ ] **GPU Acceleration**: Leveraging CUDA for the AI Scanner.
*   [ ] **Custom Banlists**: A UI to create your own banlists for local tournaments.

---

## 🤝 Contributing

OpenYuGi is a community-driven project. We welcome pull requests!

1.  Fork the repo.
2.  Create a feature branch (`git checkout -b feature/AmazingFeature`).
3.  Commit your changes.
4.  Open a Pull Request.

---

## ❓ FAQ

**Q: Is this safer than a website?**
A: Yes. Your data never leaves your computer. If the internet goes down, or if a website shuts down, your collection remains safe on your drive.

**Q: Can I manage multiple collections?**
A: Absolutely. You can create unlimited collection files (e.g., one for trades, one for keeps, one for bulk).

**Q: Does it support Speed Duel / Rush Duel?**
A: The database includes Speed Duel cards. Rush Duel support depends on the YGOPRODeck API coverage.

---

*Built with ❤️ by the OpenYuGi Community.*
