# CryLocal — Windows quick start

1. Get the Windows portable package from the repository Releases page when one is available, or install the verified Python/Qt runtime using `windows/setup.ps1`.
2. Run `Start CryLocal.cmd`.
3. Select English or Ukrainian for the interface. Choose the game profile and create a project from that game's language PAK.
4. Select a target language. Translate manually with Copy source / Paste translation, or download TranslateGemma 4B and use Translate row / Translate batch.
5. Save the project and export/import CSV when useful. Validate the text and build a new PAK outside the game folder. Back up the original before installing any built package.

The model is not included in the source checkout. It is downloaded on demand and runs locally. Crysis 2/3 PAK compatibility and in-game display have not been verified. See [GUIDE.uk.md](GUIDE.uk.md) for the full workflow.
