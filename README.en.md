# CryLocal 0.4 — Windows quick start

1. Extract **CryLocal-Windows-x64.zip** completely into a folder on Windows 10/11 x64.
2. Run **Start CryLocal.cmd**. Python 3.14.8 and PySide6 6.12.0 are included; a separate Python installation and administrator privileges are not required.
3. Select **English** or **Українська** from **Interface language** in the top right. The selection is remembered and applied immediately. Choose a target language from **Translation language** beside the game selector; Ukrainian is the default, and the choice is saved with each project. Translations are kept separately for each target language, so switching languages preserves the current work and restores saved rows for the selected language.
4. Choose **Crysis 1 Remastered**, **Crysis 2 Remastered** or **Crysis 3 Remastered** from the **Game** list, then select that game’s language PAK. Switching games creates a separate project; cancelling the PAK picker restores the previous game. Opening a saved project restores its game selection. The last saved project reopens at the next launch. For the included starter project, click **Locate PAK…** and choose your matching `english.pak`. The XML fingerprints must match. Create a new project if you have a different game version.
5. Edit rows manually with **Copy source / Paste translation**, or use **Download 4B** to download TranslateGemma 4B (2.49 GB). In **Settings → Acceleration**, select **GPU · Vulkan** to run all model layers on a compatible GPU, or select **CPU**. GPU mode needs a compatible Vulkan driver and enough VRAM; it does not fall back to CPU. The model runs locally and supports translation among 55 languages; the app offers 45 common targets. Use **Translate row** for an editable result, or **Translate batch** for selected rows/search results. Existing translations for the active target language are skipped in batch mode.
6. **Ctrl+S** saves the project. **Ctrl+Enter** applies the current translation and moves to the next row. CSV export/import is also available; edit only the `translation` column.
7. Click **Validate**, then **Build PAK**. Save the result outside the game folder. Back up the original PAK before manually installing the result. The utility does not install files into the game automatically.

Windows settings, model files, logs and the initial editable project are stored in **%LOCALAPPDATA%\CryLocal**. Other projects are saved wherever you choose. Batch translation creates a project backup and a recovery journal.

The portable ZIP includes Windows Python and Qt, but excludes the model. First-time model download needs internet; built-in translation works offline afterwards. If using the smaller source ZIP, the launcher downloads a verified portable Python/Qt environment using `windows/setup.ps1` first.

The Windows Python/Qt binaries and Windows llama.cpp executable were exercised through **Wine on Linux**. This is not a clean Windows 10/11 machine test. Crysis 2/3 Remastered files and final text/font appearance inside the game have not been verified.

Third-party files retain their licenses in `.python/` and its package directories. Pinned component URLs, sizes and SHA-256 values are recorded in `windows/artifacts.json`. The model/runtime sources and detailed localization workflow are listed in [the Ukrainian manual](README.md).
