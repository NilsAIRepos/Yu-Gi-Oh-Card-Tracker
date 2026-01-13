# OpenYuGi - Architectural Guidelines

## Core Philosophy
"Local-First Data, Professional Grade Management."
- **Source of Truth:** Local JSON/YAML files in the `data/` directory.
- **UI Framework:** NiceGUI (Python-based).
- **Style:** Modern, Dark Mode, Responsive.

## Directory Structure
- `src/core/`: Business logic, Pydantic models, and file persistence. **No UI code here.**
- `src/ui/`: NiceGUI components and pages.
- `src/services/`: External API integrations (Mocked/Placeholder for now).
- `data/`: User data storage.

## Coding Standards
- **Typing:** Use Python type hints everywhere.
- **Models:** Use Pydantic for all data structures.
- **Async:** Use `async/await` for file I/O and UI events where possible to keep the interface snappy.
- **UI Components:** Create reusable components for Cards, Headers, etc., to maintain visual consistency.
