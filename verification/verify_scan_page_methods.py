
import sys
import os
import asyncio

# Adjust path to import src
sys.path.append(os.getcwd())

# Mock nicegui components to allow import
import unittest.mock
sys.modules['nicegui'] = unittest.mock.MagicMock()
sys.modules['nicegui.ui'] = unittest.mock.MagicMock()
sys.modules['nicegui.app'] = unittest.mock.MagicMock()
sys.modules['nicegui.run'] = unittest.mock.MagicMock()
sys.modules['nicegui.events'] = unittest.mock.MagicMock()
sys.modules['fastapi'] = unittest.mock.MagicMock()

from src.ui.scan import ScanPage

def verify_methods():
    print("--- Verifying ScanPage Methods ---")
    page = ScanPage()

    print(f"Has status_loop: {hasattr(page, 'status_loop')}")
    print(f"Has processing_loop: {hasattr(page, 'processing_loop')}")
    print(f"Has update_loop: {hasattr(page, 'update_loop')}")

    if hasattr(page, 'status_loop') and hasattr(page, 'processing_loop') and not hasattr(page, 'update_loop'):
        print("PASS: Methods exist and update_loop is removed.")
        return True
    else:
        print("FAIL: Method structure incorrect.")
        return False

if __name__ == "__main__":
    if verify_methods():
        print("\nVerification: SUCCESS")
    else:
        print("\nVerification: FAILED")
        sys.exit(1)
