# 🏗️ XLWings Excel Sheet: Installation & Setup Guide

This repository contains a Python-powered Excel application for simply supported steel beam design. It uses **pandas** for data handling, **xlwings** for the Excel interface, and **PyLaTeX** for automated structural calculation reports.

Follow these steps to configure your local machine.

---

## 1. System Prerequisites

Before configuring the Python environment, ensure your Windows machine has the following foundational software installed:

*   **Microsoft Excel:** Desktop version required.
*   **Python:** Version 3.9 or newer.
*   **Git:** For version control.
*   **MiKTeX:** Essential system-level LaTeX compiler required by PyLaTeX to generate PDF reports. Download from [miktex.org/download](https://miktex.org/download).

> **Crucial MiKTeX Note:** During installation, if prompted to "Install missing packages on-the-fly", you must select **Yes**. Restart your computer or terminal after installing MiKTeX.

---

## 2. Clone the Repository

Open your PowerShell terminal and download the project files:

```powershell
git clone [https://github.com/USERNAME/xlwings_spreadsheet_structural.git](https://github.com/USERNAME/xlwings_spreadsheet_structural.git)
cd xlwings_spreadsheet_structural