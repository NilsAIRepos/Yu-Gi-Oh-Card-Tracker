from nicegui import ui

def apply_theme():
    """Applies the global color theme to the application."""
    ui.colors(
        primary='#1e1e2e',   # Dark background
        secondary='#cba6f7', # Accent purple
        accent='#89b4fa',    # Accent blue
        dark='#11111b',      # Darker background
        positive='#a6e3a1',  # Green
        negative='#f38ba8',  # Red
        info='#74c7ec',      # Cyan
        warning='#f9e2af'    # Yellow
    )
    # Force dark mode for the page
    ui.dark_mode().enable()
