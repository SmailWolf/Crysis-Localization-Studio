# CryLocal

Desktop toolkit for manual and local AI localization of Crysis Remastered games. It includes Ukrainian and English interfaces, 45 target languages, CSV import/export, and locally run TranslateGemma 4B.

- [Українська інструкція](GUIDE.uk.md)
- [English quick start](README.en.md)

This repository contains the utility source code and synthetic tests. It does not contain game PAK files, extracted game text, user projects, model weights, local paths, or personal settings.

## Compatibility

Crysis Remastered PAK/XML handling was checked against local game files. Crysis 2 and Crysis 3 Remastered profiles are available, but their PAKs have not been verified. The tool currently reads ZIP-compatible PAK archives containing SpreadsheetML XML; encrypted archives and binary CryXmlB are not supported.

## Run

On Linux, install Python 3.10+ and run `./install-linux.sh`, then `./launch.sh`; internet is needed for the initial PySide6 setup. On Windows, use `Start CryLocal.cmd`; if using the source checkout, follow the PowerShell setup step in [GUIDE.uk.md](GUIDE.uk.md). The TranslateGemma model is downloaded on demand from its configured upstream source and runs locally after download.

The project does not currently declare a license for CryLocal. Third-party components retain their own licenses.
